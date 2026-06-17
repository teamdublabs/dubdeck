"""VirtualBox provider — `VBoxManage`-driven VMs on a Linux, macOS, or Windows
host reached over SSH.

Same shape as libvirt: command builders and parsers are pure functions over
captured command output (see tests/fixtures/), and the provider class wires them
to a Transport. The acpipowerbutton-vs-poweroff comments below encode the real
guest behaviour that drives `stop_is_graceful = True`.

A few VirtualBox specifics this module bakes in:

1. **Long-format listing (`VBoxManage list -l vms`).** The short format
   (`VBoxManage list vms`) returns only names and UUIDs — no state. The long
   format is one big text dump with one `Name:` / ... / `State:` block per VM
   concatenated back-to-back; we walk it line by line and pair each `Name:` with
   the first `State:` line that follows it. A VM section without a `State:`
   line (corrupt, mid-creation) is recorded as UNKNOWN rather than dropped —
   the next poll resolves it.

2. **State values carry a `(since …)` tail.** `State: powered off (since
   2026-01-15T09:00:00)` is the canonical form; we split on " (" and keep the
   leading token so the parser sees `powered off`, `running`, `saved`, or
   `paused` cleanly. Transient values (`starting`, `stopping`, `saving`,
   `restoring`, `teleporting`, `live snapshotting`, `aborted`, `""`) fall
   through to UNKNOWN until the next poll.

3. **Headless start.** `VBoxManage startvm` opens a GUI window by default —
   useless over SSH. The command always passes `--type headless`; the VM still
   boots normally, it just doesn't try to attach a display.

4. **POSIX shell quoting.** `VBoxManage` runs through the same SSH shell the
   other POSIX providers use (bash/zsh on Linux/macOS); `shlex.quote` is the
   correct grammar. PowerShell-on-Windows is not the typical VBox-over-SSH
   deployment and is out of scope; if a user wants that path they can switch to
   the hyperv provider.
"""

import shlex

from app.providers.base import (
    Capability,
    CommandProvider,
    Resource,
    ResourceKind,
    ResourceState,
    Snapshot,
)

# `--long` (alias `-l`) is the only single-call shape that surfaces per-VM state.
# The short form is just names + UUIDs; per-VM state would force N extra calls.
VBOX_LIST = "VBoxManage list -l vms"

_STATES = {
    "powered off": ResourceState.STOPPED,
    "running": ResourceState.RUNNING,
    "saved": ResourceState.SUSPENDED,  # savestate — RAM frozen to disk
    "paused": ResourceState.PAUSED,
    # Transient / crash states (starting / stopping / saving / restoring /
    # teleporting[ paused] / live snapshotting / aborted / "") → next poll.
}


def parse_list(output: str) -> dict[str, ResourceState]:
    """`VBoxManage list -l vms` → VM name → state.

    The long output is one big text dump: each VM is a sequence of `Key:
    value` lines (no blank-line separator), so the parser tracks `current_name`
    from a `Name:` line and consumes the first `State:` line that follows.
    `current_name` is finalized on the next `Name:` (or at EOF) so a VM whose
    `State:` line is missing still appears in the map as UNKNOWN.
    """
    states: dict[str, ResourceState] = {}
    current_name: str | None = None
    has_state = False

    for line in output.splitlines():
        if line.startswith("Name:"):
            # Finalize the prior VM section (if any) that never produced a State:.
            if current_name is not None and not has_state:
                states[current_name] = ResourceState.UNKNOWN
            current_name = line[len("Name:") :].strip()
            has_state = False
        elif line.startswith("State:") and current_name is not None and not has_state:
            # `State: powered off (since 2026-01-15T09:00:00)` → "powered off".
            raw = line[len("State:") :].strip().split(" (", 1)[0].strip().lower()
            states[current_name] = _STATES.get(raw, ResourceState.UNKNOWN)
            has_state = True

    # EOF — same finalization as the inter-section case above.
    if current_name is not None and not has_state:
        states[current_name] = ResourceState.UNKNOWN

    return states


def start_command(vm: str) -> str:
    # `--type headless`: no GUI window opens on the host. The VM still boots
    # normally; "headless" here just means "no display attachment" — exactly
    # what we want over SSH.
    return f"VBoxManage startvm {shlex.quote(vm)} --type headless"


def stop_command(vm: str) -> str:
    # `acpipowerbutton` is the graceful guest shutdown — same ACPI shape as
    # libvirt's `virsh shutdown` and parallels' bare `prlctl stop`. Guests
    # without ACPI support (or sitting at a prompt) ignore it, which is why
    # `stop_is_graceful = True` below and the ops layer escalates to poweroff.
    return f"VBoxManage controlvm {shlex.quote(vm)} acpipowerbutton"


