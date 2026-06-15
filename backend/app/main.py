"""Dubdeck backend — FastAPI app, wiring, and API routes."""

import logging
import os
import time
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse, PlainTextResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from app.auth import PUBLIC_API_PATHS as PUBLIC_API
from app.auth import (
    SESSION_COOKIE,
    AuthService,
    RateLimited,
    assert_auth_or_loopback,
    attach_session,
    clear_session,
    is_loopback_bind,
    session_secure,
)
from app.config import Config, load_config
from app.db import Database
from app.httpclient import HttpxClient
from app.modules.egress import EgressConfig, EgressEngine
from app.modules.egress import router as egress_router
from app.opslog import OpsLog
from app.providers import (
    Capability,
    Provider,
    Unsupported,
    build_api_provider,
    build_command_provider,
    is_command_type,
    XCPNgProvider,
)
from app.providers.proxmox import auth_header
from app.services.ops import OpRegistry, ResourceOps
from app.services.stats import ResourceStatsService
from app.services.status import StatusService, event_stream
from app.settings import InvalidSettingError, SettingsService, UnknownSettingError
from app.transports import LocalTransport, SSHTransport, Transport

CONFIG_PATH = os.environ.get("DUBDECK_CONFIG", "../config.yaml")
DB_PATH = os.environ.get("DUBDECK_DB", "dubdeck.db")
STATIC_DIR = os.environ.get("DUBDECK_STATIC", "static")
# The address uvicorn binds to — the source of truth for the auth-disabled
# loopback check. Set by whoever launches the process, not user-editable.
BIND = os.environ.get("DUBDECK_BIND", "127.0.0.1")

