import asyncio
import json
import time

import pytest

from app.main import build_providers
from app.modules.egress import EgressConfig, EgressEngine
from app.opslog import OpsLog
from app.services.ops import OpRegistry
from app.services.status import StatusService, event_stream

TARGET02 = "host01-kvm/target-02"
FW2 = "host01-kvm/gateway-02"


async def make_service(db, config, transports, registry=None) -> StatusService:
    ops = OpsLog(db)
    await ops.init()
    egress = EgressEngine(
        db, EgressConfig.model_validate(config.modules["egress"]), transports, ops
    )
    await egress.init()
    providers = build_providers(config, transports)
    return StatusService(config, providers, transports, registry, [egress])


def resource(snap, group, rid):
    return next(r for r in snap["groups"][group]["resources"] if r["id"] == rid)


async def test_snapshot_aggregates_all_groups(db, config, transports):
    service = await make_service(db, config, transports)
    snap = await service.snapshot()
    assert snap["hosts"]["server01"]["reachable"]
    assert snap["hosts"]["server01"]["stats"]["mem_total"] == 65170083840
    assert snap["hosts"]["relay-fw"]["stats"] is None  # stats: null host
    assert snap["providers"]["host01-kvm"]["reachable"] is True
    assert resource(snap, "lab-02", "gateway-02")["state"] == "running"
    assert resource(snap, "lab-02", "target-01")["state"] == "stopped"
    # capability set rides along for the UI to render buttons from
    assert "start" in resource(snap, "lab-02", "target-02")["capabilities"]
    assert snap["modules"]["egress"]["lab-02"]["internet"] is False


async def test_auto_group_resources_sorted_by_id(db, config, transports):
    # auto: groups track a provider's listing, whose order can shift between
    # polls; the snapshot sorts them by id so churn never reshuffles the rest.
    from app.config import Config

    data = config.model_dump()
    data["groups"]["auto-kvm"] = {"label": "Auto KVM", "auto": "host01-kvm"}
    service = await make_service(db, Config.model_validate(data), transports)
    snap = await service.snapshot()
    ids = [r["id"] for r in snap["groups"]["auto-kvm"]["resources"]]
    # virsh fixture lists gateway-02, target-01, target-02 — sorted differs
    assert ids == ["gateway-02", "target-01", "target-02"]


async def test_running_gateway_with_exit_node_shows_internet(db, config, transports):
    from tests.conftest import EXIT_NODE_ACTIVE

    transports["relay-fw"].respond("tailscale status --json", stdout=EXIT_NODE_ACTIVE)
    service = await make_service(db, config, transports)
    snap = await service.snapshot()
    assert snap["modules"]["egress"]["lab-02"]["internet"] is True


async def test_unreachable_host_degrades_gracefully(db, config, transports):
    transports["server01"].respond("virsh list --all", stderr="ssh: timeout", exit_code=255)
    transports["server01"].respond(
        "cat /proc/loadavg && free -b && df -kP /", stderr="ssh: timeout", exit_code=255
    )
    service = await make_service(db, config, transports)
    snap = await service.snapshot()
    assert snap["hosts"]["server01"]["reachable"] is False
    assert "timeout" in snap["hosts"]["server01"]["error"]
    assert snap["providers"]["host01-kvm"]["reachable"] is False
    assert resource(snap, "lab-02", "gateway-02")["state"] == "unknown"
    # parallels side unaffected
    assert resource(snap, "lab-01", "pentest-vm")["state"] == "stopped"


async def test_snapshot_is_cached(db, config, transports):
    service = await make_service(db, config, transports)
    await service.snapshot()
    calls_after_first = sum(len(t.calls) for t in transports.values())
    await service.snapshot()
    assert sum(len(t.calls) for t in transports.values()) == calls_after_first


async def test_inflight_overlay_is_applied_without_refetch(db, config, transports):
    reg = OpRegistry()
    service = await make_service(db, config, transports, reg)
    await service.snapshot()
    calls_after_first = sum(len(t.calls) for t in transports.values())
    reg.inflight[FW2] = "stopping"
    reg.errors[TARGET02] = "boom"
    snap = await service.snapshot()
    assert sum(len(t.calls) for t in transports.values()) == calls_after_first  # no refetch
    assert resource(snap, "lab-02", "gateway-02")["state"] == "stopping"
    assert resource(snap, "lab-02", "target-02")["error"] == "boom"


async def test_stale_snapshot_served_while_refresh_runs(db, config, transports):
    service = await make_service(db, config, transports)
    service._ttl = 0
    first = await service.snapshot()
    assert first["providers"]["host01-kvm"]["reachable"] is True
    transports["server01"].respond("virsh list --all", stderr="ssh: down", exit_code=255)
    stale = await service.snapshot()  # served instantly from old cache
    assert stale["providers"]["host01-kvm"]["reachable"] is True
    await service._refresh_task
    fresh = await service.snapshot()
    assert fresh["providers"]["host01-kvm"]["reachable"] is False


async def test_subscribers_notified_after_invalidate(db, config, transports):
    service = await make_service(db, config, transports)
    await service.snapshot()
    event = service.subscribe()
    service.invalidate()
    await asyncio.wait_for(event.wait(), 2)
    service.unsubscribe(event)


async def test_stale_inflight_marker_is_ignored(db, config, transports):
    reg = OpRegistry()
    service = await make_service(db, config, transports, reg)
    await service.snapshot()
    reg.inflight[TARGET02] = "stopping"
    reg.started[TARGET02] = time.time() - 9999  # wedged task from long ago
    snap = await service.snapshot()
    assert resource(snap, "lab-02", "target-02")["state"] == "stopped"  # truth wins


async def test_event_stream_yields_snapshots_and_stops_on_disconnect(db, config, transports):
    service = await make_service(db, config, transports)
    disconnected = False

    async def is_disconnected() -> bool:
        return disconnected

    stream = event_stream(service, is_disconnected, heartbeat_s=0.01)
    frame = await anext(stream)
    assert frame.startswith("data: ")
    assert "groups" in json.loads(frame[len("data: ") :])
    disconnected = True
    with pytest.raises(StopAsyncIteration):
        await anext(stream)
    assert not service._listeners


async def test_event_stream_pushes_promptly_after_invalidate(db, config, transports):
    service = await make_service(db, config, transports)

    async def is_disconnected() -> bool:
        return False

    stream = event_stream(service, is_disconnected, heartbeat_s=30.0)
    await anext(stream)
    service.invalidate()
    frame = await asyncio.wait_for(anext(stream), timeout=2)
    assert frame.startswith("data: ")
