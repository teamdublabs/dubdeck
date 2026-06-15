"""Provider registry — a plain dict, no entry-points/importlib magic.

Each provider type registers its class here. A provider type absent from this
map (because its module is disabled in settings) contributes nothing. Wiring
(config.py / main.py) looks the class up by type name and constructs it with
the right transport for its host.
"""

from typing import Any

from app.httpclient import HttpClient
from app.providers.base import CommandProvider, Provider
from app.providers.compose import ComposeProvider
from app.providers.docker import DockerProvider
from app.providers.hyperv import HyperVProvider
from app.providers.libvirt import LibvirtProvider
from app.providers.parallels import ParallelsProvider
from app.providers.proxmox import ProxmoxProvider
from app.providers.xcpng import XCPNgProvider
from app.transports import Transport

# type_name → provider class, for every provider family. The two families are
# constructed differently by the wiring layer: transport-backed CommandProviders
# (build_command_provider) and HttpClient-backed API providers
# (build_api_provider). This map is the single source of truth for "what types
# exist" — config validation checks against it, the UI reads capabilities off it.
PROVIDER_TYPES: dict[str, type[Provider]] = {
    ParallelsProvider.type_name: ParallelsProvider,
    LibvirtProvider.type_name: LibvirtProvider,
    DockerProvider.type_name: DockerProvider,
    ComposeProvider.type_name: ComposeProvider,
    HyperVProvider.type_name: HyperVProvider,
    ProxmoxProvider.type_name: ProxmoxProvider,
    XCPNgProvider.type_name: XCPNgProvider,
}


def is_command_type(type_name: str) -> bool:
    """True for transport-backed providers (built from a host), False for API
    providers (built from a URL + token). Drives config validation — command
    providers require a `host`, API providers require `url`/token fields."""
    cls = PROVIDER_TYPES.get(type_name)
    return cls is not None and issubclass(cls, CommandProvider)


def build_command_provider(
    type_name: str,
    instance_id: str,
    transport: Transport,
    options: dict[str, Any] | None = None,
) -> Provider:
    """Construct a transport-backed provider by type name. `options` carries
    provider-type-specific config (e.g. compose's `stacks_dir`)."""
    cls = PROVIDER_TYPES.get(type_name)
    if cls is None:
        raise ValueError(f"unknown provider type {type_name!r}")
    if not issubclass(cls, CommandProvider):
        raise ValueError(f"provider type {type_name!r} is not transport-backed")
    return cls(instance_id, transport, **(options or {}))


def build_api_provider(
    type_name: str,
    instance_id: str,
    client: HttpClient,
) -> Provider:
    """Construct an HttpClient-backed API provider (proxmox) by type name. The
    client carries the base URL, auth, and TLS policy — the provider only knows
    paths, mirroring how command providers only know commands."""
    cls = PROVIDER_TYPES.get(type_name)
    if cls is None:
        raise ValueError(f"unknown provider type {type_name!r}")
    if issubclass(cls, CommandProvider):
        raise ValueError(f"provider type {type_name!r} is transport-backed, not an API provider")
    return cls(instance_id, client)
