"""Docker / Podman provider — containers on a host running the Docker CLI.

Pure command builders + parsers over captured `docker` or `podman` output (see
tests/fixtures/docker_*). The first non-VM provider: kind=CONTAINER, no
snapshot/suspend, plus the LOGS capability the VM providers don't have.

State is read from the machine-readable `State` field (running/exited/paused,…),
NOT the human `Status` string. `Status` carries display noise — "Up 3 days
(healthy)", "Exited (143) 2 weeks ago" — that would force fragile prefix
parsing; `State` is a stable lowercase enum present since Docker 20.x. We keep
`Status` out of the state decision entirely.

The `binary_name` constructor parameter allows the same class to serve both
`docker` (default) and `podman` providers — the binary name is substituted into
every command, so all methods work identically for both.
"""

import json
import shlex

from app.providers.base import (
    Capability,
    CommandProvider,
    Resource,
    ResourceKind,
    ResourceState,
)
from app.transports import Transport


class DockerProvider(CommandProvider):
    """Container provider using the Docker CLI. Subclassed by PodmanProvider
    with binary_name = \"podman\" for full podman support."""

    type_name = "docker"
    capabilities = frozenset(
        {
            Capability.START,
            Capability.STOP,
            Capability.RESTART,
            Capability.LOGS,
        }
    )
    stop_is_graceful = False  # docker/podman stop already does SIGTERM→SIGKILL itself

    # Binary name — \"docker\" by default, \"podman\" for the PodmanProvider subclass.
    # Subclasses can override this class attribute.
    binary_name: str = "docker"

    def __init__(self, instance_id: str, transport: Transport, binary_name: str | None = None):
        super().__init__(instance_id, transport)
        # Explicit binary_name from config takes precedence over the class default.
        self._binary = binary_name if binary_name is not None else self.binary_name

    # --- command builders (use self._binary so the same class works for podman) ---

    def _ps(self) -> str:
        return f"{self._binary} ps -a --format '{{{{json .}}}}'"

    def _start(self, rid: str) -> str:
        return f"{self._binary} start {shlex.quote(rid)}"

    def _stop(self, rid: str) -> str:
        return f"{self._binary} stop {shlex.quote(rid)}"

    def _restart(self, rid: str) -> str:
        return f"{self._binary} restart {shlex.quote(rid)}"

    def _logs(self, rid: str, n: int) -> str:
        # `docker logs` splits app stdout/stderr across two streams; merge them
        # (2>&1) so the viewer shows the real interleaved tail.
        return f"{self._binary} logs --tail {int(n)} {shlex.quote(rid)} 2>&1"

    async def list_resources(self) -> list[Resource]:
        result = await self._t.run(self._ps())
        if not result.ok:
            raise RuntimeError(result.stderr.strip() or f"exit {result.exit_code}")
        return [
            Resource(id=name, name=name, kind=ResourceKind.CONTAINER, state=state)
            for name, state in _parse_list(result.stdout).items()
        ]

    async def start(self, rid: str, timeout: float = 90.0) -> None:
        await self._run_or_raise(self._start(rid), timeout)

    async def stop(self, rid: str, timeout: float = 180.0) -> None:
        await self._run_or_raise(self._stop(rid), timeout)

    async def restart(self, rid: str, timeout: float = 180.0) -> None:
        await self._run_or_raise(self._restart(rid), timeout)

    async def logs(self, rid: str, n: int = 200) -> str:
        result = await self._t.run(self._logs(rid, n), timeout=30.0)
        if not result.ok:
            raise RuntimeError(result.stderr.strip() or f"exit {result.exit_code}")
        return result.stdout


def _parse_list(output: str) -> dict[str, ResourceState]:
    """`docker ps -a --format '{{json .}}'` → container name → state. The
    resource id is the container NAME (stable across restarts); container IDs
    change when a compose service is recreated."""
    states: dict[str, ResourceState] = {}
    for line in output.splitlines():
        line = line.strip()
        if not line:
            continue
        obj = json.loads(line)
        name = obj["Names"].split(",")[0].strip()
        states[name] = _STATES.get(obj.get("State", "").lower(), ResourceState.UNKNOWN)
    return states


_STATES = {
    "running": ResourceState.RUNNING,
    "paused": ResourceState.PAUSED,
    "exited": ResourceState.STOPPED,
    "created": ResourceState.STOPPED,  # made but never started — effectively off
    "restarting": ResourceState.UNKNOWN,  # transient; the next poll resolves it
    "removing": ResourceState.UNKNOWN,
    "dead": ResourceState.UNKNOWN,
}


class PodmanProvider(DockerProvider):
    """Podman provider — identical to DockerProvider but uses \"podman\" as the
    CLI binary. All methods, output parsing, and capabilities are shared."""

    type_name = "podman"
    binary_name = "podman"