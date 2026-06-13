"""Module-boundary tests: the app works without the egress module, and core
never depends on it (one-way dependency)."""

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import app.main as main_module
from app.main import app, wire
from tests.test_api import STATE_KEYS, authenticate, seed_setting

APP_DIR = Path(__file__).parent.parent / "app"


@pytest.fixture
def no_egress_client(tmp_path, config, transports, monkeypatch):
    monkeypatch.setattr(main_module, "DB_PATH", str(tmp_path / "test.db"))
    cfg = config.model_copy(update={"modules": {}})  # drop the egress module
    wire(app, cfg, transports)
    with TestClient(app, base_url="http://127.0.0.1") as test_client:
        authenticate(test_client)
        yield test_client
    for key in STATE_KEYS:
        if hasattr(app.state, key):
            delattr(app.state, key)


@pytest.fixture
def egress_off_client(tmp_path, config, transports, monkeypatch):
    """Egress IS in config but the settings toggle is off (default) — the module
    must behave as if absent: no engine, no status section, routes 404."""
    monkeypatch.setattr(main_module, "DB_PATH", str(tmp_path / "test.db"))
    wire(app, config, transports)
    with TestClient(app, base_url="http://127.0.0.1") as test_client:
        authenticate(test_client)
        yield test_client
    for key in STATE_KEYS:
        if hasattr(app.state, key):
            delattr(app.state, key)


def test_app_boots_and_serves_status_without_egress(no_egress_client):
    assert app.state.egress is None
    body = no_egress_client.get("/api/status").json()
    assert set(body["groups"]) == {"lab-03", "lab-01", "lab-02"}
    assert body["modules"] == {}  # no module contributed a section


def test_egress_routes_404_when_module_disabled(no_egress_client):
    assert no_egress_client.post("/api/groups/lab-01/egress", json={}).status_code == 404


def test_egress_configured_but_toggle_off_behaves_as_absent(egress_off_client):
    # Configured in config.yaml, but modules.egress.enabled defaults to off.
    assert app.state.egress is None
    assert egress_off_client.get("/api/status").json()["modules"] == {}
    assert egress_off_client.post("/api/groups/lab-01/egress", json={}).status_code == 404


def test_egress_toggle_on_activates_module(tmp_path, config, transports, monkeypatch):
    db_path = str(tmp_path / "test.db")
    monkeypatch.setattr(main_module, "DB_PATH", db_path)
    seed_setting(db_path, "modules.egress.enabled", True)
    wire(app, config, transports)
    try:
        with TestClient(app, base_url="http://127.0.0.1") as test_client:
            authenticate(test_client)
            assert app.state.egress is not None
            assert "egress" in test_client.get("/api/status").json()["modules"]
    finally:
        for key in STATE_KEYS:
            if hasattr(app.state, key):
                delattr(app.state, key)


def test_core_does_not_import_egress_module():
    """Core packages must not import modules.egress — the dependency is one-way
    (main.py, the composition root, is the only allowed importer)."""
    core = ["config.py", "transports", "providers", "services"]
    offenders = []
    for target in core:
        path = APP_DIR / target
        files = path.rglob("*.py") if path.is_dir() else [path]
        for f in files:
            if "modules.egress" in f.read_text() or "modules import egress" in f.read_text():
                offenders.append(str(f))
    assert not offenders, f"core imports egress module: {offenders}"
