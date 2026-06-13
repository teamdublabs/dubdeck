"""Transports — host-bound command execution.

A Transport runs a command on one machine and returns a CommandResult. It is
bound to its host at construction, so callers pass only the command (contrast
the old SSHRunner, which took a target per call and pooled connections across
hosts). Providers own a transport; tests use FakeTransport, never real SSH.
"""

from app.transports.base import CommandResult, Transport
from app.transports.fake import FakeTransport
from app.transports.local import LocalTransport
from app.transports.ssh import SSHTransport

__all__ = [
    "CommandResult",
    "Transport",
    "SSHTransport",
    "LocalTransport",
    "FakeTransport",
]
