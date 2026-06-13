"""Hyper-V provider — Windows VMs over OpenSSH, driven entirely by PowerShell.

The transport is an SSH connection to a Windows host whose default shell is
PowerShell (the documented setup — see docs/providers/hyperv.md). Every command
emits JSON via `ConvertTo-Json -Compress` and we parse the JSON; we never parse
PowerShell's human table output (it wraps, localises, and reflows columns).

Two PowerShell-specific gotchas this module encodes:

1. **Quoting is NOT shlex.** PowerShell parses its own command line, so resource
   names are wrapped in PowerShell single-quoted literals (`ps_quote`), where the
   only metacharacter is the single quote itself (doubled to escape). shlex.quote
   produces POSIX-shell quoting that PowerShell mis-parses — using it here would
   be both wrong and an injection hole. See the loud note on `ps_quote`.

2. **`ConvertTo-Json` shape + state are version-dependent.** A single object
   serialises as a bare `{...}`, several as a `[...]` array, zero as empty output;
   `_loads` normalises all three. Enum/date values serialise as their *numeric*
   underlying value in Windows PowerShell 5.1 but as a string in PowerShell 7, so
   the list/checkpoint commands force `.ToString()` in a calculated property to
   pin a stable string regardless of the host's PowerShell version. A UTF-8 BOM
   (common when the SSH shell doesn't force UTF-8 output) is stripped defensively.
"""

import json

from app.providers.base import (
    Capability,
    CommandProvider,
    Resource,
    ResourceKind,
    ResourceState,
    Snapshot,
)

# State is forced to a string via a calculated property so it never arrives as
# the VMState enum's raw integer (5.1) — see module docstring gotcha #2.
GET_VM = (
    "Get-VM | Select-Object Name,"
    "@{Name='State';Expression={$_.State.ToString()}} | ConvertTo-Json -Compress"
)

_STATES = {
    "running": ResourceState.RUNNING,
    "off": ResourceState.STOPPED,
    "saved": ResourceState.SUSPENDED,  # Save-VM freezes RAM to disk → "Saved"
    "paused": ResourceState.PAUSED,
    # Transient states (Starting/Stopping/Saving/Pausing/Resuming/Reset…) fall
    # through to UNKNOWN; the next poll resolves them.
}


def ps_quote(s: str) -> str:
    """Wrap a value as a PowerShell single-quoted literal.

    Inside PowerShell single quotes EVERY character is literal — no `$` variable
    or `$()` subexpression expansion, no backtick escapes, no `;`/`|`/`&` command
    chaining — and the *only* metacharacter is the single quote itself, escaped by
    doubling (`''`). Wrapping a name this way neutralises every injection vector
    an operator-supplied VM or snapshot name could carry.

    This is deliberately NOT `shlex.quote`. shlex emits POSIX-shell quoting, which
    PowerShell parses by different rules (it does not treat `\\` as an escape and
    handles embedded quotes differently) — using it here would be both incorrect
    and unsafe. Every name that reaches a command line goes through THIS function.
    """
    return "'" + s.replace("'", "''") + "'"


def _loads(output: str) -> list[dict]:
    """Normalise `ConvertTo-Json -Compress` output to a list of rows.

    Handles all three shapes ConvertTo-Json produces — bare object (one item),
    array (many), empty (none) — and strips a leading UTF-8 BOM that a non-UTF-8
    SSH shell may prepend.
    """
    text = output.lstrip("\ufeff").strip()
    if not text:
        return []
    data = json.loads(text)
    return data if isinstance(data, list) else [data]


def parse_list(output: str) -> dict[str, ResourceState]:
    """`Get-VM … | ConvertTo-Json` → VM name → state. Resource id is the VM name
    (Hyper-V VM names are unique per host)."""
    states: dict[str, ResourceState] = {}
    for row in _loads(output):
        name = row["Name"]
        states[name] = _STATES.get(str(row.get("State", "")).lower(), ResourceState.UNKNOWN)
    return states


def parse_snapshots(output: str) -> list[Snapshot]:
    """`Get-VMCheckpoint … | ConvertTo-Json` → checkpoints, oldest first. The
    command forces CreationTime to an ISO-8601 string (`.ToString('o')`) so it
    sorts and renders stably across PowerShell versions."""
    snaps = [
        Snapshot(name=row["Name"], created=str(row.get("Created", ""))) for row in _loads(output)
    ]
    return sorted(snaps, key=lambda s: s.created)


def start_command(vm: str) -> str:
    return f"Start-VM -Name {ps_quote(vm)}"


def stop_command(vm: str) -> str:
    # Graceful in-guest shutdown via the Hyper-V shutdown integration service.
    # A guest without integration services (or sitting at a prompt) ignores it,
    # exactly like libvirt's ACPI shutdown — so stop_is_graceful is True and the
    # ops layer escalates to force_stop. `-Force` here only suppresses the
    # interactive confirmation prompt (which would hang a non-interactive SSH
    # session); it does NOT make the shutdown hard.
    return f"Stop-VM -Name {ps_quote(vm)} -Force"


def force_stop_command(vm: str) -> str:
    # Hard power-off — `-TurnOff` pulls the virtual plug (no guest cooperation).
    return f"Stop-VM -Name {ps_quote(vm)} -TurnOff -Force"


def suspend_command(vm: str) -> str:
    # Saves VM state (RAM) to disk; `Start-VM` resumes from it.
    return f"Save-VM -Name {ps_quote(vm)}"


def snapshot_list_command(vm: str) -> str:
    return (
        f"Get-VMCheckpoint -VMName {ps_quote(vm)} | Select-Object Name,"
        "@{Name='Created';Expression={$_.CreationTime.ToString('o')}} | ConvertTo-Json -Compress"
    )


def snapshot_create_command(vm: str, name: str) -> str:
    # Checkpoint = Hyper-V's snapshot. Restore/delete deliberately absent —
    # destructive ops stay manual, the same boundary as the other providers.
    return f"Checkpoint-VM -Name {ps_quote(vm)} -SnapshotName {ps_quote(name)}"


class HyperVProvider(CommandProvider):
    type_name = "hyperv"
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
    # `Stop-VM` requests an in-guest shutdown the OS can ignore — escalate to
    # force_stop (TurnOff) on timeout, exactly like libvirt's ACPI shutdown.
    stop_is_graceful = True

    async def list_resources(self) -> list[Resource]:
        result = await self._t.run(GET_VM)
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
