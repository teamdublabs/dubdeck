"""Single-user auth — argon2id password, HMAC session cookie, login rate-limit.

Layered AFTER the host-allowlist + Sec-Fetch guard in main.py (those are
auth-independent and stay exactly as-is). First run has NO password: every /api
route outside the public set returns 403 until POST /api/setup sets one — there
are no default credentials, ever. The opt-out (`auth.enabled=false`) is only
honoured on a loopback bind, enforced at startup (see assert_auth_or_loopback).
"""

import hmac
import secrets
import time
from hashlib import sha256

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerifyMismatchError
from starlette.requests import Request
from starlette.responses import Response

from app.db import Database

SESSION_COOKIE = "dubdeck_session"
SESSION_TTL = 8 * 3600  # 8h idle window; the cookie slides on every authed request
LOGIN_WINDOW = 60.0  # seconds
LOGIN_MAX = 5  # attempts per window per IP

# Routes reachable without a session. Everything else under /api needs one once
# a password is set; static files (the SPA shell) are never under /api so they
# always load — the frontend needs to render the setup/login screen.
PUBLIC_API_PATHS = frozenset(
    {"/api/health", "/api/auth", "/api/setup", "/api/login", "/api/logout"}
)

# Addresses we treat as loopback for the auth-disabled startup check. 0.0.0.0,
# "::", and "" are deliberately absent — a wildcard bind is NOT loopback.
LOOPBACK_BINDS = frozenset({"127.0.0.1", "::1", "localhost"})

_SCHEMA = """
CREATE TABLE IF NOT EXISTS auth (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
)
"""


class RateLimited(Exception):
    """Raised by check_rate when an IP exceeds LOGIN_MAX in LOGIN_WINDOW."""


class AuthService:
    """Owns the single admin password hash + the server-side session-signing key.
    The signing key is persisted so sessions survive a backend restart."""

    def __init__(self, db: Database):
        self._db = db
        self._hasher = PasswordHasher()  # argon2id by default
        self._password_hash: str | None = None
        self._secret: bytes = b""
        self._attempts: dict[str, list[float]] = {}

    async def init(self) -> None:
        await self._db.init()
        await self._db.execute(_SCHEMA)
        rows = {
            r["key"]: r["value"] for r in await self._db.fetchall("SELECT key, value FROM auth")
        }
        self._password_hash = rows.get("password_hash")
        secret = rows.get("session_secret")
        if secret is None:
            secret = secrets.token_hex(32)
            await self._db.execute(
                "INSERT INTO auth (key, value) VALUES ('session_secret', ?)", (secret,)
            )
        self._secret = secret.encode()

    async def rotate_session_secret(self) -> None:
        """Replace the signing key — invalidates every previously-issued cookie.
        Called on password change so a stolen/old session can't outlive the
        credential it was minted under."""
        secret = secrets.token_hex(32)
        await self._db.execute(
            "INSERT INTO auth (key, value) VALUES ('session_secret', ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (secret,),
        )
        self._secret = secret.encode()

    # ---- password ----------------------------------------------------------

    def is_configured(self) -> bool:
        return self._password_hash is not None

    async def set_password(self, password: str) -> None:
        if len(password) < 8:
            raise ValueError("password must be at least 8 characters")
        digest = self._hasher.hash(password)
        await self._db.execute(
            "INSERT INTO auth (key, value) VALUES ('password_hash', ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (digest,),
        )
        self._password_hash = digest

    def verify_password(self, password: str) -> bool:
        if self._password_hash is None:
            return False
        try:
            self._hasher.verify(self._password_hash, password)
            return True
        except (VerifyMismatchError, InvalidHashError):
            return False

    # ---- session token: "exp.hexsig", HMAC-SHA256 over str(exp) -------------

    def issue_token(self, now: float | None = None) -> str:
        exp = int((time.time() if now is None else now) + SESSION_TTL)
        return f"{exp}.{self._sign(str(exp))}"

    def verify_token(self, token: str | None, now: float | None = None) -> bool:
        if not token or "." not in token:
            return False
        exp_str, sig = token.rsplit(".", 1)
        if not hmac.compare_digest(sig, self._sign(exp_str)):
            return False
        try:
            exp = int(exp_str)
        except ValueError:
            return False
        return exp > (time.time() if now is None else now)

    def _sign(self, msg: str) -> str:
        return hmac.new(self._secret, msg.encode(), sha256).hexdigest()

    # ---- login rate limit (in-memory, per IP) ------------------------------

    def check_rate(self, ip: str) -> None:
        now = time.monotonic()
        hits = [t for t in self._attempts.get(ip, []) if now - t < LOGIN_WINDOW]
        if len(hits) >= LOGIN_MAX:
            self._attempts[ip] = hits
            raise RateLimited
        hits.append(now)
        self._attempts[ip] = hits


def attach_session(response: Response, token: str, *, secure: bool) -> None:
    response.set_cookie(
        SESSION_COOKIE,
        token,
        max_age=SESSION_TTL,
        httponly=True,
        samesite="lax",
        secure=secure,
        path="/",
    )


def clear_session(response: Response) -> None:
    response.delete_cookie(SESSION_COOKIE, path="/")


def session_secure(request: Request) -> bool:
    """Set the Secure flag only over HTTPS — the default loopback bind is plain
    HTTP, where Secure would make the cookie unusable."""
    return request.url.scheme == "https"


def is_loopback_bind(bind: str) -> bool:
    """Allowlist, not a parse — only these exact strings count as loopback. Any
    alias we don't recognise (0.0.0.0, ::, 127.0.0.2, octal forms) fails closed:
    the worst case is refusing to disable auth, never wrongly allowing it."""
    return bind in LOOPBACK_BINDS


def assert_auth_or_loopback(auth_enabled: bool, bind: str) -> None:
    """Refuse to start with auth disabled on a non-loopback bind. Enforced, not
    just documented — a wildcard bind (0.0.0.0/::) fails this check by design.

    NB: `bind` is DUBDECK_BIND, which the operator must set to match uvicorn's
    real --host; this guard can only reason about what it's told."""
    if auth_enabled:
        return
    if not is_loopback_bind(bind):
        raise RuntimeError(
            f"auth.enabled=false is only allowed on a loopback bind; "
            f"DUBDECK_BIND={bind!r} is not loopback ({sorted(LOOPBACK_BINDS)})"
        )
