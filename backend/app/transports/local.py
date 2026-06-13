"""LocalTransport — run commands on the backend's own machine, no SSH.

Lets the backend manage the host it runs on directly. Same CommandResult
contract as SSHTransport; commands are shell strings, run via the shell so the
provider command-builders (which already shlex-quote their arguments) work
unchanged.
"""

import asyncio

from app.transports.base import CommandResult


class LocalTransport:
    label = "local"

    async def run(self, command: str, timeout: float = 15.0) -> CommandResult:
        proc = await asyncio.create_subprocess_shell(
            command,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        except TimeoutError:
            # Mirror SSHTransport: the command was already running, so don't
            # retry — kill it and surface the timeout.
            proc.kill()
            await proc.wait()
            return CommandResult(
                stdout="",
                stderr=f"local: command exceeded {timeout:.0f}s",
                exit_code=255,
            )
        return CommandResult(
            stdout=stdout.decode(errors="replace"),
            stderr=stderr.decode(errors="replace"),
            exit_code=proc.returncode if proc.returncode is not None else 255,
        )
