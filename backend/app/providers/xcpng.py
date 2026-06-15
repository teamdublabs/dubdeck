"""XCP-ng provider — XenAPI XML-RPC over HTTPS.

Direct connection to XCP-ng XenAPI (port 443) using Python's xmlrpc.client
module.  No `xe` CLI binary, no SSH tunnel — the provider makes raw XML-RPC
calls over HTTPS to the XenAPI endpoint.

Authentication: session.login_with_password → session reference used in all
subsequent calls.  The session ref is cached and reused until it expires or
the provider is torn down.

This is the recommended approach for any machine that can reach the XCP-ng
host's port 443 directly.  Treadstone-engine can reach Mars (192.168.0.156)
on 443, so no SSH or `xe` binary is needed.

Snapshot restore/delete are deliberately absent — same boundary as every
other provider.  Destructive ops stay manual.
"""

import asyncio
import ssl
import xmlrpc.client
from urllib.parse import urlparse

from app.httpclient import HttpClient
from app.providers.base import (
    Capability,
    Provider,
    Resource,
    ResourceKind,
    ResourceState,
    Snapshot,
)


class XenAPISession:
    """Wraps an xmlrpc.client.ServerProxy with session management.

    Handles login, call forwarding, and logout.  All XenAPI calls return
    a {"Status": "Success"|"Failure", "Value": ..., "ErrorDescription": ...}
    dict.  We unwrap the Status/Value envelope and raise on Failure.
    """

    def __init__(self, url: str, username: str, password: str, verify_ssl: bool = False):
        ctx = ssl.create_default_context()
        if not verify_ssl:
            ctx.check_hostname = False
            ctx.verify_mode = ssl.CERT_NONE

        self._url = url.rstrip("/")
        self._username = username
        self._password = password
        self._session: str | None = None
        self._proxy = xmlrpc.client.ServerProxy(self._url + "/", context=ctx, allow_none=True)
        self._logged_in = False

    def _call(self, method: str, *args) -> dict:
        """Make a raw XenAPI call, returning the full response dict."""
        return getattr(self._proxy, method)(*args)

    def _call_single(self, method: str, *args):
        """Call a xenapi method, unwrap Status/Value, raise on Failure."""
        resp = self._call(method, *args)
        if resp.get("Status") == "Failure":
            raise RuntimeError("xenapi " + method + " failed: " + str(resp.get("ErrorDescription", [])))
        return resp.get("Value")

    def login(self) -> None:
        if self._logged_in:
            return
        self._session = self._call_single(
            "session.login_with_password", self._username, self._password
        )
        self._logged_in = True

    def logout(self) -> None:
        if not self._logged_in or self._session is None:
            return
        try:
            self._call("session.logout", self._session)
        except Exception:
            pass
        self._session = None
        self._logged_in = False

    def __enter__(self):
        self.login()
        return self

    def __exit__(self, *args):
        self.logout()

    @property
    def xenapi(self):
        """Return a session-wrapped proxy so callers can do: session.xenapi.VM.get_all(...)"""
        return XenAPIWrapper(self, self._session)

    def call(self, method_path: str, *args):
        """Call a method on a specific xenapi object.

        Example: session.call("VM.get_all_records")
        """
        return self._call_single(method_path, self._session, *args)


class XenAPIWrapper:
    """Holds the session ref so XenAPI calls carry it implicitly."""

    def __init__(self, parent: XenAPISession, session: str):
        self._parent = parent
        self._session = session
        self._cache: dict[str, object] = {}

    def __getattr__(self, name: str):
        return XenAPIObject(self._parent, self._session, name)


class XenAPIObject:
    """Proxies `session.xenapi.VM` style access with the session ref threaded through."""

    def __init__(self, parent: XenAPISession, session: str, name: str):
        self._parent = parent
        self._session = session
        self._name = name

    def __getattr__(self, method: str):
        return XenAPIMethod(self._parent, self._session, self._name, method)


class XenAPIMethod:
    def __init__(self, parent: XenAPISession, session: str, obj: str, method: str):
        self._parent = parent
        self._session = session
        self._obj = obj
        self._method = method

    def __call__(self, *args):
        # All xenapi calls take (session, ...) first
        full_method = f"{self._obj}.{self._method}"
        return self._parent._call_single(full_method, self._session, *args)


# ── State mapping ─────────────────────────────────────────────────────────────

_STATE_MAP = {
    "Running": ResourceState.RUNNING,
    "Halted": ResourceState.STOPPED,
    "Suspended": ResourceState.SUSPENDED,
    "Paused": ResourceState.PAUSED,
    "Crashdumped": ResourceState.STOPPED,
}


def _map_state(raw: str) -> ResourceState:
    return _STATE_MAP.get(raw, ResourceState.UNKNOWN)


# ── Provider ──────────────────────────────────────────────────────────────────

