"""Compose-stack provider — multi-container stacks managed by `docker compose`.

kind=STACK. A stack is one compose project living in `stacks_dir/<name>/` (the
Dockge layout). List comes from `docker compose ls`, filtered to projects whose
compose file lives under our `stacks_dir` so we never surface a stranger's
unrelated projects.

Ops `cd` into the stack directory and let `docker compose` discover the compose
file itself (`compose.yaml` OR `docker-compose.yml` — real deployments mix the
two), rather than hardcoding a filename in a `-f` path.

Path safety: a stack name flows straight into a `cd <dir>/<name>` path, so names
are validated against a strict allowlist BEFORE they ever touch a command line —
rejected outright, not merely shell-quoted (a quoted `../../x` is still a
traversal).
"""

import json
import re
import shlex

from app.providers.base import (
    Capability,
    CommandProvider,
    Resource,
    ResourceKind,
    ResourceState,
)

# Stacks show up here only when their compose file is under our stacks_dir.
# `-a` includes stopped stacks (default `ls` hides them).
COMPOSE_LS = "docker compose ls -a --format json"

# Compose project names are lowercase alnum + dash/underscore (Docker's own
# rule). Anything else is rejected before building a path/command.
STACK_NAME = re.compile(r"^[a-z0-9_-]+$")


def validate_name(name: str) -> str:
    if not STACK_NAME.match(name):
        raise ValueError(f"invalid stack name {name!r} — must match {STACK_NAME.pattern}")
    return name


def _stack_state(status: str) -> ResourceState:
    """`docker compose ls` Status is like `running(2)`, `exited(3)`, or mixed
    `running(1), exited(1)`. Any running service ⇒ the stack is up."""
    return ResourceState.RUNNING if "running(" in status else ResourceState.STOPPED


def parse_list(output: str, stacks_dir: str) -> dict[str, ResourceState]:
    """`docker compose ls -a --format json` (a JSON array) → stack name → state,
    keeping only projects whose compose file lives under stacks_dir."""
    if not output.strip():
        return {}
    prefix = stacks_dir.rstrip("/") + "/"
    states: dict[str, ResourceState] = {}
    for project in json.loads(output):
        # ConfigFiles can list several comma-joined files; the first is the
        # primary compose file and the one that pins the project's directory.
        config_file = project.get("ConfigFiles", "").split(",")[0].strip()
        if not config_file.startswith(prefix):
            continue
        states[project["Name"]] = _stack_state(project.get("Status", ""))
    return states


def stack_dir(stacks_dir: str, name: str) -> str:
    return f"{stacks_dir.rstrip('/')}/{validate_name(name)}"


def _in_stack(stacks_dir: str, name: str, verb: str) -> str:
    # cd into the stack dir so `docker compose` auto-discovers compose.yaml or
    # docker-compose.yml — deployments mix both, and Dockge writes compose.yaml.
    return f"cd {shlex.quote(stack_dir(stacks_dir, name))} && docker compose {verb}"


def up_command(stacks_dir: str, name: str) -> str:
    return _in_stack(stacks_dir, name, "up -d")


def down_command(stacks_dir: str, name: str) -> str:
    return _in_stack(stacks_dir, name, "down")


def restart_command(stacks_dir: str, name: str) -> str:
    return _in_stack(stacks_dir, name, "restart")


class ComposeProvider(CommandProvider):
    type_name = "compose"
    capabilities = frozenset(
        {
            Capability.START,
            Capability.STOP,
            Capability.RESTART,
        }
    )
    stop_is_graceful = False  # `compose down` blocks until the stack is torn down

    def __init__(self, instance_id: str, transport, stacks_dir: str):
        super().__init__(instance_id, transport)
        self._stacks_dir = stacks_dir

    async def list_resources(self) -> list[Resource]:
        result = await self._t.run(COMPOSE_LS, timeout=30.0)
        if not result.ok:
            raise RuntimeError(result.stderr.strip() or f"exit {result.exit_code}")
        return [
            Resource(id=name, name=name, kind=ResourceKind.STACK, state=state)
            for name, state in parse_list(result.stdout, self._stacks_dir).items()
        ]

    async def start(self, rid: str, timeout: float = 90.0) -> None:
        await self._run_or_raise(up_command(self._stacks_dir, rid), timeout)

    async def stop(self, rid: str, timeout: float = 180.0) -> None:
        await self._run_or_raise(down_command(self._stacks_dir, rid), timeout)

    async def restart(self, rid: str, timeout: float = 180.0) -> None:
        await self._run_or_raise(restart_command(self._stacks_dir, rid), timeout)
