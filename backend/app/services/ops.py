"""Resource start/stop/suspend/snapshot with policy-driven ordering.

Operations run in the background: a graceful ACPI shutdown can take 30-60s, so
blocking the HTTP request on it would freeze the UI. start()/stop() launch the
work, mark the resource in-flight, and return immediately; the status poll
reflects the real state once the provider reports it.

Resources are addressed by ref = "provider-id/resource-id". The old gateway-
first logic generalizes to group policy: `start_first` members boot first (and
stop last), `ready_probe` waits for one to reach RUNNING, `snapshot_before_stop`
snapshots the other members first.
"""

import asyncio
import logging
import time
from dataclasses import dataclass, field

from app.config import Config, Group, split_ref
from app.opslog import OpsLog
from app.providers import Provider, ResourceState

log = logging.getLogger(__name__)


@dataclass
class OpRegistry:
    """Shared between ResourceOps (writer) and StatusService (reader). Keyed by
    ref ("provider/rid") for resources and "group:<name>" for bulk ops."""

    inflight: dict[str, str] = field(default_factory=dict)  # ref -> phase
    errors: dict[str, str] = field(default_factory=dict)  # ref -> last failure
    started: dict[str, float] = field(default_factory=dict)  # ref -> op launch time


class ResourceOps:
    START_TIMEOUT = 90.0
    STOP_TIMEOUT = 180.0
    # Graceful providers (libvirt) fire-and-forget ACPI; give the guest this
    # long to power down cleanly, then force it off.
    STOP_GRACE = 10.0
    STOP_POLL = 2.0
    # After powering on a start_first member, wait until it reports RUNNING
    # before starting the rest — a member booted into a half-up group can come
    # up misrouted.
    READY_TIMEOUT = 120.0
    READY_POLL = 3.0
    SNAPSHOT_TIMEOUT = 300.0

    def __init__(
        self,
        config: Config,
        providers: dict[str, Provider],
        ops: OpsLog,
        registry: OpRegistry,
    ):
        self._config = config
        self._providers = providers
        self._ops = ops
        self._reg = registry
        self._tasks: set[asyncio.Task] = set()
        self._group_locks: dict[str, asyncio.Lock] = {}  # group -> start_first lock

    # --- helpers ---

    def _provider_of(self, ref: str) -> tuple[Provider, str]:
        provider_id, rid = split_ref(ref)
        return self._providers[provider_id], rid

    async def _state(self, ref: str) -> ResourceState:
        provider, rid = self._provider_of(ref)
        try:
            resources = await provider.list_resources()
        except Exception:
            return ResourceState.UNKNOWN  # host down — caller treats as not-running
        for r in resources:
            if r.id == rid:
                return r.state
        return ResourceState.UNKNOWN

    def _spawn(self, key: str, phase: str, coro) -> dict:
        self._reg.inflight[key] = phase
        self._reg.started[key] = time.time()
        self._reg.errors.pop(key, None)
        task = asyncio.create_task(self._guard(key, coro))
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
        return {"ref": key, "status": phase}

    async def _guard(self, key: str, coro) -> None:
        try:
            await coro
        except Exception as exc:  # background task: never let it vanish silently
            log.error("background op failed for %s: %s", key, exc)
            self._reg.errors[key] = str(exc)
            await self._ops.record("resource.error", key, False, str(exc))
        finally:
            self._reg.inflight.pop(key, None)
            self._reg.started.pop(key, None)

    async def drain(self) -> None:
        while self._tasks:
            await asyncio.gather(*list(self._tasks), return_exceptions=True)

    def dismiss_error(self, key: str) -> bool:
        return self._reg.errors.pop(key, None) is not None

    # --- single-resource ops ---

    def _locate(self, ref: str) -> tuple[str, Group]:
        located = self._config.group_for_ref(ref)
        if located is None:
            raise KeyError(f"unknown resource {ref!r}")
        return located

    def start(self, ref: str) -> dict:
        group_name, group = self._locate(ref)
        if ref in self._reg.inflight:
            return {"ref": ref, "status": self._reg.inflight[ref]}
        return {
            **self._spawn(ref, "starting", self._do_start(ref, group_name, group)),
            "group": group_name,
        }

    def stop(self, ref: str) -> dict:
        group_name, group = self._locate(ref)
        if ref in self._reg.inflight:
            return {"ref": ref, "status": self._reg.inflight[ref]}
        return {**self._spawn(ref, "stopping", self._do_stop(ref, group)), "group": group_name}

    def suspend(self, ref: str) -> dict:
        group_name, _ = self._locate(ref)
        if ref in self._reg.inflight:
            return {"ref": ref, "status": self._reg.inflight[ref]}
        return {**self._spawn(ref, "suspending", self._do_suspend(ref)), "group": group_name}

    async def _do_suspend(self, ref: str) -> None:
        provider, rid = self._provider_of(ref)
        try:
            await provider.suspend(rid, timeout=self.STOP_TIMEOUT)
        except Exception as exc:
            await self._ops.record("resource.suspend", ref, False, str(exc))
            raise
        await self._ops.record("resource.suspend", ref, True, "")

    def restart(self, ref: str) -> dict:
        group_name, _ = self._locate(ref)
        if ref in self._reg.inflight:
            return {"ref": ref, "status": self._reg.inflight[ref]}
        return {**self._spawn(ref, "restarting", self._do_restart(ref)), "group": group_name}

    async def _do_restart(self, ref: str) -> None:
        # One call: docker/compose restart is atomic from our side (no graceful
        # ACPI escalation like the VM providers need).
        provider, rid = self._provider_of(ref)
        try:
            await provider.restart(rid, timeout=self.STOP_TIMEOUT)
        except Exception as exc:
            await self._ops.record("resource.restart", ref, False, str(exc))
            raise
        await self._ops.record("resource.restart", ref, True, "")

    async def logs(self, ref: str, n: int) -> str:
        """Read-only log tail. Locates the resource (404 if unknown), then defers
        to the provider — Unsupported bubbles up for non-LOGS providers."""
        self._locate(ref)
        provider, rid = self._provider_of(ref)
        return await provider.logs(rid, n)

    async def snapshots(self, ref: str):
        self._locate(ref)
        provider, rid = self._provider_of(ref)
        return await provider.snapshot_list(rid)

    def create_snapshot(self, ref: str, name: str) -> dict:
        self._locate(ref)
        if ref in self._reg.inflight:
            return {"ref": ref, "status": self._reg.inflight[ref]}
        return self._spawn(ref, "snapshotting", self._do_snapshot(ref, name))

    async def _do_snapshot(self, ref: str, name: str) -> None:
        provider, rid = self._provider_of(ref)
        try:
            await provider.snapshot_create(rid, name, timeout=self.SNAPSHOT_TIMEOUT)
        except Exception as exc:
            await self._ops.record("resource.snapshot", ref, False, str(exc))
            raise
        await self._ops.record("resource.snapshot", ref, True, name)

    # --- start_first / ready-probe ordering ---

    async def _ensure_start_first(self, group_name: str, group: Group) -> None:
        """Bring up the group's start_first members (in order) and wait for the
        ready-probe resource to report RUNNING. Serialized per group so racing
        member starts boot the infra exactly once."""
        if not group.policies.start_first:
            return
        lock = self._group_locks.setdefault(group_name, asyncio.Lock())
        async with lock:
            for ref in group.policies.start_first:
                if await self._state(ref) == ResourceState.RUNNING:
                    continue
                provider, rid = self._provider_of(ref)
                try:
                    await provider.start(rid, timeout=self.START_TIMEOUT)
                except Exception as exc:
                    await self._ops.record("resource.start", ref, False, str(exc))
                    raise RuntimeError(f"start_first {ref} failed: {exc}") from exc
                await self._ops.record("resource.start", ref, True, "start_first")
            await self._wait_ready(group)

    async def _wait_ready(self, group: Group) -> None:
        probe = group.policies.ready_probe
        if probe is None:
            return
        deadline = time.monotonic() + self.READY_TIMEOUT
        while True:
            if await self._state(probe.ref) == ResourceState.RUNNING:
                return
            if time.monotonic() >= deadline:
                raise RuntimeError(
                    f"{probe.ref} started but not RUNNING after "
                    f"{self.READY_TIMEOUT:.0f}s — member start aborted"
                )
            await asyncio.sleep(self.READY_POLL)

    async def _do_start(self, ref: str, group_name: str, group: Group) -> None:
        # Members get the start_first tier (firewall/infra) up first — a lab VM
        # without its gateway is an isolated box at best, a misrouted one at worst.
        if ref not in group.policies.start_first:
            await self._ensure_start_first(group_name, group)
        provider, rid = self._provider_of(ref)
        try:
            await provider.start(rid, timeout=self.START_TIMEOUT)
        except Exception as exc:
            await self._ops.record("resource.start", ref, False, str(exc))
            raise
        await self._ops.record("resource.start", ref, True, "")

    async def _do_stop(self, ref: str, group: Group) -> None:
        provider, rid = self._provider_of(ref)

        # Guardrail groups: snapshot before stopping (except start_first infra,
        # which is stateless), and abort the stop if the snapshot fails — losing
        # in-progress lab state is worse than a VM that stays up a little longer.
        if group.policies.snapshot_before_stop and ref not in group.policies.start_first:
            name = f"dubdeck-prestop-{time.strftime('%Y%m%d-%H%M%S')}"
            try:
                await provider.snapshot_create(rid, name, timeout=self.SNAPSHOT_TIMEOUT)
            except Exception as exc:
                await self._ops.record("resource.snapshot", ref, False, str(exc))
                raise RuntimeError(f"pre-stop snapshot failed — stop aborted: {exc}") from exc
            await self._ops.record("resource.snapshot", ref, True, name)

        # Blocking providers (parallels): one clean stop call, done.
        if not getattr(provider, "stop_is_graceful", False):
            try:
                await provider.stop(rid, timeout=self.STOP_TIMEOUT)
            except Exception as exc:
                await self._ops.record("resource.stop", ref, False, str(exc))
                raise
            await self._ops.record("resource.stop", ref, True, "")
            return

        # Graceful providers (libvirt): request ACPI shutdown, then poll. Its
        # exit code isn't the final word — `virsh shutdown` returns 0 even when
        # the guest ignores ACPI — so the real signal is whether it powers off.
        try:
            await provider.stop(rid, timeout=self.STOP_TIMEOUT)
            await self._ops.record("resource.stop", ref, True, "graceful shutdown requested")
        except Exception as exc:
            await self._ops.record("resource.stop", ref, False, str(exc))

        checks = int(self.STOP_GRACE / self.STOP_POLL) + 1
        for _ in range(checks):
            if await self._state(ref) != ResourceState.RUNNING:
                return  # powered off on its own — clean stop
            await asyncio.sleep(self.STOP_POLL)

        # Still running after the grace window — force it off.
        try:
            await provider.force_stop(rid, timeout=self.STOP_TIMEOUT)
        except Exception as exc:
            await self._ops.record("resource.force_stop", ref, False, str(exc))
            raise
        await self._ops.record(
            "resource.force_stop", ref, True, f"forced after {self.STOP_GRACE:.0f}s grace"
        )

    # --- group bulk ops ---

    async def start_group(self, name: str) -> dict:
        group = self._config.groups[name]
        states = {r: await self._state(r) for r in group.members}
        targets = [
            ref
            for ref in group.members
            if states.get(ref) != ResourceState.RUNNING and ref not in self._reg.inflight
        ]
        for ref in targets:
            self.start(ref)
        return {"group": name, "starting": targets}

    def stop_group(self, name: str) -> dict:
        group = self._config.groups[name]
        key = f"group:{name}"
        if key in self._reg.inflight:
            return {"group": name, "status": self._reg.inflight[key]}
        self._spawn(key, "stopping", self._do_stop_group(name, group))
        return {"group": name, "status": "stopping"}

    async def _do_stop_group(self, name: str, group: Group) -> None:
        states = {r: await self._state(r) for r in group.members}
        running = [r for r in group.members if states.get(r) == ResourceState.RUNNING]
        # Non-infra members first, so nothing is left running behind a dead
        # firewall; start_first (infra) comes down last.
        others = [r for r in running if r not in group.policies.start_first]
        infra = [r for r in running if r in group.policies.start_first]

        for ref in others:
            if ref not in self._reg.inflight:
                self.stop(ref)
        while any(ref in self._reg.inflight for ref in others):
            await asyncio.sleep(self.STOP_POLL)
        failed = [ref for ref in others if ref in self._reg.errors]
        if failed:
            # Leave the infra up: a member that refused to stop must not end up
            # running in a group with no gateway.
            raise RuntimeError(f"infra left running — stop failed for: {', '.join(failed)}")
        for ref in infra:
            if ref not in self._reg.inflight:
                self.stop(ref)
