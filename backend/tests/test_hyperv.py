"""Phase 7 — Hyper-V provider.

Parser edge cases over the ConvertTo-Json shapes (array / bare object / empty /
BOM-prefixed), the PowerShell-quoting injection posture (the loud divergence from
shlex), exact command strings, and the VM-kind capability set. The generic
capability/listing contract lives in tests/contract; this file covers what's
specific to driving Hyper-V over SSH-to-PowerShell.
"""

from pathlib import Path

import pytest

from app.providers import Capability, ResourceKind, ResourceState, Unsupported
from app.providers import hyperv as hv
from app.providers.registry import build_command_provider
from app.transports import FakeTransport

FIXTURES = Path(__file__).parent / "fixtures"


# --- list parser: the three ConvertTo-Json shapes + BOM ---


def test_parse_list_array_states():
    states = hv.parse_list((FIXTURES / "hyperv_get_vm.json").read_text())
    assert states == {
        "DC01": ResourceState.RUNNING,
        "WEB01": ResourceState.STOPPED,  # "Off"
        "DB01": ResourceState.SUSPENDED,  # "Saved" (Save-VM)
        "APP01": ResourceState.PAUSED,
    }


def test_parse_list_single_object_is_not_an_array():
    # ConvertTo-Json emits a BARE OBJECT (not a 1-element array) for a single VM.
    line = '{"Name":"solo","State":"Running"}'
    assert hv.parse_list(line) == {"solo": ResourceState.RUNNING}


def test_parse_list_empty_output():
    # Zero VMs → ConvertTo-Json emits nothing.
    assert hv.parse_list("") == {}
    assert hv.parse_list("   \n  ") == {}


def test_parse_list_strips_utf8_bom():
    # A non-UTF-8 SSH shell can prepend a BOM; it must not break json.loads.
    line = "\ufeff" + '{"Name":"x","State":"Off"}'
    assert hv.parse_list(line) == {"x": ResourceState.STOPPED}


@pytest.mark.parametrize("state", ["Starting", "Stopping", "Saving", "Resuming", "Reset", ""])
def test_parse_list_transient_states_are_unknown(state):
    line = f'{{"Name":"v","State":"{state}"}}'
    assert hv.parse_list(line) == {"v": ResourceState.UNKNOWN}


def test_parse_list_state_is_case_insensitive():
    # We lowercase before mapping, so casing differences across PS versions are fine.
    assert hv.parse_list('{"Name":"v","State":"RUNNING"}') == {"v": ResourceState.RUNNING}


# --- snapshot parser ---


def test_parse_snapshots_sorts_oldest_first():
    out = (
        '[{"Name":"after","Created":"2026-02-01T10:00:00.000+00:00"},'
        '{"Name":"before","Created":"2026-01-01T10:00:00.000+00:00"}]'
    )
    snaps = hv.parse_snapshots(out)
    assert [s.name for s in snaps] == ["before", "after"]


def test_parse_snapshots_empty():
    assert hv.parse_snapshots("") == []


# --- command builders: exact strings ---


def test_command_strings():
    assert hv.start_command("DC01") == "Start-VM -Name 'DC01'"
    # graceful guest shutdown; -Force only suppresses the confirm prompt
    assert hv.stop_command("DC01") == "Stop-VM -Name 'DC01' -Force"
    # hard power-off via -TurnOff
    assert hv.force_stop_command("DC01") == "Stop-VM -Name 'DC01' -TurnOff -Force"
    assert hv.suspend_command("DC01") == "Save-VM -Name 'DC01'"
    assert hv.snapshot_create_command("DC01", "snap1") == (
        "Checkpoint-VM -Name 'DC01' -SnapshotName 'snap1'"
    )


def test_list_command_forces_state_to_string():
    # The calculated property pins State to a string regardless of PS version.
    assert "$_.State.ToString()" in hv.GET_VM
    assert hv.GET_VM.endswith("ConvertTo-Json -Compress")


# --- PowerShell quoting: the loud divergence from shlex ---


def test_ps_quote_doubles_single_quotes():
    # The ONLY metacharacter inside PS single quotes is the quote itself.
    assert hv.ps_quote("it's") == "'it''s'"
    assert hv.ps_quote("plain") == "'plain'"


@pytest.mark.parametrize(
    "evil",
    [
        "vm'; Remove-VM -Name *",  # closing-quote breakout attempt
        "$(Get-Process)",  # subexpression — literal inside single quotes
        "`n; rm",  # backtick escape — literal inside single quotes
        "a & b | c",  # call/pipe operators — literal inside single quotes
        "vm name with spaces",
    ],
)
def test_ps_quote_neutralises_injection(evil):
    quoted = hv.ps_quote(evil)
    # wrapped in single quotes, with every embedded quote doubled — nothing else
    assert quoted.startswith("'") and quoted.endswith("'")
    inner = quoted[1:-1]
    assert "'" not in inner.replace("''", "")  # no un-doubled single quote escapes


def test_builders_only_emit_quoted_hostile_name():
    evil = "vm'; Stop-VM -Name *"
    for builder in (
        hv.start_command,
        hv.stop_command,
        hv.force_stop_command,
        hv.suspend_command,
        hv.snapshot_list_command,
    ):
        assert hv.ps_quote(evil) in builder(evil)


# --- provider: VM kind + capability set ---


async def test_provider_kinds_and_caps():
    t = FakeTransport(label="hyperv")
    t.respond(hv.GET_VM, stdout=(FIXTURES / "hyperv_get_vm.json").read_text())
    provider = build_command_provider("hyperv", "win-hv", t)
    resources = await provider.list_resources()
    assert {r.id for r in resources} == {"DC01", "WEB01", "DB01", "APP01"}
    assert all(r.kind is ResourceKind.VM for r in resources)
    assert provider.supports(Capability.SUSPEND)
    assert provider.supports(Capability.SNAPSHOT_CREATE)
    assert provider.supports(Capability.FORCE_STOP)
    # Hyper-V has no logs/disk-stats path in v1
    assert not provider.supports(Capability.LOGS)
    assert not provider.supports(Capability.DISK_STATS)


async def test_logs_unsupported_bubbles():
    provider = build_command_provider("hyperv", "win-hv", FakeTransport())
    with pytest.raises(Unsupported):
        await provider.logs("DC01")


async def test_snapshot_list_parses_from_checkpoint_output():
    t = FakeTransport(label="hyperv")
    t.respond(
        hv.snapshot_list_command("DC01"),
        stdout='{"Name":"pre-patch","Created":"2026-01-15T09:00:00.000+00:00"}',
    )
    provider = build_command_provider("hyperv", "win-hv", t)
    snaps = await provider.snapshot_list("DC01")
    assert [s.name for s in snaps] == ["pre-patch"]


async def test_stop_failure_surfaces():
    t = FakeTransport(label="hyperv")
    t.respond(hv.stop_command("DC01"), stderr="VM not found", exit_code=1)
    provider = build_command_provider("hyperv", "win-hv", t)
    with pytest.raises(RuntimeError, match="VM not found"):
        await provider.stop("DC01")
