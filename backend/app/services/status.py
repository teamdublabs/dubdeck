"""Status aggregator — one cached snapshot of every host, provider, and group.

Aggregates resource state across all providers and assembles it into groups.
Modules (e.g. egress) inject their own data via StatusContributor without core
importing them — main.py wires any contributors in.
"""

import asyncio
import contextlib
import json
import logging
import time
from dataclasses import asdict
from typing import Any, Protocol

from app.config import Config, Host, split_ref
from app.providers import Provider
from app.services.stats import parse_stats, stats_command
from app.transports import Transport

log = logging.getLogger(__name__)


class StatusContributor(Protocol):
    """A module contributes a section under snapshot["modules"][name]."""

    name: str

    async def status(self) -> dict[str, Any]: ...


class StatusService:
    # Ignore in-flight markers older than this — a wedged background task must
    # not pin a resource on "starting" in the UI forever.
    INFLIGHT_TTL = 600.0

    def __init__(
        self,
        config: Config,
        providers: dict[str, Provider],
        host_transports: dict[str, Transport],
        registry: Any = None,
        contributors: list[StatusContributor] | None = None,
        ttl: float = 5.0,
    ):
        self._config = config
        self._providers = providers
        self._host_transports = host_transports
        self._reg = registry  # duck-typed OpRegistry (.inflight / .errors / .started)
        self._contributors = contributors or []
        self._ttl = ttl
        self._cache: dict[str, Any] | None = None
        self._cached_at = 0.0
        self._lock = asyncio.Lock()
        self._refresh_task: asyncio.Task | None = None
        self._listeners: set[asyncio.Event] = set()

    def add_contributor(self, contributor: StatusContributor) -> None:
        """Register a module's status section. Called at startup before the first
        snapshot is built (see apply_modules), so it always lands in the cache."""
        self._contributors.append(contributor)

    def _inflight(self, ref: str) -> str | None:
        if not self._reg:
            return None
        phase = self._reg.inflight.get(ref)
        if phase is None:
            return None
        started = getattr(self._reg, "started", {}).get(ref)
        if started is not None and time.time() - started > self.INFLIGHT_TTL:
            return None
        return phase

    async def snapshot(self) -> dict[str, Any]:
        # Stale-while-revalidate: only the very first call blocks. After that,
        # readers get the cached snapshot immediately and a stale cache kicks one
        # background refresh — a downed host must never freeze every poller.
        # The in-flight overlay is applied on every read (never cached).
        if self._cache is None:
            async with self._lock:
                if self._cache is None:
                    self._cache = await self._build()
                    self._cached_at = time.time()
        elif time.time() - self._cached_at >= self._ttl:
            self._kick_refresh()
        return self._overlay(self._cache)

    def _kick_refresh(self) -> None:
        if self._refresh_task is None or self._refresh_task.done():
            self._refresh_task = asyncio.create_task(self._refresh())

    async def _refresh(self) -> None:
        try:
            snap = await self._build()
        except Exception:
            log.exception("status refresh failed — keeping the stale snapshot")
            return
        self._cache = snap
        self._cached_at = time.time()
        self._notify()

    def subscribe(self) -> asyncio.Event:
        event = asyncio.Event()
        self._listeners.add(event)
        return event

    def unsubscribe(self, event: asyncio.Event) -> None:
        self._listeners.discard(event)

    def _notify(self) -> None:
        for event in self._listeners:
            event.set()

    def _overlay(self, snap: dict[str, Any]) -> dict[str, Any]:
        if not self._reg or (not self._reg.inflight and not self._reg.errors):
            return snap

        def overlaid(node: dict[str, Any]) -> dict[str, Any]:
            ref = node["ref"]
            return {
                **node,
                "state": self._inflight(ref) or node["state"],
                "error": self._reg.errors.get(ref),
            }

        out = {**snap, "groups": {}}
        for name, group in snap["groups"].items():
            out["groups"][name] = {
                **group,
                "error": self._reg.errors.get(f"group:{name}"),
                "resources": [overlaid(r) for r in group["resources"]],
            }
        return out

    def invalidate(self) -> None:
        self._cached_at = 0.0
        if self._cache is not None:
            self._kick_refresh()

    async def _provider_listing(self, provider_id: str, provider: Provider) -> tuple[str, dict]:
        import logging, traceback
        try:
            resources = await provider.list_resources()
            logging.getLogger().warning(f"DEBUG _provider_listing {provider_id}: {len(resources)} resources")
        except Exception as exc:
            logging.getLogger().warning(f"DEBUG _provider_listing {provider_id}: EXCEPTION {exc}\n{traceback.format_exc()}")
            return provider_id, {"reachable": False, "error": str(exc), "resources": {}}
        caps = sorted(str(c) for c in provider.capabilities)
        return provider_id, {
            "reachable": True,
            "error": None,
            "capabilities": caps,
            "resources": {
                r.id: {
                    "ref": f"{provider_id}/{r.id}",
                    "provider": provider_id,
                    "id": r.id,
                    "name": r.name,
                    "kind": str(r.kind),
                    "state": str(r.state),
                    "capabilities": caps,
                    "error": None,
                }
                for r in resources
            },
        }

    async def _host_stats(self, name: str, host: Host) -> tuple[str, dict]:
        info: dict[str, Any] = {"reachable": True, "error": None, "stats": None}
        if host.stats is None:
            return name, info
        result = await self._host_transports[name].run(stats_command(host.stats))
        if not result.ok:
            info["reachable"] = False
            info["error"] = result.stderr.strip() or f"exit {result.exit_code}"
            return name, info
        with contextlib.suppress(ValueError, IndexError, StopIteration):
            info["stats"] = asdict(parse_stats(host.stats, result.stdout))
        return name, info

    async def _provider_node_stats(self, provider_id: str, provider: Provider) -> dict[str, Any]:
        """API providers that own their hosts (proxmox nodes) expose `node_stats`
        — fold each node into the `hosts` section keyed `provider/node`. Duck-
        typed: providers without the method contribute nothing. Best-effort, like
        every other stats call — an error degrades to no entry, never a wedge."""
        fn = getattr(provider, "node_stats", None)
        if fn is None:
            return {}
        try:
            nodes = await fn()
        except Exception as exc:
            log.warning("node stats failed for %s: %s", provider_id, exc)
            return {}
        return {
            f"{provider_id}/{node}": {"reachable": True, "error": None, "stats": stats}
            for node, stats in nodes.items()
        }

    async def _build(self) -> dict[str, Any]:
        provider_results, host_results, module_results, node_stat_results = await asyncio.gather(
            asyncio.gather(*(self._provider_listing(i, p) for i, p in self._providers.items())),
            asyncio.gather(*(self._host_stats(n, h) for n, h in self._config.hosts.items())),
            asyncio.gather(*(self._contribution(c) for c in self._contributors)),
            asyncio.gather(*(self._provider_node_stats(i, p) for i, p in self._providers.items())),
        )
        listings = dict(provider_results)
        hosts = dict(host_results)
        # API providers (proxmox) contribute their nodes as host-stat entries,
        # namespaced provider/node so they sit beside the SSH hosts in the UI.
        for node_stats in node_stat_results:
            hosts.update(node_stats)

        import logging
        logging.getLogger().warning(f"DEBUG _build provider_results: {provider_results}")
        listings = dict(provider_results)
        hosts = dict(host_results)
        logging.getLogger().warning(f"DEBUG _build listings: {listings}")
        groups: dict[str, Any] = {}
        for name, group in self._config.groups.items():
            if group.auto:
                listing = listings.get(group.auto, {})
                # auto: groups track a provider's full listing, whose order can
                # shift between polls (e.g. `docker ps`). Sort by id so resource
                # churn adds/removes rows without reshuffling the rest (no jank).
                resources = sorted(listing.get("resources", {}).values(), key=lambda r: r["id"])
            else:
                resources = []
                for ref in group.members:
                    provider_id, rid = split_ref(ref)
                    listing = listings.get(provider_id, {})
                    node = listing.get("resources", {}).get(rid)
                    resources.append(node or self._unknown_node(provider_id, rid))
            groups[name] = {"label": group.label, "resources": resources}

        return {
            "generated_at": time.time(),
            "hosts": hosts,
            "providers": {
                pid: {"reachable": info["reachable"], "error": info["error"]}
                for pid, info in listings.items()
            },
            "groups": groups,
            "modules": {name: data for name, data in module_results},
        }

    def _unknown_node(self, provider_id: str, rid: str) -> dict[str, Any]:
        caps = sorted(str(c) for c in self._providers[provider_id].capabilities)
        return {
            "ref": f"{provider_id}/{rid}",
            "provider": provider_id,
            "id": rid,
            "name": rid,
            "kind": "vm",
            "state": "unknown",
            "capabilities": caps,
            "error": None,
        }

    async def _contribution(self, contributor: StatusContributor) -> tuple[str, dict]:
        try:
            return contributor.name, await contributor.status()
        except Exception:
            log.exception("status contributor %s failed", contributor.name)
            return contributor.name, {}


async def event_stream(service: StatusService, is_disconnected, heartbeat_s: float = 5.0):
    """SSE frames of status snapshots: prompt push after each refresh, steady
    heartbeat otherwise. Pure generator — the HTTP endpoint only wraps it."""
    refreshed = service.subscribe()
    try:
        while True:
            snap = await service.snapshot()
            yield f"data: {json.dumps(snap)}\n\n"
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(refreshed.wait(), timeout=heartbeat_s)
            refreshed.clear()
            if await is_disconnected():
                return
    finally:
        service.unsubscribe(refreshed)
