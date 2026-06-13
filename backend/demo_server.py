"""Dubdeck demo server — the real backend against fictional infrastructure.

Runs every real service (status aggregation, ops, egress, settings) but over
FakeTransports instead of SSH, so it touches no real hosts. Seeded, reproducible
states make a clean desktop for screenshots and manual UI poking.

    cd backend && uv run python demo_server.py     # serves on 127.0.0.1:8042

Auth is disabled (loopback bind only, so the startup guard is satisfied) so it
lands straight on the desktop; the egress module is on so its panel renders. The
fictional config + canned command outputs are the same ones the test suite uses
(tests/fixtures), i.e. RFC 5737 addresses and made-up VM names — never real
infrastructure. Pair with `npm run dev` in frontend/ (Vite proxies /api here).
"""

import json
import os
import sqlite3
import tempfile
from pathlib import Path

FIXTURES = Path(__file__).parent / "tests" / "fixtures"

# Seed a throwaway DB BEFORE importing app.main, so its module-level DB_PATH (and
# the settings the startup reads) point at our seeded state.
_DB = str(Path(tempfile.mkdtemp(prefix="dubdeck-demo-")) / "demo.db")
os.environ["DUBDECK_DB"] = _DB
os.environ["DUBDECK_BIND"] = "127.0.0.1"
os.environ.setdefault("DUBDECK_LOG_LEVEL", "INFO")

EXIT_NODE_NONE = (FIXTURES / "tailscale_status_exitnode_null.json").read_text()


def _seed(key: str, value: object) -> None:
    conn = sqlite3.connect(_DB)
    conn.execute("CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
    conn.execute(
        "INSERT INTO settings (key, value) VALUES (?, ?) "
        "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        (key, json.dumps(value)),
    )
    conn.commit()
    conn.close()


_seed("auth.enabled", False)  # loopback bind -> guard satisfied; lands on desktop
_seed("modules.egress.enabled", True)  # render the egress panel
_seed("ui.branding.name", "Dubdeck")

from app.config import load_config  # noqa: E402
from app.main import app, wire  # noqa: E402
from app.transports import FakeTransport  # noqa: E402


def _build_transports() -> dict[str, FakeTransport]:
    """Mirror tests/conftest.py's canned, everything-stopped lab."""
    workstation = FakeTransport(label="workstation")
    workstation.respond(
        "prlctl list --all -o name,status", stdout=(FIXTURES / "prlctl_list.txt").read_text()
    )
    workstation.respond("dubdeck-stats", stdout=(FIXTURES / "mac_stats.txt").read_text())
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
    # Phase 5: Docker containers + compose stacks (same host as the KVM provider).
    server01.respond(
        "docker ps -a --format '{{json .}}'", stdout=(FIXTURES / "docker_ps.txt").read_text()
    )
    server01.respond(
        "docker compose ls -a --format json",
        stdout=(FIXTURES / "docker_compose_ls.txt").read_text(),
    )
    for name in ("web", "api", "cache", "importer", "batch", "edge.proxy", "migrator"):
        server01.respond(f"docker start {name}")
        server01.respond(f"docker stop {name}")
        server01.respond(f"docker restart {name}")
        server01.respond(
            f"docker logs --tail 200 {name} 2>&1",
            stdout=f"[demo] tail of {name}\nstarted ok\nlistening on :8080\n",
        )
    for stack in ("blog", "wiki", "monitoring"):
        server01.respond(f"cd /srv/stacks/{stack} && docker compose up -d")
        server01.respond(f"cd /srv/stacks/{stack} && docker compose down")
        server01.respond(f"cd /srv/stacks/{stack} && docker compose restart")

    relay_fw = FakeTransport(label="relay-fw")
    relay_fw.respond("tailscale status --json", stdout=EXIT_NODE_NONE)
    relay_fw.respond("tailscale set --exit-node=relay-01")
    relay_fw.respond("tailscale set --exit-node=")

    return {"workstation": workstation, "server01": server01, "relay-fw": relay_fw}


_CONFIG = Path(__file__).parent / "demo_config.yaml"
wire(app, load_config(_CONFIG), _build_transports())

if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="127.0.0.1", port=8042, log_level="info")
