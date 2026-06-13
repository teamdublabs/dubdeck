"""Auth core — setup flow, login, sessions, rate limit, loopback check (Phase 3.2)."""

import pytest
from fastapi.testclient import TestClient

import app.main as main_module
from app.auth import (
    SESSION_COOKIE,
    AuthService,
    RateLimited,
    assert_auth_or_loopback,
    is_loopback_bind,
)
from app.main import app, wire
from tests.test_api import SETUP_PASSWORD, STATE_KEYS, authenticate


@pytest.fixture
def raw_client(tmp_path, config, transports, monkeypatch):
    """A client on a fresh DB with auth enabled and NO password set — i.e. the
    real first-run state. Tests drive setup/login themselves."""
    monkeypatch.setattr(main_module, "DB_PATH", str(tmp_path / "test.db"))
    wire(app, config, transports)
    with TestClient(app, base_url="http://127.0.0.1") as test_client:
        yield test_client
    for key in STATE_KEYS:
        if hasattr(app.state, key):
            delattr(app.state, key)


# ---- setup / first-run -----------------------------------------------------


def test_gated_route_403_until_setup(raw_client):
    assert raw_client.get("/api/status").status_code == 403


def test_auth_state_reports_unconfigured(raw_client):
    body = raw_client.get("/api/auth").json()
    assert body["enabled"] is True
    assert body["configured"] is False
    assert body["authenticated"] is False
    assert body["brand"] == "Dubdeck"  # default branding for the pre-login screen


def test_setup_sets_password_and_authenticates(raw_client):
    resp = raw_client.post("/api/setup", json={"password": SETUP_PASSWORD})
    assert resp.status_code == 200
    assert raw_client.cookies.get(SESSION_COOKIE)  # session issued
    # Now a gated route works on the same client (cookie carried).
    assert raw_client.get("/api/status").status_code == 200
    assert raw_client.get("/api/auth").json()["authenticated"] is True


def test_setup_rejects_short_password(raw_client):
    assert raw_client.post("/api/setup", json={"password": "short"}).status_code == 400


def test_setup_409_once_configured(raw_client):
    authenticate(raw_client)
    assert raw_client.post("/api/setup", json={"password": "another-one-99"}).status_code == 409


# ---- login / logout --------------------------------------------------------


def test_login_with_correct_password_authenticates(raw_client):
    authenticate(raw_client)
    raw_client.post("/api/logout")
    assert raw_client.get("/api/status").status_code == 401  # cookie cleared
    resp = raw_client.post("/api/login", json={"password": SETUP_PASSWORD})
    assert resp.status_code == 200
    assert raw_client.get("/api/status").status_code == 200


def test_login_wrong_password_401(raw_client):
    authenticate(raw_client)
    raw_client.post("/api/logout")
    assert raw_client.post("/api/login", json={"password": "wrong-password"}).status_code == 401


def test_logout_clears_session(raw_client):
    authenticate(raw_client)
    assert raw_client.get("/api/status").status_code == 200
    raw_client.post("/api/logout")
    assert raw_client.get("/api/status").status_code == 401


def test_login_rate_limited_after_five_attempts(raw_client):
    authenticate(raw_client)
    raw_client.post("/api/logout")
    for _ in range(5):
        assert raw_client.post("/api/login", json={"password": "nope"}).status_code == 401
    # 6th attempt in the window is throttled regardless of correctness.
    assert raw_client.post("/api/login", json={"password": SETUP_PASSWORD}).status_code == 429


def test_tampered_cookie_rejected(raw_client):
    authenticate(raw_client)
    raw_client.cookies.set(SESSION_COOKIE, "9999999999.deadbeef", domain="127.0.0.1")
    assert raw_client.get("/api/status").status_code == 401


def test_change_password_requires_current(raw_client):
    authenticate(raw_client)
    # Wrong current password is rejected even with a valid session.
    bad = raw_client.post("/api/auth/password", json={"current": "wrong", "new": "new-password-1"})
    assert bad.status_code == 403
    # Correct current rotates it; old password no longer logs in, new one does.
    ok = raw_client.post(
        "/api/auth/password", json={"current": SETUP_PASSWORD, "new": "new-password-1"}
    )
    assert ok.status_code == 200
    raw_client.post("/api/logout")
    assert raw_client.post("/api/login", json={"password": SETUP_PASSWORD}).status_code == 401
    assert raw_client.post("/api/login", json={"password": "new-password-1"}).status_code == 200


