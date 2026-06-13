from pathlib import Path

import pytest

from app.providers import Capability, ResourceKind, ResourceState, Unsupported
from app.providers import libvirt as lv
from app.providers import parallels as pl
from app.providers.registry import build_command_provider
from app.transports import FakeTransport

FIXTURES = Path(__file__).parent / "fixtures"


# --- pure parsers (ported verbatim; same expectations as the old hypervisor tests) ---


def test_parallels_parse_list():
    states = pl.parse_list((FIXTURES / "prlctl_list.txt").read_text())
    assert states["gateway-01"] == ResourceState.STOPPED
    assert states["Safe Browsing v2"] == ResourceState.SUSPENDED  # spaces in name survive
    assert len(states) == 13


def test_libvirt_parse_list():
    states = lv.parse_list((FIXTURES / "virsh_list.txt").read_text())
    assert states == {
        "gateway-02": ResourceState.RUNNING,
        "target-01": ResourceState.STOPPED,
        "target-02": ResourceState.STOPPED,
    }


def test_unknown_state_does_not_crash():
    assert pl.parse_list("NAME STATUS\nweird exploding\n")["weird"] == ResourceState.UNKNOWN


def test_parallels_snapshots_and_disks():
    snaps = pl.parse_snapshots((FIXTURES / "prlctl_snapshot_list.json").read_text())
    assert [s.name for s in snaps] == ["Clean", "updates"]
    assert snaps[1].current is True
    assert pl.parse_snapshots("") == []
    disks = pl.parse_disks((FIXTURES / "dubdeck_vm_disks.txt").read_text())
    assert all(v > 0 for v in disks.values())


def test_libvirt_snapshots_and_disks():
    snaps = lv.parse_snapshots((FIXTURES / "virsh_snapshot_list.txt").read_text())
    assert len(snaps) == 1 and snaps[0].name == "baseline"
    disks = lv.parse_disks((FIXTURES / "virsh_domstats_block.txt").read_text())
    assert all(v >= 0 for v in disks.values())


def test_command_builders_quote_names():
    assert pl.start_command("Safe Browsing v2") == "prlctl start 'Safe Browsing v2'"
    assert pl.force_stop_command("pentest-vm") == "prlctl stop pentest-vm --kill"
    assert lv.stop_command("target-02") == "virsh shutdown target-02"
    assert lv.force_stop_command("target-02") == "virsh destroy target-02"
    assert lv.suspend_command("target-02") == "virsh managedsave target-02"


# --- provider classes over a FakeTransport ---


async def test_parallels_provider_lists_and_acts():
    t = FakeTransport()
    t.respond(pl.PRLCTL_LIST, stdout=(FIXTURES / "prlctl_list.txt").read_text())
    t.respond(pl.start_command("pentest-vm"))
    provider = build_command_provider("parallels", "host02-parallels", t)

    resources = await provider.list_resources()
    assert any(r.name == "gateway-01" and r.kind == ResourceKind.VM for r in resources)
    await provider.start("pentest-vm")  # no raise = success
    assert pl.start_command("pentest-vm") in t.calls


async def test_libvirt_provider_start_raises_on_failure():
    t = FakeTransport()
    t.respond(lv.start_command("ghost"), stderr="domain not found", exit_code=1)
    provider = build_command_provider("libvirt", "host01-kvm", t)
    with pytest.raises(RuntimeError, match="domain not found"):
        await provider.start("ghost")


async def test_provider_declares_expected_capabilities():
    provider = build_command_provider("libvirt", "host01-kvm", FakeTransport())
    for cap in (Capability.START, Capability.SNAPSHOT_CREATE, Capability.DISK_STATS):
        assert provider.supports(cap)
    assert not provider.supports(Capability.LOGS)
    assert not provider.supports(Capability.RESTART)


async def test_undeclared_capability_raises_unsupported():
    provider = build_command_provider("parallels", "host02-parallels", FakeTransport())
    with pytest.raises(Unsupported):
        await provider.logs("anything")
    with pytest.raises(Unsupported):
        await provider.restart("anything")


def test_registry_rejects_unknown_type():
    with pytest.raises(ValueError, match="unknown provider type"):
        build_command_provider("vmware", "x", FakeTransport())