logging.basicConfig(
    level=os.environ.get("DUBDECK_LOG_LEVEL", "INFO"),
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
log = logging.getLogger(__name__)


def build_transports(config: Config) -> dict[str, Transport]:
    transports: dict[str, Transport] = {}
    for name, host in config.hosts.items():
        if host.transport == "local":
            transports[name] = LocalTransport()
        else:
            transports[name] = SSHTransport(host.address, host.user, host.port)
    return transports


def build_providers(config: Config, transports: dict[str, Transport]) -> dict[str, Provider]:
    providers: dict[str, Provider] = {}
    for p in config.providers:
        if p.type == "xcpng":
            providers[p.id] = _build_xcpng(p, transports)
        elif is_command_type(p.type):
            options = {"stacks_dir": p.stacks_dir} if p.stacks_dir is not None else None
            providers[p.id] = build_command_provider(p.type, p.id, transports[p.host], options)
        else:
            providers[p.id] = _build_api(p)
    return providers


def _build_api(p) -> Provider:
    """Construct an API provider from config: resolve its token from the named
    env var (never inline), build the HttpClient, hand it to the registry."""
    secret = os.environ.get(p.token_secret_env or "")
    if not secret:
        raise RuntimeError(
            f"provider {p.id!r}: env var {p.token_secret_env!r} (token secret) is unset"
        )
    if not p.verify_tls:
        log.warning("provider %s: TLS verification DISABLED (verify_tls: false)", p.id)
    client = HttpxClient(
        p.url,
        headers=auth_header(p.token_id, secret),
        verify_tls=p.verify_tls,
    )
    return build_api_provider(p.type, p.id, client)


def _build_xcpng(p, _) -> Provider:
    """Construct an XCP-ng provider — uses XenAPI XML-RPC directly, no HttpClient.

    The provider takes a `host` (XCP-ng address), `username`, and `password`.
    An optional `token_secret_env` can name an env var holding the password,
    mirroring the secret-env pattern used by the Proxmox provider.
    """
    from app.httpclient import HttpClient

    if not p.host:
        raise ValueError(f"provider {p.id!r}: 'xcpng' requires a 'host' field")

    # Resolve password: prefer inline, fall back to env var.
    password = p.password
    if not password and p.token_secret_env:
        password = os.environ.get(p.token_secret_env, "")
    if not password:
        raise ValueError(
            f"provider {p.id!r}: 'xcpng' requires either 'password' or "
            f"'token_secret_env' pointing to an env var"
        )

    # verify_tls defaults to False for homelab self-signed certs.
    verify_ssl = p.verify_tls

    return XCPNgProvider(
        p.id,
        None,  # XCPNgProvider manages its own XenAPISession — no HttpClient needed
        host=p.host,
        username=p.username or "root",
        password=password,
        verify_ssl=verify_ssl,
    )


def wire(app: FastAPI, config: Config, transports: dict[str, Transport]) -> None:
    """Attach all services to app.state. Tests call this with FakeTransports."""
    db = Database(DB_PATH)
    ops = OpsLog(db)
    settings = SettingsService(db)
    auth = AuthService(db)
    providers = build_providers(config, transports)
    registry = OpRegistry()

    app.state.db = db
    app.state.config = config
    app.state.settings = settings
    app.state.auth = auth
    app.state.transports = transports
    app.state.providers = providers
    app.state.ops = ops
    # Module-bound services (egress) are built in apply_modules() once settings
    # are loaded and their toggle can be read — see the comment there.
    app.state.egress = None
    app.state.status = StatusService(config, providers, transports, registry, [])
    app.state.ops_svc = ResourceOps(config, providers, ops, registry)
    app.state.stats = ResourceStatsService(config, providers)


async def apply_modules(app: FastAPI) -> None:
    """Resolve module toggles AFTER settings are loaded, then build module-bound
    services. A module absent from config OR disabled in settings contributes no
    engine, no status section, and no working routes (its handlers 404).

    Enable/disable takes effect at (re)start: modules own background tasks
    (egress's auto-revoke sweeper), so we resolve once at startup rather than
    hot-swapping a live engine. The settings toggle persists either way.
    """
    config: Config = app.state.config
    settings: SettingsService = app.state.settings
    # Egress is default-off for the world; an operator turns it on in settings.
    if "egress" in config.modules and bool(settings.get("modules.egress.enabled", False)):
        engine = EgressEngine(
            app.state.db,
            EgressConfig.model_validate(config.modules["egress"]),
            app.state.transports,
            app.state.ops,
        )
        await engine.init()
        await engine.reconcile()
        engine.start_sweeper()
        app.state.egress = engine
        app.state.status.add_contributor(engine)


@asynccontextmanager
async def lifespan(app: FastAPI):
    if not hasattr(app.state, "config"):  # tests pre-wire with FakeTransports
        config = load_config(CONFIG_PATH)
        wire(app, config, build_transports(config))
    await app.state.ops.init()
    await app.state.settings.init()
    await app.state.auth.init()
    # Fail fast: auth must not be disabled while bound to a non-loopback address.
    assert_auth_or_loopback(bool(app.state.settings.get("auth.enabled")), BIND)
    await apply_modules(app)
    yield
    await app.state.ops_svc.drain()
    if app.state.egress is not None:
        await app.state.egress.close()
    await app.state.db.close()
    for transport in app.state.transports.values():
        if isinstance(transport, SSHTransport):
            await transport.close()


app = FastAPI(title="Dubdeck", lifespan=lifespan)

# Hostnames this app may be addressed as. The bind is 127.0.0.1-only, but that
# alone doesn't stop a browser being used as a proxy against it.
ALLOWED_HOSTS = {
    h.strip()
    for h in os.environ.get("DUBDECK_ALLOWED_HOSTS", "127.0.0.1,localhost,::1").split(",")
    if h.strip()
}


@app.middleware("http")
async def local_origin_guard(request: Request, call_next):
    # DNS rebinding: a page on evil.com whose DNS flips to 127.0.0.1 reaches
    # this server with Host: evil.com — reject anything not addressed to us.
    if request.url.hostname not in ALLOWED_HOSTS:
        return JSONResponse({"detail": f"forbidden Host {request.url.hostname!r}"}, status_code=403)
    # Drive-by CSRF: browsers label cross-site fetches via Sec-Fetch-Site, and
    # no legitimate cross-site caller of a localhost-only API exists.
    if (
        request.url.path.startswith("/api")
        and request.headers.get("sec-fetch-site") == "cross-site"
    ):
        return JSONResponse({"detail": "cross-site request blocked"}, status_code=403)

    # Auth layer — runs AFTER the host/Sec-Fetch checks above (same middleware so
    # the ordering is literal, not dependent on registration order). Folded in
    # here rather than added as a second BaseHTTPMiddleware, which would also
    # re-introduce the SSE-buffering hazard the existing single guard avoids.
    auth: AuthService = app.state.auth
    path = request.url.path
    gated = (
        bool(app.state.settings.get("auth.enabled"))
        and path.startswith("/api")
        and path not in PUBLIC_API
    )
    authed = False
    if gated:
        if not auth.is_configured():
            # First run: force the setup screen, reveal nothing else.
            return JSONResponse({"detail": "setup required"}, status_code=403)
        if auth.verify_token(request.cookies.get(SESSION_COOKIE)):
            authed = True
        else:
            return JSONResponse({"detail": "authentication required"}, status_code=401)

    response = await call_next(request)
    if authed:  # slide the idle window on every authenticated request
        attach_session(response, auth.issue_token(), secure=session_secure(request))
    return response


@app.get("/api/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


class PasswordBody(BaseModel):
    password: str = Field(min_length=1, max_length=256)


@app.get("/api/auth")
async def auth_state(request: Request) -> dict:
    """Public — the frontend reads this to choose setup vs login vs app. Carries
    the brand name so the pre-login screen can render it (settings are gated)."""
    auth: AuthService = app.state.auth
    enabled = bool(app.state.settings.get("auth.enabled"))
    authenticated = (not enabled) or auth.verify_token(request.cookies.get(SESSION_COOKIE))
    return {
        "enabled": enabled,
        "configured": auth.is_configured(),
        "authenticated": authenticated,
        "brand": app.state.settings.get("ui.branding.name"),
    }


@app.post("/api/setup")
async def setup(body: PasswordBody, request: Request) -> JSONResponse:
    """First-run password set. Only works while unconfigured — there is no
    authenticated way back to it (change-password is a separate authed route)."""
    auth: AuthService = app.state.auth
    if auth.is_configured():
        raise HTTPException(409, "already configured")
    try:
        await auth.set_password(body.password)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    resp = JSONResponse({"status": "ok"})
    attach_session(resp, auth.issue_token(), secure=session_secure(request))
    return resp


@app.post("/api/login")
async def login(body: PasswordBody, request: Request) -> JSONResponse:
    auth: AuthService = app.state.auth
    ip = request.client.host if request.client else "unknown"
    try:
        auth.check_rate(ip)
    except RateLimited as exc:
        raise HTTPException(429, "too many attempts; wait a minute") from exc
    if not auth.verify_password(body.password):
        raise HTTPException(401, "invalid password")
    resp = JSONResponse({"status": "ok"})
    attach_session(resp, auth.issue_token(), secure=session_secure(request))
    return resp


@app.post("/api/logout")
async def logout() -> JSONResponse:
    resp = JSONResponse({"status": "ok"})
    clear_session(resp)
    return resp


class ChangePasswordBody(BaseModel):
    current: str = Field(min_length=1, max_length=256)
    new: str = Field(min_length=1, max_length=256)


@app.post("/api/auth/password")
async def change_password(body: ChangePasswordBody) -> dict:
    """Authed (not in PUBLIC_API) — and still re-checks the current password, so
    a hijacked session alone can't rotate the credential."""
    auth: AuthService = app.state.auth
    if not auth.verify_password(body.current):
        raise HTTPException(403, "current password is incorrect")
    try:
        await auth.set_password(body.new)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    # Invalidate every other live session. This request is authed, so the guard
    # middleware re-issues a fresh cookie for the current client on the way out.
    await auth.rotate_session_secret()
    return {"status": "ok"}


@app.get("/api/config")
async def get_config() -> Config:
    return app.state.config


@app.get("/api/settings")
async def get_settings() -> dict:
    # Auth-protected once 3.2 lands; the host-guard middleware covers it until then.
    return app.state.settings.all()


@app.patch("/api/settings")
async def patch_settings(updates: dict[str, object]) -> dict:
    settings: SettingsService = app.state.settings
    # Runtime guard for the same invariant assert_auth_or_loopback enforces at
    # startup: the middleware reads auth.enabled live, so disabling it here would
    # otherwise bypass the loopback rule entirely.
    if updates.get("auth.enabled") is False and not is_loopback_bind(BIND):
        raise HTTPException(
            400, f"auth.enabled=false requires a loopback bind (DUBDECK_BIND={BIND!r})"
        )
    for key, value in updates.items():
        try:
            await settings.set(key, value)
        except UnknownSettingError as exc:
            raise HTTPException(400, f"unknown setting {str(exc)}") from exc
        except InvalidSettingError as exc:
            raise HTTPException(400, str(exc)) from exc
    return settings.all()


@app.get("/api/status")
async def status() -> dict:
    return await app.state.status.snapshot()


@app.get("/api/events")
async def events(request: Request) -> StreamingResponse:
    """SSE stream of status snapshots — pushed promptly after every mutation,
    with a steady ≤5s cadence otherwise."""
    return StreamingResponse(
        event_stream(app.state.status, request.is_disconnected),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.post("/api/groups/{name}/start", status_code=202)
async def start_group(name: str) -> dict:
    if name not in app.state.config.groups:
        raise HTTPException(404, f"unknown group {name!r}")
    result = await app.state.ops_svc.start_group(name)
    app.state.status.invalidate()
    return result


@app.post("/api/groups/{name}/stop", status_code=202)
async def stop_group(name: str) -> dict:
    if name not in app.state.config.groups:
        raise HTTPException(404, f"unknown group {name!r}")
    result = app.state.ops_svc.stop_group(name)
    app.state.status.invalidate()
    return result


@app.delete("/api/errors/{ref:path}")
async def dismiss_error(ref: str) -> dict:
    """Clear a recorded failure for a resource ref or a group:* coordinator key."""
    if not app.state.ops_svc.dismiss_error(ref):
        raise HTTPException(404, f"no error recorded for {ref!r}")
    app.state.status.invalidate()
    return {"ref": ref, "dismissed": True}


def _resource_op(verb: str, provider: str, rid: str) -> dict:
    ref = f"{provider}/{rid}"
    try:
        result = getattr(app.state.ops_svc, verb)(ref)  # returns immediately
    except KeyError as exc:
        raise HTTPException(404, str(exc)) from exc
    app.state.status.invalidate()
    return result


@app.post("/api/resources/{provider}/{rid:path}/start", status_code=202)
async def start_resource(provider: str, rid: str) -> dict:
    return _resource_op("start", provider, rid)


@app.post("/api/resources/{provider}/{rid:path}/stop", status_code=202)
async def stop_resource(provider: str, rid: str) -> dict:
    return _resource_op("stop", provider, rid)


@app.post("/api/resources/{provider}/{rid:path}/suspend", status_code=202)
async def suspend_resource(provider: str, rid: str) -> dict:
    return _resource_op("suspend", provider, rid)


@app.post("/api/resources/{provider}/{rid:path}/restart", status_code=202)
async def restart_resource(provider: str, rid: str) -> dict:
    return _resource_op("restart", provider, rid)


@app.get("/api/resources/{provider}/{rid:path}/logs", response_class=PlainTextResponse)
async def resource_logs(provider: str, rid: str, n: int = 200) -> str:
    """Plain-text log tail for LOGS-capable resources (Docker). `n` clamped to a
    sane window so a runaway request can't pull an unbounded backlog."""
    try:
        return await app.state.ops_svc.logs(f"{provider}/{rid}", min(max(n, 1), 2000))
    except KeyError as exc:
        raise HTTPException(404, str(exc)) from exc
    except Unsupported as exc:
        raise HTTPException(404, str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(502, str(exc)) from exc


@app.get("/api/resources/{provider}/{rid:path}/console")
async def resource_console(provider: str, rid: str) -> dict:
    """Return the console URL (VNC/RDP) for a resource. The frontend opens it
    in a new tab — no in-app embedding required for MVP."""
    if provider not in app.state.providers:
        raise HTTPException(404, f"unknown provider {provider!r}")
    p = app.state.providers[provider]
    if not p.supports(Capability.CONSOLE):
        raise HTTPException(404, f"provider {provider!r} does not support console")
    try:
        url = await p.console(rid)
        return {"url": url}
    except Unsupported as exc:
        raise HTTPException(404, str(exc)) from exc
    except Exception as exc:
        raise HTTPException(502, str(exc)) from exc


@app.get("/api/resources/{provider}/{rid:path}/snapshots")
async def list_snapshots(provider: str, rid: str) -> list[dict]:
    from dataclasses import asdict

    try:
        snaps = await app.state.ops_svc.snapshots(f"{provider}/{rid}")
    except KeyError as exc:
        raise HTTPException(404, str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(502, str(exc)) from exc
    return [asdict(s) for s in snaps]


class SnapshotRequest(BaseModel):
    # Mirrors the Mac shim's snapshot-name pattern — anything else would be
    # rejected there anyway, so fail fast here.
    name: str | None = Field(default=None, pattern=r"^[A-Za-z0-9._-]{1,40}$")


@app.post("/api/resources/{provider}/{rid:path}/snapshots", status_code=202)
async def create_snapshot(provider: str, rid: str, body: SnapshotRequest) -> dict:
    name = body.name or f"dubdeck-{time.strftime('%Y%m%d-%H%M%S')}"
    try:
        result = app.state.ops_svc.create_snapshot(f"{provider}/{rid}", name)
    except KeyError as exc:
        raise HTTPException(404, str(exc)) from exc
    app.state.status.invalidate()
    return {**result, "name": name}


@app.get("/api/resourcestats")
async def resourcestats() -> dict:
    return await app.state.stats.snapshot()


# Egress module routes — handlers 404 when the module is disabled.
app.include_router(egress_router)


@app.get("/api/log")
async def ops_log(
    limit: int = 100,
    before_id: int | None = None,
    action: str | None = None,
    target: str | None = None,
    failures: bool = False,
) -> list[dict]:
    return await app.state.ops.recent(
        limit=min(max(limit, 1), 500),
        before_id=before_id,
        action=action,
        target=target,
        failures_only=failures,
    )


if Path(STATIC_DIR).is_dir():  # built frontend, present in the prod image
    app.mount("/", StaticFiles(directory=STATIC_DIR, html=True), name="frontend")