def test_change_password_invalidates_other_sessions(raw_client):
    authenticate(raw_client)
    stolen = raw_client.cookies.get(SESSION_COOKIE)  # a copy held by "another session"
    resp = raw_client.post(
        "/api/auth/password", json={"current": SETUP_PASSWORD, "new": "new-password-1"}
    )
    assert resp.status_code == 200
    # The current client keeps working — the guard re-issued a fresh cookie.
    assert raw_client.get("/api/status").status_code == 200
    # But the pre-change cookie is now worthless (signing secret rotated).
    raw_client.cookies.clear()
    raw_client.cookies.set(SESSION_COOKIE, stolen, domain="127.0.0.1")
    assert raw_client.get("/api/status").status_code == 401


async def test_rotate_session_secret_kills_old_tokens(db):
    auth = AuthService(db)
    await auth.init()
    token = auth.issue_token()
    assert auth.verify_token(token) is True
    await auth.rotate_session_secret()
    assert auth.verify_token(token) is False


def test_change_password_needs_session(raw_client):
    authenticate(raw_client)
    raw_client.post("/api/logout")
    resp = raw_client.post(
        "/api/auth/password", json={"current": SETUP_PASSWORD, "new": "new-password-1"}
    )
    assert resp.status_code == 401  # gated route, no session


def test_change_password_rejects_short_new(raw_client):
    authenticate(raw_client)
    resp = raw_client.post("/api/auth/password", json={"current": SETUP_PASSWORD, "new": "short"})
    assert resp.status_code == 400


def test_public_routes_reachable_without_session(raw_client):
    assert raw_client.get("/api/health").status_code == 200
    assert raw_client.get("/api/auth").status_code == 200


# ---- AuthService unit ------------------------------------------------------


async def test_password_hash_is_argon2id(db):
    auth = AuthService(db)
    await auth.init()
    await auth.set_password("correct-horse-battery")
    assert auth.verify_password("correct-horse-battery") is True
    assert auth.verify_password("wrong") is False
    assert auth._password_hash.startswith("$argon2id$")


async def test_session_secret_persists_across_restart(db):
    auth = AuthService(db)
    await auth.init()
    token = auth.issue_token()
    # A fresh service over the same DB loads the same secret → token still valid.
    restarted = AuthService(db)
    await restarted.init()
    assert restarted.verify_token(token) is True


async def test_expired_token_rejected(db):
    auth = AuthService(db)
    await auth.init()
    stale = auth.issue_token(now=0)  # exp far in the past
    assert auth.verify_token(stale) is False
    assert auth.verify_token(stale, now=0) is True  # was valid when issued


async def test_token_with_swapped_secret_rejected(db):
    auth = AuthService(db)
    await auth.init()
    token = auth.issue_token()
    auth._secret = b"different-secret"
    assert auth.verify_token(token) is False


async def test_rate_limit_is_per_ip(db):
    auth = AuthService(db)
    await auth.init()
    for _ in range(5):
        auth.check_rate("10.0.0.1")
    with pytest.raises(RateLimited):
        auth.check_rate("10.0.0.1")
    auth.check_rate("10.0.0.2")  # a different IP is unaffected


# ---- loopback startup check ------------------------------------------------


def test_auth_enabled_skips_loopback_check():
    assert_auth_or_loopback(True, "0.0.0.0")  # auth on → bind irrelevant, no raise


@pytest.mark.parametrize("bind", ["127.0.0.1", "::1", "localhost"])
def test_auth_disabled_allowed_on_loopback(bind):
    assert_auth_or_loopback(False, bind)  # loopback → no raise


@pytest.mark.parametrize("bind", ["0.0.0.0", "::", "", "192.168.0.10", "192.0.2.10"])
def test_auth_disabled_refused_off_loopback(bind):
    with pytest.raises(RuntimeError, match="loopback"):
        assert_auth_or_loopback(False, bind)


@pytest.mark.parametrize(
    ("bind", "loopback"),
    [
        ("127.0.0.1", True),
        ("::1", True),
        ("localhost", True),
        ("0.0.0.0", False),
        ("::", False),
        ("127.0.0.2", False),
        ("", False),
    ],
)
def test_is_loopback_bind_is_an_allowlist(bind, loopback):
    # 127.0.0.2 is technically loopback but fails closed — safe direction.
    assert is_loopback_bind(bind) is loopback


def test_patch_disabling_auth_refused_off_loopback(raw_client, monkeypatch):
    authenticate(raw_client)
    monkeypatch.setattr(main_module, "BIND", "0.0.0.0")
    resp = raw_client.patch("/api/settings", json={"auth.enabled": False})
    assert resp.status_code == 400
    assert "loopback" in resp.json()["detail"]
    # Auth is still on — a fresh unauthenticated client is still gated.
    raw_client.cookies.clear()
    assert raw_client.get("/api/status").status_code == 401


def test_patch_disabling_auth_allowed_on_loopback(raw_client, monkeypatch):
    authenticate(raw_client)
    monkeypatch.setattr(main_module, "BIND", "127.0.0.1")
    assert raw_client.patch("/api/settings", json={"auth.enabled": False}).status_code == 200
