"""Proxmox provider unit tests — listing, qemu/lxc resolution, UPID task
polling (success/failure/timeout), auth + unreachable-endpoint degradation, and
the path-injection guard. All against FakeHttpClient; nothing opens a socket.
"""

import time

import pytest

from app.httpclient import FakeHttpClient, HttpError
from app.providers.base import Capability, ResourceKind, ResourceState
from app.providers.proxmox import API, ProxmoxProvider, auth_header
from tests.proxmox_fakes import TASK_PATH, healthy_client, healthy_provider


def test_auth_header_format():
    assert auth_header("dubdeck@pam!tok", "secret") == {
        "Authorization": "PVEAPIToken=dubdeck@pam!tok=secret"
    }


async def test_list_spans_qemu_and_lxc():
    resources = {r.id: r for r in await healthy_provider().list_resources()}
    assert set(resources) == {"pve/100", "pve/101"}
    assert resources["pve/100"].kind == ResourceKind.VM
    assert resources["pve/100"].name == "webvm"
    assert resources["pve/100"].state == ResourceState.RUNNING
    assert resources["pve/101"].state == ResourceState.STOPPED  # lxc, stopped


async def test_start_polls_task_to_completion():
    client = healthy_client()
    provider = ProxmoxProvider("pve", client, poll_interval=0.0)
    await provider.start("pve/100")
    # the POST fired and the UPID was polled
    assert ("POST", f"{API}/nodes/pve/qemu/100/status/start", None) in client.calls
    assert any(c[1] == TASK_PATH for c in client.calls)


async def test_graceful_vs_force_stop_hit_distinct_endpoints():
    client = healthy_client()
    provider = ProxmoxProvider("pve", client, poll_interval=0.0)
    await provider.stop("pve/100")
    await provider.force_stop("pve/100")
    paths = [c[1] for c in client.calls if c[0] == "POST"]
    assert f"{API}/nodes/pve/qemu/100/status/shutdown" in paths  # graceful
    assert f"{API}/nodes/pve/qemu/100/status/stop" in paths  # force


async def test_resolves_lxc_endpoint_family():
    client = healthy_client()
    provider = ProxmoxProvider("pve", client, poll_interval=0.0)
    await provider.stop("pve/101")
    assert ("POST", f"{API}/nodes/pve/lxc/101/status/shutdown", None) in client.calls


async def test_task_failure_raises():
    client = healthy_client()
    client.respond("GET", TASK_PATH, body={"data": {"status": "stopped", "exitstatus": "boom"}})
    provider = ProxmoxProvider("pve", client, poll_interval=0.0)
    with pytest.raises(RuntimeError, match="task failed: boom"):
        await provider.start("pve/100")


async def test_task_timeout_raises():
    client = healthy_client()
    # task never finishes — always "running"
    client.respond("GET", TASK_PATH, body={"data": {"status": "running"}})
    provider = ProxmoxProvider("pve", client, poll_interval=0.0)
    with pytest.raises(RuntimeError, match="did not finish"):
        await provider.start("pve/100", timeout=0.0)


async def test_auth_failure_surfaces_as_error():
    client = healthy_client()
    client.respond("GET", f"{API}/nodes", status=401, body={"message": "auth failed"})
    provider = ProxmoxProvider("pve", client, poll_interval=0.0)
    with pytest.raises(RuntimeError, match="auth failed"):
        await provider.list_resources()


async def test_unreachable_endpoint_raises_httperror():
    client = healthy_client()
    client.fail("GET", f"{API}/nodes", "connection refused")
    provider = ProxmoxProvider("pve", client, poll_interval=0.0)
    with pytest.raises(HttpError):
        await provider.list_resources()


async def test_snapshot_list_drops_synthetic_current():
    snaps = await healthy_provider().snapshot_list("pve/100")
    names = [s.name for s in snaps]
    assert names == ["pre-update"]  # the "current" pseudo-entry is filtered out