def force_stop_command(vm: str) -> str:
    # `poweroff` is a hard pull-the-plug — equivalent to libvirt's `virsh
    # destroy` (the VM definition is not removed; `startvm` resumes it).
    return f"VBoxManage controlvm {shlex.quote(vm)} poweroff"


def suspend_command(vm: str) -> str:
    # `savestate` freezes RAM to disk; `VBoxManage startvm` resumes from it.
    return f"VBoxManage controlvm {shlex.quote(vm)} savestate"


def snapshot_list_command(vm: str) -> str:
    return f"VBoxManage snapshot {shlex.quote(vm)} list"


def snapshot_create_command(vm: str, name: str) -> str:
    # Restore/delete deliberately absent — same no-destructive-ops boundary as
    # every other VM provider (`VBoxManage snapshot <vm> restore|delete` are
    # out of scope; operators run them manually).
    return f"VBoxManage snapshot {shlex.quote(vm)} take {shlex.quote(name)}"


def parse_snapshots(output: str) -> list[Snapshot]:
    """`VBoxManage snapshot <vm> list` → list of snapshots, oldest first.

    The output format varies subtly between VirtualBox versions (5.x prints
    `Date created:` at column 0, 6.x/7.x print an indented `Created:`), so the
    parser accepts either field name and either indentation. Snapshot sections
    are delimited by `Name:` lines (possibly indented); the `(UUID: …)` tail on
    the Name line is stripped so the snapshot's `name` is the human label only.
    """
    snaps: list[Snapshot] = []
    current_name: str | None = None
    current_created: str = ""

    for line in output.splitlines():
        stripped = line.lstrip()
        if stripped.startswith("Name:"):
            # New section — finalize the previous snapshot (if any) before
            # starting the new one.
            if current_name is not None:
                snaps.append(Snapshot(name=current_name, created=current_created))
            # `Name: snap1 (UUID: abc-def-…)` → just "snap1".
            current_name = stripped[len("Name:") :].strip().split(" (UUID:", 1)[0].strip()
            current_created = ""
        elif current_name is not None and ("Date created:" in stripped or "Created:" in stripped):
            # Pick whichever key the version emits; `Date created:` is checked
            # first because it's the older prefix and would otherwise match
            # before `Created:` in a line containing both.
            if "Date created:" in stripped:
                current_created = stripped.split("Date created:", 1)[1].strip()
            else:
                current_created = stripped.split("Created:", 1)[1].strip()

    if current_name is not None:
        snaps.append(Snapshot(name=current_name, created=current_created))

    return sorted(snaps, key=lambda s: s.created)


class VirtualBoxProvider(CommandProvider):
    type_name = "virtualbox"
    capabilities = frozenset(
        {
            Capability.START,
            Capability.STOP,
            Capability.FORCE_STOP,
            Capability.SUSPEND,
            Capability.SNAPSHOT_LIST,
            Capability.SNAPSHOT_CREATE,
        }
    )
    # `acpipowerbutton` is fire-and-forget ACPI that some guests ignore. Exit
    # code is not the final word (returns 0 even when the guest never powers
    # off), so the ops layer polls and escalates to `poweroff` on a grace
    # timeout. Same shape as libvirt's `virsh shutdown`.
    stop_is_graceful = True

    async def list_resources(self) -> list[Resource]:
        result = await self._t.run(VBOX_LIST)
        if not result.ok:
            raise RuntimeError(result.stderr.strip() or f"exit {result.exit_code}")
        return [
            Resource(id=name, name=name, kind=ResourceKind.VM, state=state)
            for name, state in parse_list(result.stdout).items()
        ]

    async def start(self, rid: str, timeout: float = 90.0) -> None:
        await self._run_or_raise(start_command(rid), timeout)

    async def stop(self, rid: str, timeout: float = 180.0) -> None:
        await self._run_or_raise(stop_command(rid), timeout)

    async def force_stop(self, rid: str, timeout: float = 180.0) -> None:
        await self._run_or_raise(force_stop_command(rid), timeout)

    async def suspend(self, rid: str, timeout: float = 180.0) -> None:
        await self._run_or_raise(suspend_command(rid), timeout)

    async def snapshot_list(self, rid: str) -> list[Snapshot]:
        result = await self._t.run(snapshot_list_command(rid))
        if not result.ok:
            raise RuntimeError(f"snapshot list failed: {result.stderr}")
        return parse_snapshots(result.stdout)

    async def snapshot_create(self, rid: str, name: str, timeout: float = 300.0) -> None:
        await self._run_or_raise(snapshot_create_command(rid, name), timeout)
