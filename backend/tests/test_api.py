import json
import sqlite3

import pytest
from fastapi.testclient import TestClient

import app.main as main_module
from app.main import app, wire
from tests.conftest import FIXTURES

SETUP_PASSWORD = "test-password-123"

STATE_KEYS = (
    "db",
    "config",
    "settings",
    "auth",
    "transports",
    "providers",
    "ops",
    "egress",
    "status",
    "ops_svc",
    "stats",
)


def authenticate(test_client: TestClient) -> None:
    """Run the first-run setup so the client holds a valid session cookie. Every
    non-auth test exercises the real (auth-enabled) path through the middleware."""
    response = test_client.post("/api/setup", json={"password": SETUP_PASSWORD})
    assert response.status_code == 200


def seed_setting(db_path: str, key: str, value: object) -> None:
    """Persist a setting before the app boots, so settings.init() loads it and
    apply_modules() sees the toggle — the real "toggle persisted → module on at
    next start" path that startup module resolution relies on."""
    conn = sqlite3.connect(db_path)
    conn.execute("CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
    conn.execute(
        "INSERT INTO settings (key, value) VALUES (?, ?) "
        "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        (key, json.dumps(value)),
    )
    conn.commit()
    conn.close()


@pytest.fixture
def client(tmp_path, config, transports, monkeypatch):
    db_path = str(tmp_path / "test.db")
    monkeypatch.setattr(main_module, "DB_PATH", db_path)
    seed_setting(db_path, "modules.egress.enabled", True)  # egress route tests need it live
    wire(app, config, transports)
    # Shrink real-time waits so background ops (graceful-stop poll, ready-probe)
    # don't keep the suite waiting on wall-clock timers at TestClient shutdown.
    ops_svc = app.state.ops_svc
    ops_svc.STOP_GRACE, ops_svc.STOP_POLL = 0.02, 0.01
    ops_svc.READY_TIMEOUT, ops_svc.READY_POLL = 0.1, 0.01
    # 127.0.0.1 so requests pass the Host allowlist, like the real deployment.
    with TestClient(app, base_url="http://127.0.0.1") as test_client:
        authenticate(test_client)
        yield test_client
    for key in STATE_KEYS:
        if hasattr(app.state, key):
            delattr(app.state, key)


def test_health(client):
    assert client.get("/api/health").json() == {"status": "ok"}


def test_config_endpoint(client):
    body = client.get("/api/config").json()
    assert set(body["groups"]) == {"lab-03", "lab-01", "lab-02"}
    assert any(p["id"] == "host01-kvm" for p in body["providers"])


def test_settings_get_returns_defaults(client):
    body = client.get("/api/settings").json()
    assert body["auth.enabled"] is True
    assert body["ui.branding.name"] == "Dubdeck"


def test_settings_patch_updates_and_returns_all(client):
    body = client.patch("/api/settings", json={"ui.branding.name": "Homelab"}).json()
    assert body["ui.branding.name"] == "Homelab"
    assert client.get("/api/settings").json()["ui.branding.name"] == "Homelab"


def test_settings_patch_unknown_key_is_400(client):
    response = client.patch("/api/settings", json={"made.up.key": 1})
    assert response.status_code == 400
    assert "made.up.key" in response.json()["detail"]


def test_settings_patch_type_mismatch_is_400(client):
    response = client.patch("/api/settings", json={"auth.enabled": "yes"})
    assert response.status_code == 400


def test_status_endpoint(client):
    body = client.get("/api/status").json()
    fw2 = next(r for r in body["groups"]["lab-02"]["resources"] if r["id"] == "gateway-02")
    assert fw2["state"] == "running"


def test_resource_start_is_accepted_and_logs_in_background(client, transports):
    # lab-02's start_first (gateway-02) is already running → no ready-probe wait
    transports["server01"].respond("virsh start target-01")
    response = client.post("/api/resources/host01-kvm/target-01/start")
    assert response.status_code == 202
    assert response.json()["status"] == "starting"

    entries: list = []
    for _ in range(50):
        entries = client.get("/api/log", params={"action": "resource.start"}).json()
        if any(e["target"] == "host01-kvm/target-01" for e in entries):
            break
    assert any(e["target"] == "host01-kvm/target-01" for e in entries)


def test_resource_start_unknown_is_404(client):
    assert client.post("/api/resources/host01-kvm/ghost/start").status_code == 404


def test_egress_lifecycle_via_api(client):
    response = client.post("/api/groups/lab-01/egress", json={"duration_s": 600})
    assert response.status_code == 200
    assert response.json()["expires_at"] > 0
    assert client.delete("/api/groups/lab-01/egress").status_code == 200


def test_permanent_egress_is_409(client):
    assert client.post("/api/groups/lab-03/egress", json={}).status_code == 409


def test_egress_extend_via_api(client):
    first = client.post("/api/groups/lab-01/egress", json={"duration_s": 600}).json()
    extended = client.post("/api/groups/lab-01/egress/extend", json={"duration_s": 900})
    assert extended.status_code == 200
    assert extended.json()["expires_at"] > first["expires_at"]


def test_egress_extend_without_window_is_409(client):
    assert (
        client.post("/api/groups/lab-01/egress/extend", json={"duration_s": 900}).status_code == 409
    )


def test_egress_enable_reports_clamping(client):
    body = client.post("/api/groups/lab-01/egress", json={"duration_s": 999999}).json()
    assert body["clamped"] is True
    assert body["applied_duration_s"] == 4 * 3600


def test_unknown_egress_group_is_404(client):
    assert client.post("/api/groups/ghost/egress", json={}).status_code == 404


def test_foreign_host_header_is_rejected(client):
    response = client.get("/api/status", headers={"Host": "evil.com"})
    assert response.status_code == 403


def test_cross_site_fetch_is_rejected(client):
    response = client.post(
        "/api/resources/host02-parallels/pentest-vm/start", headers={"Sec-Fetch-Site": "cross-site"}
    )
    assert response.status_code == 403


def test_same_origin_fetch_is_allowed(client):
    response = client.get("/api/health", headers={"Sec-Fetch-Site": "same-origin"})
    assert response.status_code == 200


def test_group_bulk_start(client, transports):
    transports["server01"].respond("virsh start target-01")
    transports["server01"].respond("virsh start target-02")
    response = client.post("/api/groups/lab-02/start")
    assert response.status_code == 202
    assert set(response.json()["starting"]) == {"host01-kvm/target-01", "host01-kvm/target-02"}


def test_group_bulk_stop_accepted(client):
    response = client.post("/api/groups/lab-02/stop")
    assert response.status_code == 202
    assert response.json()["status"] == "stopping"


def test_unknown_group_bulk_is_404(client):
    assert client.post("/api/groups/ghost/start").status_code == 404


def test_dismiss_error(client):
    app.state.ops_svc._reg.errors["host01-kvm/target-02"] = "boom"
    assert client.delete("/api/errors/host01-kvm/target-02").status_code == 200
    assert client.delete("/api/errors/host01-kvm/target-02").status_code == 404


def test_log_filters(client):
    client.post("/api/groups/lab-01/egress", json={"duration_s": 600})
    client.delete("/api/groups/lab-01/egress")
    entries = client.get("/api/log", params={"action": "egress.revoke"}).json()
    assert entries and all(e["action"] == "egress.revoke" for e in entries)
    newest = client.get("/api/log").json()[0]
    older = client.get("/api/log", params={"before_id": newest["id"]}).json()
    assert all(e["id"] < newest["id"] for e in older)


def test_events_route_registered(client):
    assert any(getattr(r, "path", "") == "/api/events" for r in app.routes)


def test_suspend_endpoint(client, transports):
    transports["server01"].respond("virsh managedsave gateway-02")
    response = client.post("/api/resources/host01-kvm/gateway-02/suspend")
    assert response.status_code == 202
    assert response.json()["status"] == "suspending"
    assert client.post("/api/resources/host01-kvm/ghost/suspend").status_code == 404


def test_snapshot_endpoints(client, transports):
    transports["workstation"].respond(
        "prlctl snapshot-list pentest-vm -j",
        stdout=(FIXTURES / "prlctl_snapshot_list.json").read_text(),
    )
    snaps = client.get("/api/resources/host02-parallels/pentest-vm/snapshots").json()
    assert [s["name"] for s in snaps] == ["Clean", "updates"]

    transports["workstation"].respond("prlctl snapshot pentest-vm -n before-exploit")
    response = client.post(
        "/api/resources/host02-parallels/pentest-vm/snapshots", json={"name": "before-exploit"}
    )
    assert response.status_code == 202
    assert response.json()["name"] == "before-exploit"


def test_snapshot_name_validation(client):
    bad = client.post(
        "/api/resources/host02-parallels/pentest-vm/snapshots", json={"name": "rm -rf /"}
    )
    assert bad.status_code == 422


def test_resourcestats_endpoint(client, transports):
    transports["workstation"].respond(
        "dubdeck-vm-disks", stdout=(FIXTURES / "dubdeck_vm_disks.txt").read_text()
    )
    transports["server01"].respond(
        "virsh domstats --block --state",
        stdout=(FIXTURES / "virsh_domstats_block.txt").read_text(),
    )
    body = client.get("/api/resourcestats").json()
    assert body["resources"]["host02-parallels/pentest-vm"]["disk_bytes"] == 93914044 * 1024


# --- Phase 5: logs/restart routes ---


def test_logs_route_unsupported_on_vm_returns_404(client):
    # libvirt has no LOGS capability → the route maps Unsupported to 404.
    response = client.get("/api/resources/host01-kvm/gateway-02/logs")
    assert response.status_code == 404


def test_logs_route_unknown_provider_returns_404(client):
    response = client.get("/api/resources/ghost/x/logs")
    assert response.status_code == 404


@pytest.fixture
def docker_client(tmp_path, monkeypatch):
    """A second TestClient wired to a Docker provider in an auto: group, so the
    restart + logs routes can be exercised on a LOGS/RESTART-capable provider."""
    from app.config import Config
    from app.providers.docker import DOCKER_PS
    from app.transports import FakeTransport

    db_path = str(tmp_path / "docker.db")
    monkeypatch.setattr(main_module, "DB_PATH", db_path)
    seed_setting(db_path, "auth.enabled", False)  # loopback bind → guard satisfied
    config = Config.model_validate(
        {
            "hosts": {
                "edge": {"transport": "ssh", "address": "192.0.2.50", "user": "u", "stats": None}
            },
            "providers": [{"id": "edge-docker", "type": "docker", "host": "edge"}],
            "groups": {"containers": {"label": "Containers", "auto": "edge-docker"}},
        }
    )
    t = FakeTransport(label="edge")
    t.respond(DOCKER_PS, stdout=(FIXTURES / "docker_ps.txt").read_text())
    t.respond("docker restart web")
    t.respond("docker logs --tail 200 web 2>&1", stdout="log line A\nlog line B\n")
    wire(app, config, {"edge": t})
    with TestClient(app, base_url="http://127.0.0.1") as test_client:
        yield test_client
    for key in STATE_KEYS:
        if hasattr(app.state, key):
            delattr(app.state, key)


def test_docker_logs_route_returns_text(docker_client):
    response = docker_client.get("/api/resources/edge-docker/web/logs?n=200")
    assert response.status_code == 200
    assert response.text == "log line A\nlog line B\n"
    assert response.headers["content-type"].startswith("text/plain")


def test_docker_logs_route_clamps_n(docker_client):
    # n above the ceiling is clamped to 2000; the canned response is keyed on the
    # clamped command, so a hit proves the clamp ran.
    docker_client.app.state.transports["edge"].respond(
        "docker logs --tail 2000 web 2>&1", stdout="clamped\n"
    )
    response = docker_client.get("/api/resources/edge-docker/web/logs?n=999999")
    assert response.status_code == 200
    assert response.text == "clamped\n"


def test_docker_restart_route_accepts(docker_client):
    response = docker_client.post("/api/resources/edge-docker/web/restart")
    assert response.status_code == 202
    assert response.json()["status"] == "restarting"
