import pytest

from app.main import build_providers
from app.opslog import OpsLog
from app.services.ops import OpRegistry, ResourceOps
from app.transports import CommandResult
from tests.conftest import FIXTURES

# refs
FW1 = "host02-parallels/gateway-01"
KALI = "host02-parallels/pentest-vm"
DEV1 = "host02-parallels/lab-01-dev-01"
LAB03_DEV = "host02-parallels/lab-03-dev-01"
FW2 = "host01-kvm/gateway-02"
TARGET01 = "host01-kvm/target-01"
TARGET02 = "host01-kvm/target-02"

STOPPED = (FIXTURES / "prlctl_list.txt").read_text()
RUNNING_FW1 = STOPPED.replace(
    "gateway-01                       stopped", "gateway-01                       running"
)
TARGET02_RUNNING = (
    " Id   Name          State\n------------------------------\n 2    target-02       running\n"
)
ALL_RUNNING_VIRSH = (
    " Id   Name          State\n"
    "------------------------------\n"
    " 1    gateway-02    running\n"
    " 2    target-01     running\n"
    " 3    target-02     running\n"
)


async def make_ops(db, config, transports) -> ResourceOps:
    ops = OpsLog(db)
    await ops.init()
    return ResourceOps(config, build_providers(config, transports), ops, OpRegistry())


def ws(transports):
    return transports["workstation"]


def srv(transports):
    return transports["server01"]


async def test_member_start_brings_start_first_up_first(db, config, transports):
    # list: stopped (initial check), then running (ready probe sees fw up)
    ws(transports).respond_seq(
        "prlctl list --all -o name,status",
        [CommandResult(STOPPED), CommandResult(RUNNING_FW1)],
    )
    ws(transports).respond("prlctl list --all -o name,status", stdout=RUNNING_FW1)
    ws(transports).respond("prlctl start gateway-01")
    ws(transports).respond("prlctl start pentest-vm")
    ops = await make_ops(db, config, transports)
    ops.READY_POLL = 0.01
    result = ops.start(KALI)
    assert result["status"] == "starting"  # returns immediately
    await ops.drain()
    starts = [c for c in ws(transports).calls if c.startswith("prlctl start")]
    assert starts == ["prlctl start gateway-01", "prlctl start pentest-vm"]


async def test_member_start_skips_running_start_first(db, config, transports):
    ws(transports).respond("prlctl list --all -o name,status", stdout=RUNNING_FW1)
    ws(transports).respond("prlctl start pentest-vm")
    ops = await make_ops(db, config, transports)
    ops.READY_POLL = 0.01
    ops.start(KALI)
    await ops.drain()
    starts = [c for c in ws(transports).calls if c.startswith("prlctl start")]
    assert starts == ["prlctl start pentest-vm"]


async def test_kvm_member_start_with_running_infra(db, config, transports):
    # gateway-02 already running in the virsh fixture → no infra start, probe passes
    srv(transports).respond("virsh start target-01")
    ops = await make_ops(db, config, transports)
    result = ops.start(TARGET01)
    assert result == {"ref": TARGET01, "status": "starting", "group": "lab-02"}
    await ops.drain()
    assert "virsh start target-01" in srv(transports).calls
    assert "virsh start gateway-02" not in srv(transports).calls


async def test_kvm_stop_graceful_when_guest_powers_off(db, config, transports):
    # fixture shows target-02 already 'shut off' — graceful suffices, no force
    srv(transports).respond("virsh shutdown target-02")
    ops = await make_ops(db, config, transports)
    result = ops.stop(TARGET02)
    assert result["status"] == "stopping"
    assert ops._reg.inflight[TARGET02] == "stopping"
    await ops.drain()
    assert "virsh shutdown target-02" in srv(transports).calls
    assert "virsh destroy target-02" not in srv(transports).calls
    assert TARGET02 not in ops._reg.inflight


async def test_kvm_stop_forces_after_grace_if_guest_ignores_acpi(db, config, transports):
    srv(transports).respond("virsh list --all", stdout=TARGET02_RUNNING)  # never powers off
    srv(transports).respond("virsh shutdown target-02")
    srv(transports).respond("virsh destroy target-02")
    ops = await make_ops(db, config, transports)
    ops.STOP_GRACE, ops.STOP_POLL = 0.05, 0.02
    ops.stop(TARGET02)
    await ops.drain()
    assert "virsh shutdown target-02" in srv(transports).calls
    assert "virsh destroy target-02" in srv(transports).calls
    assert TARGET02 not in ops._reg.inflight


