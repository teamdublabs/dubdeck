import asyncio

import asyncssh
import pytest

from app.transports import CommandResult, FakeTransport, LocalTransport, SSHTransport

# --- FakeTransport (the test double everything else uses) ---


async def test_fake_transport_returns_canned_response_and_records_call():
    fake = FakeTransport()
    fake.respond("virsh list --all", stdout="output here")
    result = await fake.run("virsh list --all")
    assert result == CommandResult(stdout="output here")
    assert result.ok
    assert fake.calls == ["virsh list --all"]


async def test_fake_transport_rejects_unexpected_commands():
    fake = FakeTransport()
    with pytest.raises(LookupError, match="no canned response"):
        await fake.run("rm -rf /")


async def test_fake_transport_sequence_then_static_fallback():
    fake = FakeTransport()
    fake.respond("virsh list --all", stdout="steady")
    fake.respond_seq(
        "virsh list --all",
        [CommandResult(stdout="booting"), CommandResult(stdout="up")],
    )
    assert (await fake.run("virsh list --all")).stdout == "booting"
    assert (await fake.run("virsh list --all")).stdout == "up"
    assert (await fake.run("virsh list --all")).stdout == "steady"  # falls back


async def test_failed_result_is_not_ok():
    fake = FakeTransport()
    fake.respond("virsh start ghost", stderr="not found", exit_code=1)
    result = await fake.run("virsh start ghost")
    assert not result.ok


# --- LocalTransport (real subprocess; runs on the test machine) ---


async def test_local_transport_runs_command():
    result = await LocalTransport().run("printf 'hello'")
    assert result.ok
    assert result.stdout == "hello"


async def test_local_transport_captures_nonzero_exit_and_stderr():
    result = await LocalTransport().run("printf 'boom' >&2; exit 3")
    assert not result.ok
    assert result.exit_code == 3
    assert result.stderr == "boom"


async def test_local_transport_times_out_without_double_running():
    result = await LocalTransport().run("sleep 5", timeout=0.05)
    assert not result.ok
    assert "exceeded" in result.stderr


# --- SSHTransport stale-retry / never-retry-timeout (incident-encoding) ---


class _StaleConn:
    """First run() raises (idle-reaped socket); a fresh connect must recover."""

    def __init__(self, fail_first: bool):
        self._fail = fail_first

    def is_closed(self) -> bool:
        return False

    def close(self) -> None:
        pass

    async def run(self, command: str):
        if self._fail:
            self._fail = False
            raise asyncssh.Error(code=1, reason="connection lost")

        class R:
            stdout = "recovered"
            stderr = ""
            exit_status = 0

        return R()


async def test_run_retries_once_on_stale_connection(monkeypatch):
    transport = SSHTransport("192.0.2.20", "labuser", key_path="/dev/null", known_hosts=None)
    conns = [_StaleConn(fail_first=True), _StaleConn(fail_first=False)]

    async def fake_connect(*args, **kwargs):
        return conns.pop(0)

    monkeypatch.setattr(asyncssh, "connect", fake_connect)
    result = await transport.run("virsh list --all")
    assert result.ok
    assert result.stdout == "recovered"  # second (fresh) connection succeeded


async def test_run_does_not_retry_on_timeout(monkeypatch):
    """A slow (possibly mutating) command must NOT be run a second time."""
    connects = 0

    class _SlowConn:
        def is_closed(self) -> bool:
            return False

        def close(self) -> None:
            pass

        async def run(self, command: str):
            await asyncio.sleep(10)  # never finishes before the timeout

    async def fake_connect(*args, **kwargs):
        nonlocal connects
        connects += 1
        return _SlowConn()

    monkeypatch.setattr(asyncssh, "connect", fake_connect)
    transport = SSHTransport("192.0.2.20", "labuser", key_path="/dev/null", known_hosts=None)
    result = await transport.run("prlctl stop windows-vm", timeout=0.05)
    assert not result.ok
    assert "exceeded" in result.stderr
    assert connects == 1  # the stop was attempted exactly once
