"""libvirt (virsh) provider — KVM hosts.

Pure command builders + parsers over captured output (see tests/fixtures/),
ported verbatim from hypervisors.py. The virsh-shutdown-vs-destroy and
managedsave comments encode real guest behaviour.
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

VIRSH_LIST = "virsh list --all"
VIRSH_DOMSTATS = "virsh domstats --block --state"

_STATES = {
    "running": ResourceState.RUNNING,
    "shut off": ResourceState.STOPPED,
    "paused": ResourceState.PAUSED,
    "pmsuspended": ResourceState.SUSPENDED,
}


def parse_list(output: str) -> dict[str, ResourceState]:
    """Id / Name / State columns; state may contain spaces ("shut off")."""
    states: dict[str, ResourceState] = {}
    for line in output.splitlines():
        parts = line.split(None, 2)
        if len(parts) < 3 or parts[0] in ("Id",) or set(line.strip()) == {"-"}:
            continue
        _, name, state = parts
        states[name] = _STATES.get(state.strip(), ResourceState.UNKNOWN)
    return states


def start_command(vm: str) -> str:
    return f"virsh start {shlex.quote(vm)}"


def stop_command(vm: str) -> str:
    # `virsh shutdown` is the KVM graceful (ACPI) request — some old vuln guests
    # ignore it, so the ops layer escalates to force_stop after a grace window.
    return f"virsh shutdown {shlex.quote(vm)}"


def force_stop_command(vm: str) -> str:
    # `virsh destroy` powers off the domain but does NOT undefine/delete it
    # (recoverable, snapshot-resettable).
    return f"virsh destroy {shlex.quote(vm)}"


def suspend_command(vm: str) -> str:
    # Freezes RAM to disk; `virsh start` resumes from it.
    return f"virsh managedsave {shlex.quote(vm)}"


def snapshot_list_command(vm: str) -> str:
    return f"virsh snapshot-list {shlex.quote(vm)}"


def snapshot_create_command(vm: str, name: str) -> str:
    # Restore/delete deliberately absent — destructive ops stay manual.
    return f"virsh snapshot-create-as {shlex.quote(vm)} --name {shlex.quote(name)}"


def parse_snapshots(output: str) -> list[Snapshot]:
    """Name / Creation Time / State columns; time contains spaces."""
    snaps: list[Snapshot] = []
    for line in output.splitlines():
        parts = line.split()
        if len(parts) < 4 or parts[0] == "Name" or set(line.strip()) == {"-"}:
            continue
        snaps.append(Snapshot(name=parts[0], created=" ".join(parts[1:4])))
    return sorted(snaps, key=lambda s: s.created)


def parse_disks(output: str) -> dict[str, int]:
    """`virsh domstats --block`: sum block.N.physical per domain → bytes."""
    disks: dict[str, int] = {}
    domain = None
    for line in output.splitlines():
        line = line.strip()
        if line.startswith("Domain:"):
            domain = line.split("'")[1]
            disks[domain] = 0
        elif domain and ".physical=" in line:
            disks[domain] += int(line.split("=")[1])
    return disks


class LibvirtProvider(CommandProvider):
    type_name = "libvirt"
    capabilities = frozenset(
        {
            Capability.START,
            Capability.STOP,
            Capability.FORCE_STOP,
            Capability.SUSPEND,
            Capability.SNAPSHOT_LIST,
            Capability.SNAPSHOT_CREATE,
            Capability.DISK_STATS,
        }
    )
    # `virsh shutdown` is fire-and-forget ACPI that some guests ignore; the ops
    # layer polls and escalates to force_stop. Its exit code is not the final
    # word (returns 0 even when the guest never powers off).
    stop_is_graceful = True

    async def list_resources(self) -> list[Resource]:
        result = await self._t.run(VIRSH_LIST)
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

    async def disk_stats(self) -> dict[str, int]:
        result = await self._t.run(VIRSH_DOMSTATS, timeout=30.0)
        if not result.ok:
            raise RuntimeError(f"disk stats failed: {result.stderr}")
        return parse_disks(result.stdout)