async def test_snapshot_create_validates_name():
    provider = healthy_provider()
    with pytest.raises(ValueError, match="invalid snapshot name"):
        await provider.snapshot_create("pve/100", "bad name; rm -rf /")


async def test_disk_stats_reads_maxdisk():
    disks = await healthy_provider().disk_stats()
    assert disks == {"pve/100": 34359738368, "pve/101": 8589934592}


async def test_node_stats_shape():
    stats = await healthy_provider().node_stats()
    assert stats["pve"]["load_1m"] == 0.15
    assert stats["pve"]["mem_total"] == 67430322176


def test_capabilities_no_restart_no_logs():
    provider = healthy_provider()
    assert Capability.RESTART not in provider.capabilities
    assert Capability.LOGS not in provider.capabilities
    assert Capability.DISK_STATS in provider.capabilities


@pytest.mark.parametrize("rid", ["pve/abc", "p ve/100", "pve/1; rm", "../etc/100"])
async def test_rejects_nonnumeric_or_hostile_rid(rid):
    with pytest.raises(ValueError):
        await healthy_provider().start(rid)


async def test_node_stats_join_status_hosts():
    """6.3: a proxmox node surfaces in the status `hosts` section, namespaced
    provider/node, with the same stats shape as an SSH host."""
    from app.config import Config, Group, Provider
    from app.services.status import StatusService

    config = Config(
        hosts={},
        providers=[
            Provider(
                id="pve",
                type="proxmox",
                url="https://pve.example:8006",
                token_id="d@pam!t",
                token_secret_env="X",
            )
        ],
        groups={"vms": Group(label="VMs", members=["pve/100"])},
    )
    svc = StatusService(config, {"pve": healthy_provider()}, {}, None, [])
    snap = await svc.snapshot()
    assert "pve/pve" in snap["hosts"]
    assert snap["hosts"]["pve/pve"]["stats"]["mem_total"] == 67430322176


async def test_dead_endpoint_does_not_wedge_status():
    """Gate: status aggregation with one dead proxmox endpoint stays responsive
    — the healthy provider still lists and the dead one degrades to unreachable,
    all well under the would-be hang of a real network timeout."""
    from app.config import Config, Group, Host, Provider
    from app.providers.docker import DockerProvider
    from app.services.status import StatusService
    from app.transports import FakeTransport

    class SlowFailClient(FakeHttpClient):
        async def request(self, method, path, **kw):  # noqa: ANN001
            import asyncio

            await asyncio.sleep(0.02)  # a real timeout, compressed
            raise HttpError("timed out")

    dt = FakeTransport(label="edge")
    dt.respond("docker ps -a --format '{{json .}}'", stdout='{"Names":"web","State":"running"}')
    docker = DockerProvider("edge-docker", dt)
    proxmox = ProxmoxProvider("pve", SlowFailClient(), poll_interval=0.0)

    config = Config(
        hosts={"edge": Host(transport="local", stats=None)},
        providers=[
            Provider(id="edge-docker", type="docker", host="edge"),
            Provider(
                id="pve",
                type="proxmox",
                url="https://pve.example:8006",
                token_id="d@pam!t",
                token_secret_env="X",
            ),
        ],
        groups={
            "containers": Group(label="Containers", auto="edge-docker"),
            "vms": Group(label="VMs", members=["pve/100"]),
        },
    )
    svc = StatusService(config, {"edge-docker": docker, "pve": proxmox}, {"edge": dt}, None, [])
    started = time.monotonic()
    snap = await svc.snapshot()
    elapsed = time.monotonic() - started

    assert elapsed < 1.0, f"dead endpoint wedged the poll ({elapsed:.2f}s)"
    assert snap["providers"]["edge-docker"]["reachable"] is True
    assert snap["providers"]["pve"]["reachable"] is False
    assert snap["providers"]["pve"]["error"]
