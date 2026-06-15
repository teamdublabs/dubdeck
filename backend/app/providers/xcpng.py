"""XCP-ng provider — xe CLI over SSHTransport.

Pure command builders + parsers over captured `xe` output (see
tests/fixtures/). VMs are identified by UUID (not name-label) since
name-labels can collide across a pool. Templates are excluded.

XCP-ng has no virsh — xe is the sole CLI tool and is available in PATH
on every XCP-ng host. Commands run over SSHTransport on the host, so no
API token management is needed. This is the recommended approach for
lab deployments; API-token auth via XenAPI XML-RPC is documented in
docs/XCP-NG-PROVIDER-SPEC.md but not implemented here.

Snapshot restore/delete are deliberately absent — same boundary as every
other provider. Destructive ops stay manual.
"""

import shlex
import re

from app.providers.base import (
    Capability,
    CommandProvider,
    Resource,
    ResourceKind,
    ResourceState,
    Snapshot,
)

# ── Command templates ────────────────────────────────────────────────────────

XE_VM_LIST = "xe vm-list --all"
XE_SNAPSHOT_LIST = "xe snapshot-list uuid={uuid}"
XE_VDI_LIST = "xe vdi-list vm-uuid={uuid} params=snapshot-of,physical-utilisation,virtual-size"

_STATES = {
    "running": ResourceState.RUNNING,
    "halted": ResourceState.STOPPED,
    "suspended": ResourceState.SUSPENDED,
    "paused": ResourceState.PAUSED,
}


# ── State helpers ────────────────────────────────────────────────────────────

def _parse_power_state(raw: str) -> ResourceState:
    return _STATES.get(raw.strip().lower(), ResourceState.UNKNOWN)


# ── Parsers ──────────────────────────────────────────────────────────────────

def parse_vm_list(output: str) -> dict[str, ResourceState]:
    """Parse `xe vm-list --all` output.

    Each VM block looks like:
        name-label ( RO)                       : my-vm
        uuid ( RO)                             : 01234567-89ab-cdef-0123-456789abcdef
        power-state ( RO)                      : running
        is-a-template ( RO)                    : false

    We extract uuid (→ id), name-label (→ name), power-state (→ state),
    and skip templates.  A blank line separates VM blocks.
    """
    states: dict[str, ResourceState] = {}
    current: dict[str, str] = {}

    for line in output.splitlines():
        line = line.rstrip()
        if not line.strip():
            if current.get("uuid") and current.get("is-a-template", "").lower() != "true":
                vm_uuid = current["uuid"].strip()
                state_raw = current.get("power-state", "").strip()
                states[vm_uuid] = _parse_power_state(state_raw)
            current = {}
            continue

        m = re.match(r"^\s*([\w-]+)\s*\(\s*\w+\s*\)\s*:\s*(.*)$", line)
        if not m:
            continue
        current[m.group(1).strip()] = m.group(2).strip()

    if current.get("uuid") and current.get("is-a-template", "").lower() != "true":
        vm_uuid = current["uuid"].strip()
        state_raw = current.get("power-state", "").strip()
        states[vm_uuid] = _parse_power_state(state_raw)

    return states


