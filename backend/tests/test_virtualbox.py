"""VirtualBox provider.

Parser edge cases for the `VBoxManage list -l vms` long-format output (State:
trailing `(since …)`, transient states, missing-State sections), the
`VBoxManage snapshot <vm> list` parser (cross-version Date created: vs Created:
field names, indented Name: lines), exact command strings, the
`--type headless` start posture, and the VM-kind capability set. The generic
capability/listing/injection contract lives in tests/contract; this file
covers what's specific to driving VirtualBox over SSH.
"""

from pathlib import Path

import pytest

from app.providers import Capability, ResourceKind, ResourceState, Unsupported
from app.providers.registry import build_command_provider
from app.providers.virtualbox import (
    VirtualBoxProvider,
    force_stop_command,
    parse_list,
    parse_snapshots,
    snapshot_create_command,
    snapshot_list_command,
    start_command,
    stop_command,
    suspend_command,
)
from app.transports import FakeTransport

FIXTURES = Path(__file__).parent / "fixtures"


# --- list parser: long-format output ---


def test_parse_list_states():
    states = parse_list((FIXTURES / "vbox_list_vms_long.txt").read_text())
    assert states == {
        "debian-test": ResourceState.STOPPED,  # powered off (since …)
        "gateway-04": ResourceState.RUNNING,
        "kali-lab": ResourceState.SUSPENDED,  # saved (savestate)
        "dev-01": ResourceState.PAUSED,
    }


def test_parse_list_empty():
    assert parse_list("") == {}


@pytest.mark.parametrize(
    "state",
    ["starting", "stopping", "saving", "restoring", "teleporting", "aborted", ""],
)
def test_parse_list_transient_states_are_unknown(state):
    output = f"Name: v\nState: {state}\n"
    assert parse_list(output) == {"v": ResourceState.UNKNOWN}


def test_parse_list_section_without_state_is_unknown_not_dropped():
    # A VM whose section lacks a State: line (corrupt, mid-creation) is still
    # listed — as UNKNOWN — so it doesn't silently disappear from the UI.
    output = "Name: half-formed\nGroups: /\nUUID: abc-def\n"  # no State: line
    assert parse_list(output) == {"half-formed": ResourceState.UNKNOWN}


def test_parse_list_two_vms_share_the_walk_no_state_leak():
    # Regression: ensure current_name resets per Name: line so VM A's missing
    # State: doesn't get filled by VM B's.
    output = "Name: a\nGroups: /\nName: b\nState: running\n"
    assert parse_list(output) == {"a": ResourceState.UNKNOWN, "b": ResourceState.RUNNING}


def test_parse_list_state_without_since_tail():
    # Defensive: state can appear bare (no trailing parens).
    assert parse_list("Name: v\nState: running\n") == {"v": ResourceState.RUNNING}


# --- snapshot parser: cross-version field names ---


def test_parse_snapshots_sorts_oldest_first():
    snaps = parse_snapshots((FIXTURES / "vbox_snapshot_list.txt").read_text())
    assert [s.name for s in snaps] == ["clean-baseline", "post-patches", "before-exploit"]
    # `Created:` field name (VirtualBox 6.x+) — name/UUID stripped off
    assert all("(" not in s.name for s in snaps)
    assert snaps[-1].created == "2026-01-15T09:00:00.000000000Z"


def test_parse_snapshots_accepts_legacy_date_created_field():
    # VirtualBox 5.x prints `Date created:` at column 0; the parser must read
    # either form. The legacy form is checked first because both strings
    # contain the substring `created`.
    output = (
        "Name: snap-a (UUID: 11111111-1111-1111-1111-111111111111)\n"
        "   Date created: 2026-01-10 08:00:00\n"
        "   State:        powered off (snapshot)\n"
        "\n"
        "Name: snap-b (UUID: 22222222-2222-2222-2222-222222222222)\n"
        "   Date created: 2026-01-12 14:30:00\n"
        "   State:        powered off (snapshot)\n"
    )
    snaps = parse_snapshots(output)
    assert [s.name for s in snaps] == ["snap-a", "snap-b"]
    assert snaps[0].created == "2026-01-10 08:00:00"


def test_parse_snapshots_empty():
    assert parse_snapshots("") == []


def test_parse_snapshots_strips_uuid_parens_from_name():
    # The Name: line carries an embedded `(UUID: …)`; only the human label
    # belongs in `Snapshot.name`.
    output = (
        "   Name: my snapshot (UUID: aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee)\n"
        "      Created: 2026-01-15T09:00:00.000000000Z\n"
    )
    snaps = parse_snapshots(output)
    assert snaps[0].name == "my snapshot"


# --- command builders: exact strings ---


