"""FakeTransport — the only transport tests ever use.

Keyed by command alone: a transport is already host-bound, so unlike the old
FakeRunner (keyed by address+command) there is no target to disambiguate. One
FakeTransport stands in for one host.
"""

from dataclasses import dataclass, field

from app.transports.base import CommandResult


@dataclass
class FakeTransport:
    """Test double: canned responses keyed by command, records every call."""

    responses: dict[str, CommandResult] = field(default_factory=dict)
    sequences: dict[str, list[CommandResult]] = field(default_factory=dict)
    calls: list[str] = field(default_factory=list)
    label: str = "fake"

    def respond(self, command: str, stdout: str = "", exit_code: int = 0, stderr: str = "") -> None:
        self.responses[command] = CommandResult(stdout, stderr, exit_code)

    def respond_seq(self, command: str, results: list[CommandResult]) -> None:
        """Consume `results` in order, then fall back to the static response —
        models state that changes between calls (a VM booting, a flap healing)."""
        self.sequences[command] = list(results)

    async def run(self, command: str, timeout: float = 15.0) -> CommandResult:
        self.calls.append(command)
        if self.sequences.get(command):
            return self.sequences[command].pop(0)
        if command not in self.responses:
            raise LookupError(f"FakeTransport({self.label}): no canned response for {command!r}")
        return self.responses[command]