async def test_kvm_force_failure_records_error(db, config, transports):
    srv(transports).respond("virsh list --all", stdout=TARGET02_RUNNING)
    srv(transports).respond("virsh shutdown target-02")
    srv(transports).respond("virsh destroy target-02", stderr="destroy failed", exit_code=1)
    ops = await make_ops(db, config, transports)
    ops.STOP_GRACE, ops.STOP_POLL = 0.05, 0.02
    ops.stop(TARGET02)
    await ops.drain()
    assert "destroy failed" in ops._reg.errors[TARGET02]


async def test_parallels_stop_failure_records_error(db, config, transports):
    # lab-03 has no snapshot guardrail — plain stop path
    ws(transports).respond("prlctl stop lab-03-dev-01", stderr="prlctl boom", exit_code=1)
    ops = await make_ops(db, config, transports)
    ops.stop(LAB03_DEV)
    await ops.drain()
    assert "boom" in ops._reg.errors[LAB03_DEV]


async def test_double_stop_is_ignored_while_inflight(db, config, transports):
    srv(transports).respond("virsh shutdown target-02")
    ops = await make_ops(db, config, transports)
    ops.stop(TARGET02)
    second = ops.stop(TARGET02)
    assert second["status"] == "stopping"
    await ops.drain()
    assert [c for c in srv(transports).calls if c == "virsh shutdown target-02"] == [
        "virsh shutdown target-02"
    ]


async def test_unknown_ref_rejected(db, config, transports):
    ops = await make_ops(db, config, transports)
    with pytest.raises(KeyError):
        ops.start("host01-kvm/NixOS")


async def test_member_start_waits_for_readiness(db, config, transports):
    # fw boots slowly: list shows it stopped twice, then running
    ws(transports).respond_seq(
        "prlctl list --all -o name,status",
        [CommandResult(STOPPED), CommandResult(STOPPED), CommandResult(RUNNING_FW1)],
    )
    ws(transports).respond("prlctl list --all -o name,status", stdout=RUNNING_FW1)
    ws(transports).respond("prlctl start gateway-01")
    ws(transports).respond("prlctl start pentest-vm")
    ops = await make_ops(db, config, transports)
    ops.READY_POLL = 0.01
    ops.start(KALI)
    await ops.drain()
    cmds = ws(transports).calls
    # member only starts after the fw reports running
    assert cmds.index("prlctl start pentest-vm") > cmds.index("prlctl start gateway-01")
    assert KALI not in ops._reg.errors


async def test_start_first_never_ready_aborts_member(db, config, transports):
    ws(transports).respond("prlctl list --all -o name,status", stdout=STOPPED)  # fw never runs
    ws(transports).respond("prlctl start gateway-01")
    ops = await make_ops(db, config, transports)
    ops.READY_TIMEOUT, ops.READY_POLL = 0.03, 0.01
    ops.start(KALI)
    await ops.drain()
    assert "not RUNNING" in ops._reg.errors[KALI]
    assert "prlctl start pentest-vm" not in ws(transports).calls


async def test_concurrent_member_starts_share_one_infra_start(db, config, transports):
    ws(transports).respond_seq(
        "prlctl list --all -o name,status",
        [CommandResult(STOPPED), CommandResult(RUNNING_FW1)],
    )
    ws(transports).respond("prlctl list --all -o name,status", stdout=RUNNING_FW1)
    ws(transports).respond("prlctl start gateway-01")
    ws(transports).respond("prlctl start pentest-vm")
    ws(transports).respond("prlctl start lab-01-dev-01")
    ops = await make_ops(db, config, transports)
    ops.READY_POLL = 0.01
    ops.start(KALI)
    ops.start(DEV1)
    await ops.drain()
    fw_starts = [c for c in ws(transports).calls if c == "prlctl start gateway-01"]
    assert len(fw_starts) == 1  # the group lock dedups racing infra starts
    assert not ops._reg.errors


async def test_start_group_starts_only_stopped_members(db, config, transports):
    # lab-02: fw running, both members shut off
    srv(transports).respond("virsh start target-01")
    srv(transports).respond("virsh start target-02")
    ops = await make_ops(db, config, transports)
    result = await ops.start_group("lab-02")
    assert set(result["starting"]) == {TARGET01, TARGET02}
    await ops.drain()
    assert "virsh start target-01" in srv(transports).calls
    assert "virsh start gateway-02" not in srv(transports).calls  # already running


async def test_stop_group_stops_members_before_infra(db, config, transports):
    srv(transports).respond("virsh list --all", stdout=ALL_RUNNING_VIRSH)
    for vm in ("target-01", "target-02", "gateway-02"):
        srv(transports).respond(f"virsh shutdown {vm}")
        srv(transports).respond(f"virsh destroy {vm}")
    ops = await make_ops(db, config, transports)
    ops.STOP_GRACE, ops.STOP_POLL = 0.04, 0.02
    result = ops.stop_group("lab-02")
    assert result["status"] == "stopping"
    await ops.drain()
    cmds = srv(transports).calls
    gw_stop = cmds.index("virsh shutdown gateway-02")
    assert gw_stop > cmds.index("virsh shutdown target-01")
    assert gw_stop > cmds.index("virsh shutdown target-02")