def parse_snapshots(output: str) -> list[Snapshot]:
    """Parse `xe snapshot-list uuid=<vm-uuid>` output.

    Each snapshot block:
        uuid ( RO)                             : abcdef12-3456-7890-abcd-ef1234567890
        name-label ( RO)                       : dubdeck-20260613-103000
        snapshot-of ( RO)                       : 01234567-89ab-cdef-0123-456789abcdef

    name-label may embed an ISO-style timestamp: try to parse
    'dubdeck-YYYYMMDD-HHMMSS' into a created string; fall back to ''.
    """
    snaps: list[Snapshot] = []
    current: dict[str, str] = {}
    timestamp_re = re.compile(r"(\d{8}[-_]\d{6})")

    for line in output.splitlines():
        line = line.rstrip()
        if not line.strip():
            if current.get("uuid"):
                snap_uuid = current["uuid"].strip()
                name_label = current.get("name-label", "").strip() or snap_uuid
                ts_match = timestamp_re.search(name_label)
                created = ""
                if ts_match:
                    normalised = ts_match.group(1).replace("_", "-", 1)
                    try:
                        import datetime
                        dt = datetime.datetime.strptime(normalised[:15], "%Y%m%d-%H%M%S")
                        created = dt.strftime("%Y-%m-%d %H:%M:%S")
                    except ValueError:
                        created = ts_match.group(1)
                snaps.append(Snapshot(name=snap_uuid, created=created, current=False))
            current = {}
            continue

        m = re.match(r"^\s*([\w-]+)\s*\(\s*\w+\s*\)\s*:\s*(.*)$", line)
        if not m:
            continue
        current[m.group(1).strip()] = m.group(2).strip()

    if current.get("uuid"):
        snap_uuid = current["uuid"].strip()
        name_label = current.get("name-label", "").strip() or snap_uuid
        ts_match = timestamp_re.search(name_label)
        created = ""
        if ts_match:
            normalised = ts_match.group(1).replace("_", "-", 1)
            try:
                import datetime
                dt = datetime.datetime.strptime(normalised[:15], "%Y%m%d-%H%M%S")
                created = dt.strftime("%Y-%m-%d %H:%M:%S")
            except ValueError:
                created = ts_match.group(1)
        snaps.append(Snapshot(name=snap_uuid, created=created, current=False))

    return sorted(snaps, key=lambda s: s.created)


def parse_vdi_list(output: str) -> dict[str, int]:
    """Parse `xe vdi-list vm-uuid=<uuid>` output.

    Returns {vdi_uuid: physical_utilisation_bytes}.
    physical-utilisation is in bytes already on recent XCP-ng.
    """
    disks: dict[str, int] = {}
    current: dict[str, str] = {}

    for line in output.splitlines():
        line = line.rstrip()
        if not line.strip():
            if current.get("uuid"):
                try:
                    util = int(current.get("physical-utilisation", "0").strip())
                except ValueError:
                    util = 0
                disks[current["uuid"].strip()] = util
            current = {}
            continue

        m = re.match(r"^\s*([\w-]+)\s*\(\s*\w+\s*\)\s*:\s*(.*)$", line)
        if not m:
            continue
        current[m.group(1).strip()] = m.group(2).strip()

    if current.get("uuid"):
        try:
            util = int(current.get("physical-utilisation", "0").strip())
        except ValueError:
            util = 0
        disks[current["uuid"].strip()] = util

    return disks


# ── Command builders ─────────────────────────────────────────────────────────

def start_command(vm_uuid: str) -> str:
    return f"xe vm-start uuid={shlex.quote(vm_uuid)}"


def stop_command(vm_uuid: str) -> str:
    # Graceful ACPI-like shutdown — some guests ignore it; ops layer escalates.
    return f"xe vm-shutdown uuid={shlex.quote(vm_uuid)}"


def force_stop_command(vm_uuid: str) -> str:
    return f"xe vm-force-shutdown uuid={shlex.quote(vm_uuid)}"


def restart_command(vm_uuid: str) -> str:
    # XCP-ng has no single-command graceful restart.  We chain shutdown+start
    # so the ops layer can use the standard stop_is_graceful escalation path.
    # The && means the start only fires if shutdown succeeded (exit 0).
    return f"xe vm-shutdown uuid={shlex.quote(vm_uuid)} && xe vm-start uuid={shlex.quote(vm_uuid)}"


def suspend_command(vm_uuid: str) -> str:
    return f"xe vm-suspend uuid={shlex.quote(vm_uuid)}"


def snapshot_list_command(vm_uuid: str) -> str:
    return XE_SNAPSHOT_LIST.format(uuid=shlex.quote(vm_uuid))


def snapshot_create_command(vm_uuid: str, name: str) -> str:
    # Snapshot name-label is a display string — safe because it never reaches
    # a shell (xe accepts it as a string arg).  We embed it directly.
    return (
        f"xe snapshot-create uuid={shlex.quote(vm_uuid)}"
        f" snapshot-name-label={shlex.quote(name)}"
    )


