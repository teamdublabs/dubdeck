"""Proxmox VE provider — the first API provider (no Transport, no shell).

This is the phase that proves the Provider abstraction isn't secretly
SSH-shaped: everything here rides an `HttpClient` against the Proxmox REST API
(`/api2/json/...`), token-authenticated. There are no command builders and no
shlex quoting — the injection surface is the URL path, so `node`/`vmid` are
validated against strict allowlists and rejected (never "quoted") if hostile.

Two resource families, qemu (full VMs) and lxc (containers), both surface as
kind=VM — the Proxmox UI itself treats them as one "guest" list; a future badge
can distinguish them, but they share every capability. Resource id is
`{node}/{vmid}` (unique across a cluster; a bare vmid is not, since the same id
can recur per node in edge configs and the node is needed to build every URL).

Proxmox mutations are asynchronous: a POST returns a UPID (task handle), not a
result. We poll `/tasks/{upid}/status` to completion and treat `exitstatus != OK`
as the operation failing — without this, a start that fails at the hypervisor
would look like success to the caller.
"""

import asyncio
import re
import time
from urllib.parse import quote

from app.httpclient import HttpClient
from app.providers.base import (
    Capability,
    Provider,
    Resource,
    ResourceKind,
    ResourceState,
    Snapshot,
)

API = ""

# Path-injection guards. node names and vmids land in URL paths, so they get the
# same "reject, don't quote" posture the compose provider applies to stack names.
_NODE_RE = re.compile(r"^[A-Za-z0-9._-]+$")
_SNAPNAME_RE = re.compile(r"^[A-Za-z0-9_.-]+$")

_STATES = {
    "running": ResourceState.RUNNING,
    "stopped": ResourceState.STOPPED,
    "paused": ResourceState.PAUSED,
    "suspended": ResourceState.SUSPENDED,
    "prelaunch": ResourceState.UNKNOWN,  # transient lxc boot phase
}


def auth_header(token_id: str, token_secret: str) -> dict[str, str]:
    """Proxmox API-token header. Kept here (not in the client) so the exact
    format is testable without standing up a client."""
    return {"Authorization": f"PVEAPIToken={token_id}={token_secret}"}


def _node(node: str) -> str:
    if not _NODE_RE.match(node):
        raise ValueError(f"unsafe proxmox node name {node!r}")
    return node


def _vmid(vmid: str) -> str:
    if not vmid.isdigit():
        raise ValueError(f"unsafe proxmox vmid {vmid!r} — must be numeric")
    return vmid


def parse_guest_state(status: str) -> ResourceState:
    return _STATES.get(status, ResourceState.UNKNOWN)