async def test_stop_group_keeps_infra_up_when_member_fails(db, config, transports):
    srv(transports).respond("virsh list --all", stdout=ALL_RUNNING_VIRSH)
    for vm in ("target-01", "target-02"):
        srv(transports).respond(f"virsh shutdown {vm}")
        srv(transports).respond(f"virsh destroy {vm}")
    srv(transports).respond("virsh destroy target-01", stderr="destroy failed", exit_code=1)
    ops = await make_ops(db, config, transports)
    ops.STOP_GRACE, ops.STOP_POLL = 0.04, 0.02
    ops.stop_group("lab-02")
    await ops.drain()
    assert "virsh shutdown gateway-02" not in srv(transports).calls
    assert "target-01" in ops._reg.errors["group:lab-02"]


async def test_dismiss_error(db, config, transports):
    ops = await make_ops(db, config, transports)
    ops._reg.errors[TARGET02] = "boom"
    assert ops.dismiss_error(TARGET02) is True
    assert ops.dismiss_error(TARGET02) is False


async def test_suspend_parallels(db, config, transports):
    ws(transports).respond("prlctl suspend pentest-vm")
    ops = await make_ops(db, config, transports)
    result = ops.suspend(KALI)
    assert result["status"] == "suspending"
    await ops.drain()
    assert "prlctl suspend pentest-vm" in ws(transports).calls
    assert KALI not in ops._reg.errors


async def test_suspend_kvm_uses_managedsave(db, config, transports):
    srv(transports).respond("virsh managedsave target-02")
    ops = await make_ops(db, config, transports)
    ops.suspend(TARGET02)
    await ops.drain()
    assert "virsh managedsave target-02" in srv(transports).calls


async def test_snapshot_list(db, config, transports):
    ws(transports).respond(
        "prlctl snapshot-list pentest-vm -j",
        stdout=(FIXTURES / "prlctl_snapshot_list.json").read_text(),
    )
    ops = await make_ops(db, config, transports)
    snaps = await ops.snapshots(KALI)
    assert [s.name for s in snaps] == ["Clean", "updates"]


async def test_snapshot_create_runs_in_background(db, config, transports):
    ws(transports).respond("prlctl snapshot pentest-vm -n before-exploit")
    ops = await make_ops(db, config, transports)
    result = ops.create_snapshot(KALI, "before-exploit")
    assert result["status"] == "snapshotting"
    await ops.drain()
    assert "prlctl snapshot pentest-vm -n before-exploit" in ws(transports).calls


async def test_guardrail_stop_snapshots_first(db, config, transports, monkeypatch):
    import time as time_module

    monkeypatch.setattr(time_module, "strftime", lambda fmt: "20260611-000000")
    ws(transports).respond("prlctl snapshot pentest-vm -n dubdeck-prestop-20260611-000000")
    ws(transports).respond("prlctl stop pentest-vm")
    ops = await make_ops(db, config, transports)
    ops.stop(KALI)
    await ops.drain()
    cmds = ws(transports).calls
    snap_at = cmds.index("prlctl snapshot pentest-vm -n dubdeck-prestop-20260611-000000")
    assert snap_at < cmds.index("prlctl stop pentest-vm")
    assert KALI not in ops._reg.errors


async def test_guardrail_stop_aborts_when_snapshot_fails(db, config, transports, monkeypatch):
    import time as time_module

    monkeypatch.setattr(time_module, "strftime", lambda fmt: "20260611-000000")
    ws(transports).respond(
        "prlctl snapshot pentest-vm -n dubdeck-prestop-20260611-000000",
        stderr="disk full",
        exit_code=1,
    )
    ops = await make_ops(db, config, transports)
    ops.stop(KALI)
    await ops.drain()
    assert "prlctl stop pentest-vm" not in ws(transports).calls  # never stopped
    assert "pre-stop snapshot failed" in ops._reg.errors[KALI]


async def test_start_first_stop_skips_guardrail_snapshot(db, config, transports):
    ws(transports).respond("prlctl stop gateway-01")
    ops = await make_ops(db, config, transports)
    ops.stop(FW1)  # fw is start_first / stateless
    await ops.drain()
    assert not any("snapshot" in c for c in ws(transports).calls)


async def test_non_guardrail_group_stops_without_snapshot(db, config, transports):
    srv(transports).respond("virsh shutdown target-02")
    ops = await make_ops(db, config, transports)
    ops.stop(TARGET02)  # lab-02 has no snapshot_before_stop
    await ops.drain()
    assert not any("snapshot" in c for c in srv(transports).calls)