def disk_stats_command(vm_uuid: str) -> str:
    return XE_VDI_LIST.format(uuid=shlex.quote(vm_uuid))


def logs_command(vm_uuid: str) -> str:
    # XCP-ng exposes guest console output as a param on the VM record.
    # `xe vm-param-get uuid=<uuid> param-name=console-uri` returns the
    # primary console URL (e.g._vnc_console/...?  …).  The UI can use this
    # to open a VNC/WebSocket session.  This command is the "logs" proxy —
    # it returns a URL, not text, which is what XCP-ng makes available over
    # SSH for console data.
    return f"xe vm-param-get uuid={shlex.quote(vm_uuid)} param-name=console-uri"


# ── Provider class ───────────────────────────────────────────────────────────

class XCPNgProvider(CommandProvider):
    type_name = "xcpng"
    capabilities = frozenset(
        {
            Capability.START,
            Capability.STOP,
            Capability.FORCE_STOP,
            Capability.RESTART,
            Capability.SUSPEND,
            Capability.SNAPSHOT_LIST,
            Capability.SNAPSHOT_CREATE,
            Capability.DISK_STATS,
            Capability.LOGS,
        }
    )
    # xe vm-shutdown is fire-and-forget; some guests ignore it.
    stop_is_graceful = True

    async def list_resources(self) -> list[Resource]:
        result = await self._t.run(XE_VM_LIST)
        if not result.ok:
            raise RuntimeError(result.stderr.strip() or f"exit {result.exit_code}")
        return [
            Resource(id=vm_uuid, name=vm_uuid, kind=ResourceKind.VM, state=state)
            for vm_uuid, state in parse_vm_list(result.stdout).items()
        ]

    async def start(self, rid: str, timeout: float = 90.0) -> None:
        await self._run_or_raise(start_command(rid), timeout)

    async def stop(self, rid: str, timeout: float = 180.0) -> None:
        await self._run_or_raise(stop_command(rid), timeout)

    async def force_stop(self, rid: str, timeout: float = 180.0) -> None:
        await self._run_or_raise(force_stop_command(rid), timeout)

    async def restart(self, rid: str, timeout: float = 180.0) -> None:
        await self._run_or_raise(restart_command(rid), timeout)

    async def suspend(self, rid: str, timeout: float = 180.0) -> None:
        await self._run_or_raise(suspend_command(rid), timeout)

    async def snapshot_list(self, rid: str) -> list[Snapshot]:
        result = await self._t.run(snapshot_list_command(rid))
        if not result.ok:
            raise RuntimeError(f"snapshot list failed: {result.stderr}")
        return parse_snapshots(result.stdout)

    async def snapshot_create(self, rid: str, name: str, timeout: float = 300.0) -> None:
        await self._run_or_raise(snapshot_create_command(rid, name), timeout)

    async def logs(self, rid: str, n: int = 200) -> str:
        # n is accepted for API compatibility but has no effect — XCP-ng
        # console access is a VNC URL, not a text stream.  The caller passes
        # it; we ignore it and return the console URI.
        result = await self._t.run(logs_command(rid), timeout=30.0)
        if not result.ok:
            raise RuntimeError(f"logs failed: {result.stderr}")
        return result.stdout.strip()

    async def disk_stats(self) -> dict[str, int]:
        """Aggregate disk usage across all VDIs for every VM this provider
        can see.  One `xe vm-list` call to get VM UUIDs, then one
        `xe vdi-list vm-uuid=<uuid>` per VM — matching the low-overhead
        pattern of the other providers."""
        list_result = await self._t.run(XE_VM_LIST)
        if not list_result.ok:
            raise RuntimeError(f"disk stats failed: {list_result.stderr}")
        vm_uuids = list(parse_vm_list(list_result.stdout).keys())

        disks: dict[str, int] = {}
        for vm_uuid in vm_uuids:
            result = await self._t.run(disk_stats_command(vm_uuid), timeout=30.0)
            if not result.ok:
                continue
            for vdi_uuid, util in parse_vdi_list(result.stdout).items():
                disks[f"{vm_uuid}/{vdi_uuid}"] = util
        return disks