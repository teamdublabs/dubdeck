"""Provider model: Resource / Capability / state, the Provider contract, and
the CommandProvider base shared by every transport-backed provider.

A Provider is one instance of a provider type (parallels, libvirt, docker, …)
bound to a host or URL. It declares a fixed `capabilities` set; the contract is
that a declared capability's method works and an undeclared one raises
Unsupported (the contract suite in tests/contract enforces both directions). The
UI renders only the actions a provider declares.
"""

from dataclasses import dataclass
from enum import StrEnum
from typing import ClassVar

from app.transports import Transport


class ResourceKind(StrEnum):
    VM = "vm"
    CONTAINER = "container"
    STACK = "stack"


class ResourceState(StrEnum):
    # Superset of the old VMState — adds nothing today, but containers/stacks
    # map onto the same vocabulary so the UI has one state model.
    RUNNING = "running"
    STOPPED = "stopped"
    SUSPENDED = "suspended"
    PAUSED = "paused"
    UNKNOWN = "unknown"


class Capability(StrEnum):
    START = "start"
    STOP = "stop"
    FORCE_STOP = "force_stop"
    SUSPEND = "suspend"
    RESTART = "restart"
    SNAPSHOT_LIST = "snapshot_list"
    SNAPSHOT_CREATE = "snapshot_create"
    LOGS = "logs"
    DISK_STATS = "disk_stats"
    CONSOLE = "console"


# Maps each capability to the Provider method that implements it. The contract
# suite uses this to check declared-capabilities ↔ working-methods both ways.
CAPABILITY_METHODS: dict[Capability, str] = {
    Capability.START: "start",
    Capability.STOP: "stop",
    Capability.FORCE_STOP: "force_stop",
    Capability.SUSPEND: "suspend",
    Capability.RESTART: "restart",
    Capability.SNAPSHOT_LIST: "snapshot_list",
    Capability.SNAPSHOT_CREATE: "snapshot_create",
    Capability.LOGS: "logs",
    Capability.DISK_STATS: "disk_stats",
    Capability.CONSOLE: "console",
}


@dataclass(frozen=True)
class Resource:
    id: str  # unique within the provider instance (a name or node/vmid pair)
    name: str
    kind: ResourceKind
    state: ResourceState


@dataclass(frozen=True)
class Snapshot:
    name: str
    created: str
    current: bool = False


class Unsupported(Exception):
    """A capability not in the provider's declared set was invoked."""


class Provider:
    """Interface every provider implements. Capability methods default to
    raising Unsupported; a provider overrides exactly the ones it declares.

    Subclasses set `type_name` and `capabilities` as class vars and take their
    `instance_id` from config (e.g. "server01-kvm").
    """

    type_name: ClassVar[str] = "base"
    capabilities: ClassVar[frozenset[Capability]] = frozenset()
    instance_id: str

    def supports(self, cap: Capability) -> bool:
        return cap in self.capabilities

    # --- mandatory: every provider lists its resources ---
    async def list_resources(self) -> list[Resource]:
        raise NotImplementedError

    # --- capability-gated; base raises Unsupported, declared caps override ---
    async def start(self, rid: str, timeout: float = 90.0) -> None:
        raise Unsupported(f"{self.type_name}: start not supported")

    async def stop(self, rid: str, timeout: float = 180.0) -> None:
        raise Unsupported(f"{self.type_name}: stop not supported")

    async def force_stop(self, rid: str, timeout: float = 180.0) -> None:
        raise Unsupported(f"{self.type_name}: force_stop not supported")

    async def suspend(self, rid: str, timeout: float = 180.0) -> None:
        raise Unsupported(f"{self.type_name}: suspend not supported")

    async def restart(self, rid: str, timeout: float = 180.0) -> None:
        raise Unsupported(f"{self.type_name}: restart not supported")

    async def snapshot_list(self, rid: str) -> list[Snapshot]:
        raise Unsupported(f"{self.type_name}: snapshot_list not supported")

    async def snapshot_create(self, rid: str, name: str, timeout: float = 300.0) -> None:
        raise Unsupported(f"{self.type_name}: snapshot_create not supported")

    async def logs(self, rid: str, n: int = 200) -> str:
        raise Unsupported(f"{self.type_name}: logs not supported")

    async def disk_stats(self) -> dict[str, int]:
        """resource-id → disk footprint in bytes, for the resources this
        provider sees. Host-level call (one command), not per-resource."""
        raise Unsupported(f"{self.type_name}: disk_stats not supported")

    async def console(self, rid: str) -> str:
        """Return a URL to open the VM console (VNC/RDP/HTML5)."""
        raise Unsupported(f"{self.type_name}: console not supported")


class CommandProvider(Provider):
    """Base for providers backed by a Transport (parallels, libvirt, docker,
    compose, hyperv). Holds the host-bound transport; subclasses build commands
    and parse output via their own pure functions."""

    def __init__(self, instance_id: str, transport: Transport):
        self.instance_id = instance_id
        self._t = transport

    async def _run_or_raise(self, command: str, timeout: float):
        result = await self._t.run(command, timeout)
        if not result.ok:
            raise RuntimeError(result.stderr.strip() or f"exit {result.exit_code}")
        return result
