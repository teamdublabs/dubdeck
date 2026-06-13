import asyncio
import time

import pytest

from app.modules.egress import MAX_DURATION, EgressConfig, EgressEngine
from app.opslog import OpsLog

LAB01_ENABLE = "prlctl exec gateway-01 tailscale set --exit-node=relay-01"
LAB01_REVOKE = "prlctl exec gateway-01 tailscale set --exit-node="
LAB01_STATUS = "prlctl exec gateway-01 tailscale status --json"


async def make_engine(db, config, transports) -> EgressEngine:
    ops = OpsLog(db)
    await ops.init()
    engine = EgressEngine(
        db, EgressConfig.model_validate(config.modules["egress"]), transports, ops
    )
    await engine.init()
    return engine


def ws(transports):
    return transports["workstation"]


def test_gateway_call_wraps_parallels_in_prlctl_exec(config, transports):
    engine = EgressEngine(
        None, EgressConfig.model_validate(config.modules["egress"]), transports, None
    )
    transport, command = engine.gateway_call("lab-01", "tailscale up")
    assert transport is transports["workstation"]
    assert command == "prlctl exec gateway-01 tailscale up"


def test_gateway_call_direct(config, transports):
    engine = EgressEngine(
        None, EgressConfig.model_validate(config.modules["egress"]), transports, None
    )
    transport, command = engine.gateway_call("lab-02", "tailscale up")
    assert transport is transports["relay-fw"]
    assert command == "tailscale up"


def test_gateway_call_inline_address_uses_own_transport(transports):
    # An inline-address gateway reaches the VM directly without a core host —
    # the engine builds its own SSHTransport, so the VM is never modelled as a host.
    from app.transports import SSHTransport

    cfg = EgressConfig.model_validate(
        {
            "gateways": {
                "kvm-gw": {
                    "address": "192.0.2.50",
                    "user": "labuser",
                    "exit_node": "relay-01",
                    "mode": "on-demand",
                }
            }
        }
    )
    engine = EgressEngine(None, cfg, transports, None)
    transport, command = engine.gateway_call("kvm-gw", "tailscale up")
    assert isinstance(transport, SSHTransport)  # self-built, not a core host transport
    assert command == "tailscale up"  # direct, not prlctl-wrapped
    assert "kvm-gw" not in transports  # needed no core host entry


def test_gateway_reach_is_exactly_one():
    from app.modules.egress.engine import GatewayConfig

    with pytest.raises(ValueError, match="exactly one"):
        GatewayConfig(exit_node="n", host="h", address="192.0.2.50", user="u")
    with pytest.raises(ValueError, match="exactly one"):
        GatewayConfig(exit_node="n")  # none set
    with pytest.raises(ValueError, match="'address' requires 'user'"):
        GatewayConfig(exit_node="n", address="192.0.2.50")


async def test_enable_sets_exit_node_and_records_window(db, config, transports):
    engine = await make_engine(db, config, transports)
    expires_at = await engine.enable("lab-01", 1800)
    assert expires_at == pytest.approx(time.time() + 1800, abs=5)
    assert LAB01_ENABLE in ws(transports).calls
    assert await engine.expires_at("lab-01") == expires_at
    await engine.close()


async def test_revoke_clears_exit_node_and_window(db, config, transports):
    engine = await make_engine(db, config, transports)
    await engine.enable("lab-01", 1800)
    await engine.revoke("lab-01")
    assert LAB01_REVOKE in ws(transports).calls
    assert await engine.expires_at("lab-01") is None
    await engine.close()


async def test_expiry_auto_revokes(db, config, transports):
    engine = await make_engine(db, config, transports)
    await engine.enable("lab-02", 1)
    engine._schedule("lab-02", 0.05)
    await asyncio.sleep(0.2)
    assert "tailscale set --exit-node=" in transports["relay-fw"].calls
    assert await engine.expires_at("lab-02") is None
    await engine.close()


async def test_permanent_egress_rejected(db, config, transports):
    engine = await make_engine(db, config, transports)
    with pytest.raises(ValueError, match="permanent"):
        await engine.enable("lab-03", 600)
    assert not any("gateway-03" in c for c in ws(transports).calls)
    await engine.close()


async def test_reconcile_revokes_orphaned_exit_node(db, config, transports):
    from tests.conftest import EXIT_NODE_ACTIVE

    ws(transports).respond(LAB01_STATUS, stdout=EXIT_NODE_ACTIVE)
    engine = await make_engine(db, config, transports)
    await engine.reconcile()
    assert LAB01_REVOKE in ws(transports).calls
    # lab-03 is permanent — reconcile must never even look at it
    assert not any("gateway-03 tailscale set" in c for c in ws(transports).calls)
    await engine.close()