def test_command_strings():
    # A name with spaces forces shlex.quote to actually add quotes — for
    # safe names (no spaces/quotes/shells metachars) shlex leaves the word
    # bare, so testing against an unquoted form would silently lose the
    # injection-resistance check.
    vm = "debian test"
    snap = "snap 1"
    assert start_command(vm) == "VBoxManage startvm 'debian test' --type headless"
    # Graceful ACPI shutdown — `-Force` is not a thing on VBoxManage; the
    # ops layer polls and escalates to poweroff instead.
    assert stop_command(vm) == "VBoxManage controlvm 'debian test' acpipowerbutton"
    # Hard power-off — no `-Force` flag exists on VBox; poweroff IS the hard stop.
    assert force_stop_command(vm) == "VBoxManage controlvm 'debian test' poweroff"
    # savestate freezes RAM to disk; `startvm` resumes from it.
    assert suspend_command(vm) == "VBoxManage controlvm 'debian test' savestate"
    assert snapshot_list_command(vm) == "VBoxManage snapshot 'debian test' list"
    assert snapshot_create_command(vm, snap) == ("VBoxManage snapshot 'debian test' take 'snap 1'")


def test_start_command_always_headless():
    # Headless is non-negotiable over SSH — otherwise the VM tries to open a
    # GUI window on the host and hangs waiting for a display.
    assert "--type headless" in start_command("anything")


# --- injection: every builder quotes a hostile name ---


@pytest.mark.parametrize(
    "evil",
    [
        "vm; rm -rf /",
        "vm name with spaces",
        'vm"quote',
        "vm'apos",
        "vm$(whoami)",
        "vm&&echo pwned",
    ],
)
def test_command_builders_quote_hostile_names(evil):
    from shlex import quote

    for builder in (
        start_command,
        stop_command,
        force_stop_command,
        suspend_command,
        snapshot_list_command,
        lambda n: snapshot_create_command(n, "snap"),
    ):
        cmd = builder(evil)
        assert quote(evil) in cmd, f"{builder} did not quote {evil!r}: {cmd}"


# --- provider: VM kind + capability set ---


async def test_provider_kinds_and_caps():
    t = FakeTransport(label="vbox")
    t.respond("VBoxManage list -l vms", stdout=(FIXTURES / "vbox_list_vms_long.txt").read_text())
    provider = build_command_provider("virtualbox", "host-vbox", t)
    resources = await provider.list_resources()
    assert {r.id for r in resources} == {"debian-test", "gateway-04", "kali-lab", "dev-01"}
    assert all(r.kind is ResourceKind.VM for r in resources)
    # Declared capabilities
    for cap in (
        Capability.START,
        Capability.STOP,
        Capability.FORCE_STOP,
        Capability.SUSPEND,
        Capability.SNAPSHOT_LIST,
        Capability.SNAPSHOT_CREATE,
    ):
        assert provider.supports(cap)
    # Not in v1: same shape as hyperv — no logs / disk-stats single-call shape.
    assert not provider.supports(Capability.LOGS)
    assert not provider.supports(Capability.DISK_STATS)
    assert not provider.supports(Capability.RESTART)


def test_provider_type_name():
    assert VirtualBoxProvider.type_name == "virtualbox"


async def test_provider_stop_is_graceful_for_acpi():
    # acpipowerbutton is fire-and-forget ACPI — ops layer must poll and
    # escalate to force_stop, mirroring libvirt's virsh shutdown posture.
    provider = build_command_provider("virtualbox", "host-vbox", FakeTransport())
    assert provider.stop_is_graceful is True


async def test_logs_unsupported_bubbles():
    provider = build_command_provider("virtualbox", "host-vbox", FakeTransport())
    with pytest.raises(Unsupported):
        await provider.logs("debian-test")


async def test_disk_stats_unsupported_bubbles():
    provider = build_command_provider("virtualbox", "host-vbox", FakeTransport())
    with pytest.raises(Unsupported):
        await provider.disk_stats()


async def test_snapshot_list_parses_from_fixture():
    t = FakeTransport(label="vbox")
    t.respond(
        snapshot_list_command("debian-test"),
        stdout=(FIXTURES / "vbox_snapshot_list.txt").read_text(),
    )
    provider = build_command_provider("virtualbox", "host-vbox", t)
    snaps = await provider.snapshot_list("debian-test")
    assert [s.name for s in snaps] == ["clean-baseline", "post-patches", "before-exploit"]


async def test_stop_failure_surfaces():
    t = FakeTransport(label="vbox")
    t.respond(
        stop_command("ghost"),
        stderr="VBoxManage error: Could not find a registered machine",
        exit_code=1,
    )
    provider = build_command_provider("virtualbox", "host-vbox", t)
    with pytest.raises(RuntimeError, match="Could not find a registered machine"):
        await provider.stop("ghost")


async def test_list_failure_surfaces():
    t = FakeTransport(label="vbox")
    t.respond("VBoxManage list -l vms", stderr="VBoxManage: error", exit_code=1)
    provider = build_command_provider("virtualbox", "host-vbox", t)
    with pytest.raises(RuntimeError, match="VBoxManage: error"):
        await provider.list_resources()
