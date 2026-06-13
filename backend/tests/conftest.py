from pathlib import Path

import pytest

from app.config import Config, load_config
from app.db import Database
from app.transports import FakeTransport

FIXTURES = Path(__file__).parent / "fixtures"
CONFIG_FIXTURE = FIXTURES / "config.yaml"

EXIT_NODE_ACTIVE = '{"ExitNodeStatus": {"ID": "nDEADBEEF", "Online": true}}'
EXIT_NODE_NONE = (FIXTURES / "tailscale_status_exitnode_null.json").read_text()


@pytest.fixture
def config() -> Config:
    return load_config(CONFIG_FIXTURE)


# alias kept so older helpers reading `inventory` still work
@pytest.fixture
def inventory(config) -> Config:
    return config


@pytest.fixture
async def db(tmp_path):
    database = Database(str(tmp_path / "test.db"))
    yield database
    await database.close()


@pytest.fixture
def transports() -> dict[str, FakeTransport]:
    """One FakeTransport per host, pre-canned with a healthy, everything-stopped
    lab. Keyed by host id — providers and egress share their host's transport."""
    workstation = FakeTransport(label="workstation")
    workstation.respond(
        "prlctl list --all -o name,status", stdout=(FIXTURES / "prlctl_list.txt").read_text()
    )
    workstation.respond("dubdeck-stats", stdout=(FIXTURES / "mac_stats.txt").read_text())
    # Parallels gateways: tailscale reached via prlctl exec on the host channel.
    for vm in ("gateway-01", "gateway-03"):
        workstation.respond(f"prlctl exec {vm} tailscale status --json", stdout=EXIT_NODE_NONE)
        workstation.respond(f"prlctl exec {vm} tailscale set --exit-node=relay-01")
        workstation.respond(f"prlctl exec {vm} tailscale set --exit-node=")

    server01 = FakeTransport(label="server01")
    server01.respond("virsh list --all", stdout=(FIXTURES / "virsh_list.txt").read_text())
    server01.respond(
        "cat /proc/loadavg && free -b && df -kP /",
        stdout=(FIXTURES / "linux_stats.txt").read_text(),
    )

    # The KVM gateway, reached directly over the network for egress commands.
    relay_fw = FakeTransport(label="relay-fw")
    relay_fw.respond("tailscale status --json", stdout=EXIT_NODE_NONE)
    relay_fw.respond("tailscale set --exit-node=relay-01")
    relay_fw.respond("tailscale set --exit-node=")

    return {"workstation": workstation, "server01": server01, "relay-fw": relay_fw}
