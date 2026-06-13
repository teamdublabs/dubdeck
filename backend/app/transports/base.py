"""Transport protocol + the CommandResult every transport returns."""

from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class CommandResult:
    stdout: str
    stderr: str = ""
    exit_code: int = 0

    @property
    def ok(self) -> bool:
        return self.exit_code == 0


class Transport(Protocol):
    """Run a command on this transport's host.

    Host-bound: the address/user/credentials are fixed at construction, so the
    only per-call input is the command itself. One transport per host; providers
    sharing a host share its transport (one connection, not one per provider).
    """

    async def run(self, command: str, timeout: float = 15.0) -> CommandResult: ...