async def test_reconcile_resumes_live_window(db, config, transports):
    engine = await make_engine(db, config, transports)
    await db.execute(
        "INSERT INTO egress_windows VALUES ('lab-01', 'gateway-01', ?)", (time.time() + 600,)
    )
    await engine.reconcile()
    assert "lab-01" in engine._timers
    assert LAB01_REVOKE not in ws(transports).calls
    await engine.close()


async def test_enable_clamps_duration_to_max(db, config, transports):
    engine = await make_engine(db, config, transports)
    expires_at = await engine.enable("lab-01", MAX_DURATION * 10)
    assert expires_at == pytest.approx(time.time() + MAX_DURATION, abs=5)
    await engine.close()


async def test_extend_pushes_expiry_out(db, config, transports):
    engine = await make_engine(db, config, transports)
    first = await engine.enable("lab-01", 600)
    extended = await engine.extend("lab-01", 900)
    assert extended == pytest.approx(first + 900, abs=1)
    await engine.close()


async def test_extend_caps_total_at_max_duration(db, config, transports):
    engine = await make_engine(db, config, transports)
    await engine.enable("lab-01", 3 * 3600)
    extended = await engine.extend("lab-01", 3 * 3600)
    assert extended == pytest.approx(time.time() + MAX_DURATION, abs=5)
    await engine.close()


async def test_extend_without_active_window_rejected(db, config, transports):
    engine = await make_engine(db, config, transports)
    with pytest.raises(ValueError, match="no active egress window"):
        await engine.extend("lab-01", 900)
    await engine.close()


async def test_failed_revoke_keeps_window_for_retry(db, config, transports):
    engine = await make_engine(db, config, transports)
    await engine.enable("lab-01", 600)
    ws(transports).respond(LAB01_REVOKE, stderr="NAT flapping", exit_code=1)
    with pytest.raises(RuntimeError, match="REVOKE FAILED"):
        await engine.revoke("lab-01")
    assert await engine.expires_at("lab-01") is not None
    await engine.close()


async def test_sweep_retries_overdue_window_until_revoke_lands(db, config, transports):
    engine = await make_engine(db, config, transports)
    await engine.enable("lab-01", 600)
    await engine._db.execute(
        "UPDATE egress_windows SET expires_at = ? WHERE grp = 'lab-01'", (time.time() - 60,)
    )
    ws(transports).respond(LAB01_REVOKE, stderr="NAT flapping", exit_code=1)
    await engine.sweep()
    assert await engine.expires_at("lab-01") is not None
    ws(transports).respond(LAB01_REVOKE)  # gateway path recovers
    await engine.sweep()
    assert await engine.expires_at("lab-01") is None
    await engine.close()


async def test_sweep_leaves_live_windows_alone(db, config, transports):
    engine = await make_engine(db, config, transports)
    await engine.enable("lab-01", 600)
    await engine.sweep()
    assert LAB01_REVOKE not in ws(transports).calls
    assert await engine.expires_at("lab-01") is not None
    await engine.close()


async def test_reconcile_failed_orphan_revoke_queues_retry(db, config, transports):
    from tests.conftest import EXIT_NODE_ACTIVE

    ws(transports).respond(LAB01_STATUS, stdout=EXIT_NODE_ACTIVE)
    ws(transports).respond(LAB01_REVOKE, stderr="NAT flapping", exit_code=1)
    engine = await make_engine(db, config, transports)
    await engine.reconcile()  # revoke fails — must not raise, must queue for sweep
    assert await engine.expires_at("lab-01") is not None
    ws(transports).respond(LAB01_REVOKE)
    await engine.sweep()
    assert await engine.expires_at("lab-01") is None
    await engine.close()


async def test_init_migrates_pre_v2_egress_table(db, config, transports):
    # Simulate a pre-v2 DB: egress_windows keyed by `enclave`, with a row.
    await db.init()
    await db.execute(
        "CREATE TABLE egress_windows (enclave TEXT PRIMARY KEY, gateway_vm TEXT, expires_at REAL)"
    )
    await db.execute("INSERT INTO egress_windows VALUES ('lab-01', 'gateway-01', 1.0)")
    engine = await make_engine(db, config, transports)  # init() migrates
    cols = {c[1] for c in await db.fetchall("PRAGMA table_info(egress_windows)")}
    assert "grp" in cols and "enclave" not in cols
    # v2 queries work now (transient rows dropped — reconcile re-derives truth)
    assert await engine.expires_at("lab-01") is None
    await engine.close()


async def test_reconcile_revokes_expired_window(db, config, transports):
    from tests.conftest import EXIT_NODE_ACTIVE

    ws(transports).respond(LAB01_STATUS, stdout=EXIT_NODE_ACTIVE)
    engine = await make_engine(db, config, transports)
    await db.execute(
        "INSERT INTO egress_windows VALUES ('lab-01', 'gateway-01', ?)", (time.time() - 60,)
    )
    await engine.reconcile()
    assert LAB01_REVOKE in ws(transports).calls
    assert await engine.expires_at("lab-01") is None
    await engine.close()
