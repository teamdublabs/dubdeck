"""Egress engine — temporary internet for lab gateways via exit node.

Optional module (default-off; enable it in your deployment's settings). Every
enable has an expiry, revocation is server-side, and reconcile() on startup
guarantees no group stays online after a crash or restart. Permanent-egress
gateways are never touched.

A revoke that fails (gateway flapping) is NOT terminal: the
window row stays in SQLite and the periodic sweep retries until the revoke lands.

Config lives under `modules.egress` in config.yaml; the gateway VM identity is
core group policy (start_first), egress only adds how to reach tailscale on it
(prlctl exec on a host, or direct) plus the exit node and mode.
"""

import asyncio
import contextlib
import json
import logging
import shlex
import time
from typing import Any, Literal

from pydantic import BaseModel, model_validator

from app.db import Database
from app.opslog import OpsLog
from app.transports import CommandResult, SSHTransport, Transport

log = logging.getLogger(__name__)

TAILSCALE_STATUS = "tailscale status --json"
MAX_DURATION = 4 * 3600
SWEEP_INTERVAL = 30.0

_SCHEMA = """
CREATE TABLE IF NOT EXISTS egress_windows (
    grp TEXT PRIMARY KEY,
    gateway_vm TEXT NOT NULL,
    expires_at REAL NOT NULL
)
"""


class GatewayConfig(BaseModel):
    exit_node: str
    mode: Literal["on-demand", "permanent"] = "on-demand"
    # Reach exactly one way:
    #  - `exec_on` a core host: run `prlctl exec <vm> <cmd>` on its transport (the
    #    host channel keeps working when the VM's own tailnet path flaps).
    #  - `host` a core host: run <cmd> directly on that core host's transport.
    #  - `address` (+ user/port): a self-contained direct SSH target the module
    #    owns. Use this for a KVM gateway VM (no prlctl-exec channel) so it does
    #    NOT have to be modelled as a core host just to carry the transport.
    vm: str | None = None
    exec_on: str | None = None
    host: str | None = None
    address: str | None = None
    user: str | None = None
    port: int = 22

    @model_validator(mode="after")
    def validate_reach(self) -> "GatewayConfig":
        if sum(bool(x) for x in (self.exec_on, self.host, self.address)) != 1:
            raise ValueError("gateway: set exactly one of 'exec_on', 'host', or 'address'")
        if self.exec_on and not self.vm:
            raise ValueError("gateway: 'exec_on' requires 'vm'")
        if self.address and not self.user:
            raise ValueError("gateway: 'address' requires 'user'")
        return self


class EgressConfig(BaseModel):
    gateways: dict[str, GatewayConfig]  # keyed by group name


def _set_exit_node(node: str) -> str:
    return f"tailscale set --exit-node={node}"


def exit_node_active(status_json: str) -> bool:
    status = json.loads(status_json).get("ExitNodeStatus")
    return bool(status and status.get("ID"))