class ProxmoxProvider(Provider):
    type_name = "proxmox"
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

    def __init__(self, instance_id: str, client: HttpClient, poll_interval: float = 1.0):
        self.instance_id = instance_id
        self._c = client
        self._poll = poll_interval
        # rid -> ("qemu"|"lxc"). Populated by list_resources so a later
        # start/stop knows which endpoint family to hit; refreshed on a miss.
        self._vtype: dict[str, str] = {}

    # --- helpers ---------------------------------------------------------

    async def _data(self, method: str, path: str, **kw):
        resp = await self._c.request(method, path, **kw)
        if not resp.ok:
            # 4xx/5xx is a delivered response; surface its body as the error so
            # auth/permission failures read clearly in the ops log and status.
            detail = ""
            if isinstance(resp.body, dict):
                detail = str(resp.body.get("errors") or resp.body.get("message") or "")
            raise RuntimeError(detail or f"proxmox HTTP {resp.status}")
        return (resp.body or {}).get("data")

    def _split(self, rid: str) -> tuple[str, str]:
        node, sep, vmid = rid.partition("/")
        if not sep:
            raise ValueError(f"proxmox resource id {rid!r} must be 'node/vmid'")
        return _node(node), _vmid(vmid)

    async def _resolve(self, rid: str) -> tuple[str, str, str]:
        """rid -> (node, vmid, vtype). Refreshes the type cache once on a miss so
        an action issued without a preceding list still works."""
        node, vmid = self._split(rid)
        if rid not in self._vtype:
            await self.list_resources()
        vtype = self._vtype.get(rid)
        if vtype is None:
            raise RuntimeError(f"proxmox: unknown guest {rid!r}")
        return node, vmid, vtype

    def _vm_base(self, node: str, vtype: str, vmid: str) -> str:
        return f"{API}/nodes/{node}/{vtype}/{vmid}"

    async def _wait_task(self, node: str, upid: str, timeout: float) -> None:
        """Poll a UPID to completion. A task that finishes with exitstatus other
        than OK is an operation failure; running past the timeout is a failure
        too (the lab must not wedge on a stuck task)."""
        path = f"{API}/nodes/{node}/tasks/{quote(upid, safe='')}/status"
        deadline = time.monotonic() + timeout
        while True:
            data = await self._data("GET", path) or {}
            if data.get("status") == "stopped":
                exit_status = data.get("exitstatus")
                if exit_status != "OK":
                    raise RuntimeError(f"proxmox task failed: {exit_status}")
                return
            if time.monotonic() >= deadline:
                raise RuntimeError(f"proxmox task {upid} did not finish within {timeout:g}s")
            await asyncio.sleep(self._poll)

    async def _status_action(self, rid: str, action: str, timeout: float) -> None:
        node, vmid, vtype = await self._resolve(rid)
        upid = await self._data("POST", f"{self._vm_base(node, vtype, vmid)}/status/{action}")
        if isinstance(upid, str):
            await self._wait_task(node, upid, timeout)

    # --- listing ---------------------------------------------------------

    async def list_resources(self) -> list[Resource]:
        nodes = await self._data("GET", f"{API}/nodes") or []
        resources: list[Resource] = []
        vtype_map: dict[str, str] = {}
        for n in nodes:
            node = n["node"]
            for vtype in ("qemu", "lxc"):
                guests = await self._data("GET", f"{API}/nodes/{_node(node)}/{vtype}") or []
                for g in guests:
                    vmid = str(g["vmid"])
                    rid = f"{node}/{vmid}"
                    vtype_map[rid] = vtype
                    resources.append(
                        Resource(
                            id=rid,
                            name=g.get("name") or rid,
                            kind=ResourceKind.VM,
                            state=parse_guest_state(g.get("status", "")),
                        )
                    )
        self._vtype = vtype_map
        return resources

    # --- capability methods ---------------------------------------------

    async def start(self, rid: str, timeout: float = 90.0) -> None:
        await self._status_action(rid, "start", timeout)

    async def stop(self, rid: str, timeout: float = 180.0) -> None:
        # graceful ACPI shutdown — the escalate-to-force path is the caller's
        # (services/ops), exactly as with the VM providers.
        await self._status_action(rid, "shutdown", timeout)

    async def force_stop(self, rid: str, timeout: float = 180.0) -> None:
        await self._status_action(rid, "stop", timeout)

    async def suspend(self, rid: str, timeout: float = 180.0) -> None:
        await self._status_action(rid, "suspend", timeout)

    async def snapshot_list(self, rid: str) -> list[Snapshot]:
        node, vmid, vtype = await self._resolve(rid)
        snaps = await self._data("GET", f"{self._vm_base(node, vtype, vmid)}/snapshot") or []
        out: list[Snapshot] = []
        for s in snaps:
            name = s.get("name", "")
            # Proxmox lists a synthetic "current" entry (the live state, not a
            # real snapshot) — drop it so the UI shows only restorable points.
            if name == "current":
                continue
            snaptime = s.get("snaptime")
            out.append(Snapshot(name=name, created=str(snaptime) if snaptime else ""))
        return out

    async def snapshot_create(self, rid: str, name: str, timeout: float = 300.0) -> None:
        if not _SNAPNAME_RE.match(name):
            raise ValueError(f"invalid snapshot name {name!r}")
        node, vmid, vtype = await self._resolve(rid)
        upid = await self._data(
            "POST", f"{self._vm_base(node, vtype, vmid)}/snapshot", json_body={"snapname": name}
        )
        if isinstance(upid, str):
            await self._wait_task(node, upid, timeout)

    async def disk_stats(self) -> dict[str, int]:
        """Provisioned disk per guest, read from the list payload (`maxdisk`).
        One pass over the same listing the status poll uses — no extra per-VM
        calls, matching the low-overhead disk tier of the other providers."""
        nodes = await self._data("GET", f"{API}/nodes") or []
        disks: dict[str, int] = {}
        for n in nodes:
            node = n["node"]
            for vtype in ("qemu", "lxc"):
                guests = await self._data("GET", f"{API}/nodes/{_node(node)}/{vtype}") or []
                for g in guests:
                    maxdisk = g.get("maxdisk")
                    if maxdisk:
                        disks[f"{node}/{g['vmid']}"] = int(maxdisk)
        return disks

    # --- stats (6.3): node CPU/RAM joins host stats ----------------------

    async def node_stats(self) -> dict[str, dict]:
        """Per-node load/mem for the status `hosts` section. Keyed by node name;
        the wiring layer namespaces it under the provider id. Best-effort — a
        node that errors is simply omitted rather than failing the whole poll."""
        nodes = await self._data("GET", f"{API}/nodes") or []
        out: dict[str, dict] = {}
        for n in nodes:
            node = n["node"]
            try:
                st = await self._data("GET", f"{API}/nodes/{_node(node)}/status") or {}
            except (RuntimeError, ValueError):
                continue
            mem = st.get("memory") or {}
            loadavg = st.get("loadavg") or []
            out[node] = {
                "load_1m": float(loadavg[0]) if len(loadavg) > 0 else 0.0,
                "load_5m": float(loadavg[1]) if len(loadavg) > 1 else 0.0,
                "load_15m": float(loadavg[2]) if len(loadavg) > 2 else 0.0,
                "mem_total": int(mem.get("total", 0)),
                "mem_used": int(mem.get("used", 0)),
                "disk_total": int((st.get("rootfs") or {}).get("total", 0)) or None,
                "disk_used": int((st.get("rootfs") or {}).get("used", 0)) or None,
            }
        return out
