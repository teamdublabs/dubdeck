"""HTTP client abstraction for API providers (proxmox) — the API-side analogue
of the Transport layer.

CommandProviders talk to a host through a `Transport`; ApiProviders talk to a
service through an `HttpClient`. The seam exists for the same reason: tests use
`FakeHttpClient` and never open a socket, exactly as command providers use
`FakeTransport`. The client is instance-bound (base URL + auth fixed at
construction) so the only per-call inputs are method/path/params/body — mirroring
the host-bound Transport.

Transport-level failures (connect refused, DNS, TLS, timeout) raise `HttpError`;
HTTP responses (including 4xx/5xx) come back as `HttpResponse` with `.ok`
reflecting the status code. This is the same split as Transport: a delivered
response with a bad exit is data; an undeliverable command is an exception. The
status service relies on it — an unreachable proxmox endpoint must degrade like
an unreachable SSH host, not freeze the poller.
"""

from dataclasses import dataclass, field
from typing import Any, Protocol

import httpx


@dataclass(frozen=True)
class HttpResponse:
    status: int
    body: Any = None  # parsed JSON, or None when the payload wasn't JSON
    text: str = ""

    @property
    def ok(self) -> bool:
        return 200 <= self.status < 300


class HttpError(Exception):
    """The request could not be delivered (connection/TLS/timeout). A returned
    HTTP error status is NOT this — that's an HttpResponse with ok=False."""


class HttpClient(Protocol):
    """Make a request against this client's fixed base URL with its fixed auth.

    Instance-bound: base URL, headers, and TLS policy are set at construction,
    so the only per-call inputs are the method, path, query params, and JSON
    body. One client per provider instance.
    """

    async def request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        json_body: dict[str, Any] | None = None,
    ) -> HttpResponse: ...

    async def aclose(self) -> None: ...


class HttpxClient:
    """Real HttpClient over httpx.AsyncClient.

    Proxmox token auth rides a default header (`Authorization: PVEAPIToken=
    <id>=<secret>`) so it's never in a URL or log line. `verify_tls=False` is the
    homelab self-signed escape hatch — the wiring layer logs a warning when it's
    off; we don't re-warn per request.
    """

    def __init__(
        self,
        base_url: str,
        *,
        headers: dict[str, str] | None = None,
        verify_tls: bool = True,
        timeout: float = 15.0,
    ):
        self._client = httpx.AsyncClient(
            base_url=base_url.rstrip("/"),
            headers=headers or {},
            verify=verify_tls,
            timeout=timeout,
        )

    async def request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        json_body: dict[str, Any] | None = None,
    ) -> HttpResponse:
        try:
            resp = await self._client.request(method, path, params=params, json=json_body)
        except httpx.HTTPError as exc:
            # Collapse httpx's exception zoo into one type so providers and the
            # status layer never import httpx — same boundary as Transport.
            raise HttpError(str(exc) or exc.__class__.__name__) from exc
        body: Any = None
        try:
            body = resp.json()
        except ValueError:
            body = None
        return HttpResponse(status=resp.status_code, body=body, text=resp.text)

    async def aclose(self) -> None:
        await self._client.aclose()


@dataclass
class FakeHttpClient:
    """Test double: canned responses keyed by (METHOD, path), records every call.

    Mirrors FakeTransport. `respond_seq` consumes a list in order before falling
    back to the static response — this is how UPID polling is tested (a task that
    reports "running" on the first poll and "stopped" after). A keyed entry set to
    an HttpError instance is raised instead of returned, modelling an unreachable
    endpoint.
    """

    responses: dict[tuple[str, str], HttpResponse | HttpError] = field(default_factory=dict)
    sequences: dict[tuple[str, str], list[HttpResponse]] = field(default_factory=dict)
    calls: list[tuple[str, str, dict[str, Any] | None]] = field(default_factory=list)
    label: str = "fake"

    def respond(self, method: str, path: str, status: int = 200, body: Any = None) -> None:
        self.responses[(method.upper(), path)] = HttpResponse(status=status, body=body)

    def respond_seq(self, method: str, path: str, results: list[HttpResponse]) -> None:
        self.sequences[(method.upper(), path)] = list(results)

    def fail(self, method: str, path: str, message: str = "connection refused") -> None:
        self.responses[(method.upper(), path)] = HttpError(message)

    async def request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        json_body: dict[str, Any] | None = None,
    ) -> HttpResponse:
        key = (method.upper(), path)
        self.calls.append((method.upper(), path, params))
        if self.sequences.get(key):
            return self.sequences[key].pop(0)
        if key not in self.responses:
            raise LookupError(f"FakeHttpClient({self.label}): no canned response for {key}")
        canned = self.responses[key]
        if isinstance(canned, HttpError):
            raise canned
        return canned

    async def aclose(self) -> None:
        pass
