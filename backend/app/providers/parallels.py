"""Parallels (prlctl) provider.

Command builders and output parsers are pure functions over captured command
output — see tests/fixtures/. Ported verbatim from the original hypervisors.py;
the prlctl-vs-acpi and snapshot-restore comments encode real behaviour.
"""

import json
import shlex
from pathlib import PurePosixPath

from app.providers.base import (
    Capability,
    CommandProvider,
    Resource,
    ResourceKind,
    ResourceState,
    Snapshot,
)

PRLCTL_LIST = "prlctl list --all -o name,status"
# The Mac's forced-command shim translates "dubdeck-vm-disks" into
# `du -sk ~/Parallels/*.pvm`; a raw du command would never pass its allowlist.
VM_DISKS = "dubdeck-vm-disks"

_STATES = {
    "running": ResourceState.RUNNING,
    "stopped": ResourceState.STOPPED,
    "suspended": ResourceState.SUSPENDED,
    "paused": ResourceState.PAUSED,
}


def parse_list(output: str) -> dict[str, ResourceState]:
    """NAME ... STATUS columns; names may contain spaces, status is the last token."""
    states: dict[str, ResourceState] = {}
    for line in output.splitlines()[1:]:
        if not line.strip():
            continue
        name, _, status = line.rstrip().rpartition(" ")
        states[name.rstrip()] = _STATES.get(status.strip(), ResourceState.UNKNOWN)
    return states


def start_command(vm: str) -> str:
    return f"prlctl start {shlex.quote(vm)}"


def stop_command(vm: str) -> str:
    # Bare `prlctl stop` does a clean shutdown via Parallels Tools — the same
    # fast path as the GUI "Shut Down". --acpi was much slower: it emulates the
    # power button, which Linux guests without an ACPI handler ignore until a
    # ~60s timeout.
    return f"prlctl stop {shlex.quote(vm)}"


def force_stop_command(vm: str) -> str:
    # Hard power-off — pulls the virtual plug.
    return f"prlctl stop {shlex.quote(vm)} --kill"


def suspend_command(vm: str) -> str:
    # Freezes RAM to disk; `prlctl start` resumes from it.
    return f"prlctl suspend {shlex.quote(vm)}"


def snapshot_list_command(vm: str) -> str:
    return f"prlctl snapshot-list {shlex.quote(vm)} -j"


def snapshot_create_command(vm: str, name: str) -> str:
    # Restore/delete are deliberately absent — destructive ops stay manual
    # (and the Mac shim would reject them anyway).
    return f"prlctl snapshot {shlex.quote(vm)} -n {shlex.quote(name)}"


def parse_snapshots(output: str) -> list[Snapshot]:
    """`prlctl snapshot-list -j`: {id: {name, date, current, ...}, ...}."""
    if not output.strip():
        return []
    data = json.loads(output)
    snaps = [
        Snapshot(name=v["name"], created=v.get("date", ""), current=bool(v.get("current")))
        for v in data.values()
    ]
    return sorted(snaps, key=lambda s: s.created)


def parse_disks(output: str) -> dict[str, int]:
    """`du -sk <dir>.pvm` lines → VM name (bundle basename) → bytes."""
    disks: dict[str, int] = {}
    for line in output.splitlines():
        size_kib, _, path = line.partition("\t")
        if not path.strip().endswith(".pvm"):
            continue
        name = PurePosixPath(path.strip()).name.removesuffix(".pvm")
        disks[name] = int(size_kib) * 1024
    return disks


class ParallelsProvider(CommandProvider):
    type_name = "parallels"
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
    # `prlctl stop` blocks until the clean shutdown completes — no escalation
    # loop needed (contrast libvirt's fire-and-forget ACPI shutdown).
    stop_is_graceful = False

    async def list_resources(self) -> list[Resource]:
        result = await self._t.run(PRLCTL_LIST)
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
        result = await self._t.run(VM_DISKS, timeout=30.0)
        if not result.ok:
            raise RuntimeError(f"disk stats failed: {result.stderr}")
        return parse_disks(result.stdout)