class EgressEngine:
    name = "egress"  # StatusContributor section name

    def __init__(
        self,
        db: Database,
        config: EgressConfig,
        host_transports: dict[str, Transport],
        ops: OpsLog,
    ):
        self._db = db
        self._config = config
        self._transports = host_transports
        # Self-built transports for gateways with an inline `address` — owned by
        # this engine (closed in close()), so they don't need a core host entry.
        self._direct: dict[str, Transport] = {
            group: SSHTransport(gw.address, gw.user, gw.port)
            for group, gw in config.gateways.items()
            if gw.address
        }
        self._ops = ops
        self._timers: dict[str, asyncio.Task] = {}
        self._sweeper: asyncio.Task | None = None

    async def init(self) -> None:
        await self._db.init()
        await self._db.execute(_SCHEMA)
        # Pre-v2 DBs have egress_windows keyed by `enclave`; CREATE IF NOT EXISTS
        # left that table untouched, so migrate it. The rows are transient
        # active-egress state — reconcile() re-derives truth from the gateways
        # themselves, so dropping a stale-shaped table loses nothing real.
        cols = await self._db.fetchall("PRAGMA table_info(egress_windows)")
        if cols and "grp" not in {c[1] for c in cols}:
            log.warning("migrating egress_windows from pre-v2 schema (enclave → grp)")
            await self._db.execute("DROP TABLE egress_windows")
            await self._db.execute(_SCHEMA)

    def _gateway(self, group: str) -> GatewayConfig:
        return self._config.gateways[group]

    def gateway_call(self, group: str, command: str) -> tuple[Transport, str]:
        gw = self._gateway(group)
        if gw.exec_on:
            return self._transports[gw.exec_on], f"prlctl exec {shlex.quote(gw.vm)} {command}"
        if gw.host:
            return self._transports[gw.host], command
        return self._direct[group], command

    async def _run_on_gateway(self, group: str, command: str) -> CommandResult:
        transport, wrapped = self.gateway_call(group, command)
        return await transport.run(wrapped)

    async def expires_at(self, group: str) -> float | None:
        rows = await self._db.fetchall(
            "SELECT expires_at FROM egress_windows WHERE grp = ?", (group,)
        )
        return rows[0][0] if rows else None

    async def enable(self, group: str, duration_s: int) -> float:
        gw = self._gateway(group)
        if gw.mode == "permanent":
            raise ValueError(f"{group}: egress is permanent, not toggleable")
        duration_s = min(duration_s, MAX_DURATION)
        result = await self._run_on_gateway(group, _set_exit_node(gw.exit_node))
        await self._ops.record(
            "egress.enable",
            group,
            result.ok,
            f"{duration_s}s via {gw.exit_node}" if result.ok else result.stderr,
        )
        if not result.ok:
            raise RuntimeError(f"enable failed on {group}: {result.stderr}")
        expires_at = time.time() + duration_s
        await self._db.execute(
            "INSERT OR REPLACE INTO egress_windows (grp, gateway_vm, expires_at) VALUES (?, ?, ?)",
            (group, gw.vm or group, expires_at),
        )
        log.info("egress enabled for %s, %ss", group, duration_s)
        self._schedule(group, duration_s)
        return expires_at

    async def extend(self, group: str, duration_s: int) -> float:
        current = await self.expires_at(group)
        now = time.time()
        if current is None or current <= now:
            raise ValueError(f"{group}: no active egress window to extend")
        expires_at = min(current + duration_s, now + MAX_DURATION)
        await self._db.execute(
            "UPDATE egress_windows SET expires_at = ? WHERE grp = ?", (expires_at, group)
        )
        await self._ops.record("egress.extend", group, True, f"+{duration_s}s → {expires_at:.0f}")
        self._schedule(group, expires_at - now)
        return expires_at

    async def revoke(self, group: str, reason: str = "manual") -> None:
        if timer := self._timers.pop(group, None):
            timer.cancel()
        result = await self._run_on_gateway(group, _set_exit_node(""))
        await self._ops.record(
            "egress.revoke", group, result.ok, reason if result.ok else result.stderr
        )
        if not result.ok:
            # Window row is intentionally left in place — the sweep retries.
            log.error("revoke failed on %s: %s", group, result.stderr)
            raise RuntimeError(f"REVOKE FAILED on {group} — check manually: {result.stderr}")
        await self._db.execute("DELETE FROM egress_windows WHERE grp = ?", (group,))
        log.info("egress revoked for %s (%s)", group, reason)

    def _schedule(self, group: str, delay_s: float) -> None:
        if timer := self._timers.pop(group, None):
            timer.cancel()

        async def expire() -> None:
            await asyncio.sleep(delay_s)
            self._timers.pop(group, None)
            with contextlib.suppress(RuntimeError):  # failed revoke → sweep retries
                await self.revoke(group, reason="expired")

        self._timers[group] = asyncio.create_task(expire())

    async def sweep(self) -> None:
        """Revoke any window past its expiry — the retry net for failed revokes."""
        rows = await self._db.fetchall("SELECT grp, expires_at FROM egress_windows")
        now = time.time()
        for group, expires in rows:
            if expires <= now:
                log.warning("sweep: overdue egress window for %s, revoking", group)
                with contextlib.suppress(RuntimeError):
                    await self.revoke(group, reason="sweep: overdue window")

    def start_sweeper(self, interval_s: float = SWEEP_INTERVAL) -> None:
        async def loop() -> None:
            while True:
                await asyncio.sleep(interval_s)
                try:
                    await self.sweep()
                except Exception:
                    log.exception("egress sweep failed")  # never let the net die

        self._sweeper = asyncio.create_task(loop())

    async def reconcile(self) -> None:
        """On startup: resume live windows, revoke anything orphaned or expired."""
        rows = await self._db.fetchall("SELECT grp, expires_at FROM egress_windows")
        windows = {row[0]: row[1] for row in rows}
        now = time.time()
        for group, gw in self._config.gateways.items():
            if gw.mode == "permanent":
                continue
            if group in windows and windows[group] > now:
                self._schedule(group, windows[group] - now)
                continue
            result = await self._run_on_gateway(group, TAILSCALE_STATUS)
            if result.ok and exit_node_active(result.stdout):
                if group not in windows:
                    await self._db.execute(
                        "INSERT OR REPLACE INTO egress_windows (grp, gateway_vm, expires_at)"
                        " VALUES (?, ?, ?)",
                        (group, gw.vm or group, now),
                    )
                with contextlib.suppress(RuntimeError):  # sweep retries overdue rows
                    await self.revoke(group, reason="reconcile: orphaned window")
            elif group in windows:
                await self._db.execute("DELETE FROM egress_windows WHERE grp = ?", (group,))

    async def status(self) -> dict[str, Any]:
        """StatusContributor: per-group egress state for the snapshot."""

        async def one(group: str, gw: GatewayConfig) -> tuple[str, dict]:
            result = await self._run_on_gateway(group, TAILSCALE_STATUS)
            internet: bool | None
            if not result.ok:
                internet = None  # gateway unreachable (likely powered off)
            else:
                try:
                    internet = exit_node_active(result.stdout)
                except ValueError:
                    internet = None
            return group, {
                "mode": gw.mode,
                "internet": internet,
                "expires_at": await self.expires_at(group),
            }

        results = await asyncio.gather(*(one(g, gw) for g, gw in self._config.gateways.items()))
        return dict(results)

    async def close(self) -> None:
        if self._sweeper:
            self._sweeper.cancel()
            self._sweeper = None
        for timer in self._timers.values():
            timer.cancel()
        self._timers.clear()
        for transport in self._direct.values():
            with contextlib.suppress(Exception):
                await transport.close()
        self._direct.clear()
