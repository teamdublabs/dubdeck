"""Egress module HTTP routes. Mounted by main.py; every handler 404s when the
module is disabled (app.state.egress is None), so a deployment without an
`egress:` config section exposes no working egress surface."""

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from app.modules.egress.engine import MAX_DURATION, EgressEngine

router = APIRouter(prefix="/api/groups", tags=["egress"])


class EgressRequest(BaseModel):
    duration_s: int = Field(default=1800, gt=0)


def _egress(request: Request) -> EgressEngine:
    egress = request.app.state.egress
    if egress is None:
        raise HTTPException(404, "egress module not enabled")
    return egress


@router.post("/{name}/egress")
async def enable_egress(request: Request, name: str, body: EgressRequest) -> dict:
    egress = _egress(request)
    if name not in egress._config.gateways:
        raise HTTPException(404, f"no egress gateway for group {name!r}")
    try:
        expires_at = await egress.enable(name, body.duration_s)
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(502, str(exc)) from exc
    request.app.state.status.invalidate()
    return {
        "group": name,
        "expires_at": expires_at,
        "applied_duration_s": min(body.duration_s, MAX_DURATION),
        "clamped": body.duration_s > MAX_DURATION,
    }


@router.post("/{name}/egress/extend")
async def extend_egress(request: Request, name: str, body: EgressRequest) -> dict:
    egress = _egress(request)
    try:
        expires_at = await egress.extend(name, body.duration_s)
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc
    request.app.state.status.invalidate()
    return {"group": name, "expires_at": expires_at}


@router.delete("/{name}/egress")
async def revoke_egress(request: Request, name: str) -> dict:
    egress = _egress(request)
    try:
        await egress.revoke(name)
    except RuntimeError as exc:
        raise HTTPException(502, str(exc)) from exc
    request.app.state.status.invalidate()
    return {"group": name, "revoked": True}
