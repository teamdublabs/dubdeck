"""SSHTransport — real remote execution over a reused asyncssh connection.

Host-bound: one transport owns one connection to one host (the old
AsyncSSHRunner pooled connections across targets; per-host transports make that
bookkeeping unnecessary). The stale-retry / never-retry-timeout / keepalive
behaviour below encodes real incidents — do not simplify it away.
"""

import asyncio
import os

import asyncssh

from app.transports.base import CommandResult


class SSHTransport:
    """Real transport. Reuses one connection to the host; reconnects on failure."""

    def __init__(
        self,
        address: str,
        user: str,
        port: int = 22,
        key_path: str | None = None,
        known_hosts: str | None = None,
    ):
        self._address = address
        self._user = user
        self._port = port
        self._key_path = key_path or os.environ.get("DUBDECK_SSH_KEY", "/run/secrets/ssh_key")
        # Host keys are pinned via a mounted known_hosts file; None would accept anything.
        self._known_hosts = known_hosts or os.environ.get(
            "DUBDECK_KNOWN_HOSTS", "/run/secrets/known_hosts"
        )
        self._conn: asyncssh.SSHClientConnection | None = None
        self._lock = asyncio.Lock()

    @property
    def label(self) -> str:
        return f"{self._user}@{self._address}:{self._port}"

    async def _connect(self, *, fresh: bool = False) -> asyncssh.SSHClientConnection:
        async with self._lock:
            conn = self._conn
            if conn is not None and not fresh and not conn.is_closed():
                return conn
            if conn is not None:  # drop the stale/closed one before replacing
                conn.close()
            conn = await asyncssh.connect(
                self._address,
                port=self._port,
                username=self._user,
                client_keys=[self._key_path],
                known_hosts=self._known_hosts,
                connect_timeout=10,
                # Keep idle connections from being silently reaped by the remote
                # sshd; without this, the first reuse after an idle gap fails.
                keepalive_interval=15,
                keepalive_count_max=3,
            )
            self._conn = conn
            return conn

    async def run(self, command: str, timeout: float = 15.0) -> CommandResult:
        # A reused connection can be stale (idle-reaped by the remote) even when
        # is_closed() is still False — retry once on a fresh connection before
        # surfacing an error, so a stale socket never reaches the UI.
        #
        # A TimeoutError is different: the command was already running when we
        # gave up on it, so retrying would run a (possibly mutating) command a
        # second time. Never retry a timeout — surface it, don't double-execute.
        last_exc: Exception | None = None
        for attempt in range(2):
            try:
                conn = await self._connect(fresh=attempt == 1)
                result = await asyncio.wait_for(conn.run(command), timeout=timeout)
            except TimeoutError:
                self._conn = None
                return CommandResult(
                    stdout="",
                    stderr=f"ssh {self.label}: command exceeded {timeout:.0f}s",
                    exit_code=255,
                )
            except (OSError, asyncssh.Error) as exc:
                last_exc = exc
                self._conn = None
                continue
            return CommandResult(
                stdout=str(result.stdout or ""),
                stderr=str(result.stderr or ""),
                exit_code=result.exit_status if result.exit_status is not None else 255,
            )
        return CommandResult(stdout="", stderr=f"ssh {self.label}: {last_exc}", exit_code=255)

    async def close(self) -> None:
        if self._conn is not None:
            self._conn.close()
            self._conn = None
