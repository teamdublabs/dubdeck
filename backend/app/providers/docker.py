"""Docker provider — containers on a host running the Docker CLI.

Pure command builders + parsers over captured `docker` output (see
tests/fixtures/docker_*). The first non-VM provider: kind=CONTAINER, no
snapshot/suspend, plus the LOGS capability the VM providers don't have.

State is read from the machine-readable `State` field (running/exited/paused/…),
NOT the human `Status` string. `Status` carries display noise — "Up 3 days
(healthy)", "Exited (143) 2 weeks ago" — that would force fragile prefix
parsing; `State` is a stable lowercase enum present since Docker 20.x. We keep
`Status` out of the state decision entirely.
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

# One JSON object per line (NOT a JSON array) — parse line-wise.
DOCKER_PS = "docker ps -a --format '{{json .}}'"

_STATES = {
    "running": ResourceState.RUNNING,
    "paused": ResourceState.PAUSED,
    "exited": ResourceState.STOPPED,
    "created": ResourceState.STOPPED,  # made but never started — effectively off
    "restarting": ResourceState.UNKNOWN,  # transient; the next poll resolves it
    "removing": ResourceState.UNKNOWN,
    "dead": ResourceState.UNKNOWN,
}


def parse_list(output: str) -> dict[str, ResourceState]:
    """`docker ps -a --format '{{json .}}'` → container name → state. The
    resource id is the container NAME (stable across restarts); container IDs
    change when a compose service is recreated."""
    states: dict[str, ResourceState] = {}
    for line in output.splitlines():
        line = line.strip()
        if not line:
            continue
        obj = json.loads(line)
        # `Names` is usually a single name; a container can carry several, comma-
        # joined — the first is the canonical one.
        name = obj["Names"].split(",")[0].strip()
        states[name] = _STATES.get(obj.get("State", "").lower(), ResourceState.UNKNOWN)
    return states


def start_command(rid: str) -> str:
    return f"docker start {shlex.quote(rid)}"


def stop_command(rid: str) -> str:
    # `docker stop` sends SIGTERM then SIGKILL after its own grace timeout — the
    # escalation libvirt needs is built in, so this provider is not "graceful".
    return f"docker stop {shlex.quote(rid)}"


def restart_command(rid: str) -> str:
    return f"docker restart {shlex.quote(rid)}"


def logs_command(rid: str, n: int) -> str:
    # `docker logs` splits app stdout/stderr across the two streams; merge them
    # (2>&1) so the viewer shows the real interleaved tail.
    return f"docker logs --tail {int(n)} {shlex.quote(rid)} 2>&1"


class DockerProvider(CommandProvider):
    type_name = "docker"
    capabilities = frozenset(
        {
            Capability.START,
            Capability.STOP,
            Capability.RESTART,
            Capability.LOGS,
        }
    )
    stop_is_graceful = False  # docker stop already does SIGTERM→SIGKILL itself

    async def list_resources(self) -> list[Resource]:
        result = await self._t.run(DOCKER_PS)
        if not result.ok:
            raise RuntimeError(result.stderr.strip() or f"exit {result.exit_code}")
        return [
            Resource(id=name, name=name, kind=ResourceKind.CONTAINER, state=state)
            for name, state in parse_list(result.stdout).items()
        ]

    async def start(self, rid: str, timeout: float = 90.0) -> None:
        await self._run_or_raise(start_command(rid), timeout)

    async def stop(self, rid: str, timeout: float = 180.0) -> None:
        await self._run_or_raise(stop_command(rid), timeout)

    async def restart(self, rid: str, timeout: float = 180.0) -> None:
        await self._run_or_raise(restart_command(rid), timeout)

    async def logs(self, rid: str, n: int = 200) -> str:
        result = await self._t.run(logs_command(rid, n), timeout=30.0)
        if not result.ok:
            raise RuntimeError(result.stderr.strip() or f"exit {result.exit_code}")
        return result.stdout
