"""Stats — host load/mem/disk (keyed by host stats profile) and the low-overhead
per-resource disk footprint (one provider.disk_stats() call per provider).
"""

import logging
import time
from dataclasses import dataclass
from typing import Any

from app.config import Config
from app.providers import Capability, Provider

log = logging.getLogger(__name__)

# macOS hosts go through the dubdeck-stats shim, which translates it into
# sysctl + vm_stat + df; raw command strings would never pass its allowlist.
MACOS_STATS = "dubdeck-stats"
LINUX_STATS = "cat /proc/loadavg && free -b && df -kP /"

_MAC_PAGE_KINDS_USED = ("Pages active", "Pages wired down", "Pages occupied by compressor")


@dataclass(frozen=True)
class HostStats:
    load_1m: float
    load_5m: float
    load_15m: float
    mem_total: int
    mem_used: int
    disk_total: int | None = None
    disk_used: int | None = None


def stats_command(profile: str) -> str:
    return MACOS_STATS if profile == "macos" else LINUX_STATS


def parse_stats(profile: str, output: str) -> HostStats:
    return _parse_mac(output) if profile == "macos" else _parse_linux(output)


def _parse_df(lines: list[str]) -> tuple[int | None, int | None]:
    """Pick the device line out of `df -kP` output appended to the stats."""
    for line in lines:
        fields = line.split()
        if line.startswith("/") and len(fields) >= 6:
            return int(fields[1]) * 1024, int(fields[2]) * 1024
    return None, None


def _parse_linux(output: str) -> HostStats:
    lines = output.splitlines()
    load = lines[0].split()
    mem = next(line for line in lines if line.startswith("Mem:")).split()
    disk_total, disk_used = _parse_df(lines)
    return HostStats(
        load_1m=float(load[0]),
        load_5m=float(load[1]),
        load_15m=float(load[2]),
        mem_total=int(mem[1]),
        mem_used=int(mem[2]),
        disk_total=disk_total,
        disk_used=disk_used,
    )


def _parse_mac(output: str) -> HostStats:
    lines = output.splitlines()
    load = lines[0].strip("{} ").split()
    mem_total = int(lines[1])
    page_size = 16384
    used_pages = 0
    for line in lines[2:]:
        if "page size of" in line:
            page_size = int(line.split("page size of")[1].split()[0])
        for kind in _MAC_PAGE_KINDS_USED:
            if line.startswith(f"{kind}:"):
                used_pages += int(line.split(":")[1].strip().rstrip("."))
    disk_total, disk_used = _parse_df(lines)
    return HostStats(
        load_1m=float(load[0]),
        load_5m=float(load[1]),
        load_15m=float(load[2]),
        mem_total=mem_total,
        mem_used=used_pages * page_size,
        disk_total=disk_total,
        disk_used=disk_used,
    )


class ResourceStatsService:
    """Per-resource disk footprint — the deliberately low-overhead stats tier.
    Refreshed at most every TTL via one disk_stats() call per DISK_STATS-capable
    provider. No per-resource CPU/network sampling by design."""

    TTL = 120.0

    def __init__(self, config: Config, providers: dict[str, Provider]):
        self._config = config
        self._providers = providers
        self._cache: dict[str, Any] | None = None
        self._cached_at = 0.0

    def _known_ids(self, provider_id: str) -> set[str]:
        """Resource ids this provider's group members reference — the disk
        sweep also sees non-lab resources, so drop anything unlisted."""
        ids: set[str] = set()
        for group in self._config.groups.values():
            for ref in group.members:
                pid, sep, rid = ref.partition("/")
                if pid == provider_id and sep:
                    ids.add(rid)
        return ids

    async def snapshot(self) -> dict[str, Any]:
        if self._cache is not None and time.time() - self._cached_at < self.TTL:
            return self._cache
        resources: dict[str, Any] = {}
        for provider_id, provider in self._providers.items():
            if not provider.supports(Capability.DISK_STATS):
                continue
            try:
                disks = await provider.disk_stats()
            except Exception as exc:
                log.warning("disk stats failed for %s: %s", provider_id, exc)
                continue
            known = self._known_ids(provider_id)
            auto = any(g.auto == provider_id for g in self._config.groups.values())
            for rid, size in disks.items():
                if auto or rid in known:
                    resources[f"{provider_id}/{rid}"] = {"disk_bytes": size}
        self._cache = {"generated_at": time.time(), "resources": resources}
        self._cached_at = time.time()
        return self._cache