class XCPNgProvider(Provider):
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
            Capability.CONSOLE,
        }
    )

    def __init__(
        self,
        instance_id: str,
        client: HttpClient,
        *,
        host: str,
        username: str = "root",
        password: str,
        verify_ssl: bool = False,
        poll_interval: float = 1.0,
    ):
        self.instance_id = instance_id
        self._client = client
        self._host = host
        self._username = username
        self._password = password
        self._verify_ssl = verify_ssl
        self._poll = poll_interval
        self._sess: XenAPISession | None = None

    def _session(self) -> XenAPISession:
        if self._sess is None:
            self._sess = XenAPISession(
                f"https://{self._host}",
                self._username,
                self._password,
                self._verify_ssl,
            )
            self._sess.login()
        return self._sess

    async def _async_session(self) -> XenAPISession:
        # Called from async context — XenAPISession is sync but lightweight
        # (just a TCP connection).  Wrap in executor to avoid blocking the loop.
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(None, self._session().login)
        return self._sess

    async def list_resources(self) -> list[Resource]:
        sess = await self._async_session()
        loop = asyncio.get_event_loop()
        records = await loop.run_in_executor(None, lambda: sess.xenapi.VM.get_all_records())
        resources = []
        for ref, rec in records.items():
            if rec.get("is_a_template", False):
                continue
            resources.append(
                Resource(
                    id=ref,
                    name=rec.get("name_label", ref),
                    kind=ResourceKind.VM,
                    state=_map_state(rec.get("power_state", "")),
                )
            )
        return resources

    async def _vm_action(self, rid: str, action: str, timeout: float) -> None:
        sess = await self._async_session()
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(
            None,
            lambda: getattr(sess.xenapi.VM, action)(rid),
        )

    async def start(self, rid: str, timeout: float = 90.0) -> None:
        await self._vm_action(rid, "start", timeout)

    async def stop(self, rid: str, timeout: float = 180.0) -> None:
        await self._vm_action(rid, "shutdown", timeout)

    async def force_stop(self, rid: str, timeout: float = 180.0) -> None:
        await self._vm_action(rid, "hard_shutdown", timeout)

    async def restart(self, rid: str, timeout: float = 180.0) -> None:
        await self._vm_action(rid, "reboot", timeout)

    async def suspend(self, rid: str, timeout: float = 180.0) -> None:
        await self._vm_action(rid, "suspend", timeout)

    async def snapshot_list(self, rid: str) -> list[Snapshot]:
        sess = await self._async_session()
        loop = asyncio.get_event_loop()
        snap_refs = await loop.run_in_executor(None, lambda: sess.xenapi.VM.get_snapshots(rid))
        snaps = []
        for sref in snap_refs:
            try:
                rec = await loop.run_in_executor(
                    None, lambda sr=sref: sess.xenapi.Snapshot.get_record(sr)
                )
                snaps.append(
                    Snapshot(
                        name=rec.get("name_label", sref),
                        created=str(rec.get("snapshot_time", "")),
                        current=(rec.get("snapshot_of", "") == rid),
                    )
                )
            except Exception:
                snaps.append(Snapshot(name=sref, created="", current=False))
        return snaps

    async def snapshot_create(self, rid: str, name: str, timeout: float = 300.0) -> None:
        sess = await self._async_session()
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(
            None,
            lambda: sess.xenapi.VM.snapshot(rid, name),
        )

    async def logs(self, rid: str, n: int = 200) -> str:
        # XCP-ng doesn't have a shell-accessible console log.  Return the
        # console VNC URI as a proxy — the UI can use this to open a console.
        sess = await self._async_session()
        loop = asyncio.get_event_loop()
        try:
            console = await loop.run_in_executor(
                None, lambda: sess.xenapi.VM.get_console(rid)
            )
            uri = await loop.run_in_executor(
                None, lambda: sess.xenapi.console.get_location(console)
            )
            return uri
        except Exception as e:
            return f"(console unavailable: {e})"

    async def console(self, rid: str) -> str:
        """Return the VNC/RDP console URL for a VM.

        Queries XenAPI VM.get_console and returns the location string —
        typically an HTML5 console URL of the form:
            https://<xcp-ng-host>/consoles/<vm-uuid>/
        or a direct vnc://<host>:<port> URI.
        """
        sess = await self._async_session()
        loop = asyncio.get_event_loop()
        console_ref = await loop.run_in_executor(
            None, lambda: sess.xenapi.VM.get_console(rid)
        )
        return await loop.run_in_executor(
            None, lambda: sess.xenapi.console.get_location(console_ref)
        )

    async def disk_stats(self) -> dict[str, int]:
        sess = await self._async_session()
        loop = asyncio.get_event_loop()
        records = await loop.run_in_executor(None, lambda: sess.xenapi.VM.get_all_records())
        disks: dict[str, int] = {}
        for ref, rec in records.items():
            if rec.get("is_a_template", False):
                continue
            vdis = rec.get("VBDs", [])
            for vbd_ref in vdis:
                try:
                    vbd_rec = await loop.run_in_executor(
                        None, lambda r=vbd_ref: sess.xenapi.VBD.get_record(r)
                    )
                    vdi_ref = vbd_rec.get("VDI", "")
                    if not vdi_ref:
                        continue
                    vdi_rec = await loop.run_in_executor(
                        None, lambda r=vdi_ref: sess.xenapi.VDI.get_record(r)
                    )
                    size = vdi_rec.get("virtual_size", 0)
                    disks[f"{ref}/{vdi_ref}"] = size
                except Exception:
                    continue
        return disks