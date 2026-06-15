"""Providers — one interface over parallels, libvirt, docker, … resources."""

from app.providers.base import (
    CAPABILITY_METHODS,
    Capability,
    CommandProvider,
    Provider,
    Resource,
    ResourceKind,
    ResourceState,
    Snapshot,
    Unsupported,
)
from app.providers.docker import DockerProvider, PodmanProvider
from app.providers.registry import (
    PROVIDER_TYPES,
    build_api_provider,
    build_command_provider,
    is_command_type,
)
from app.providers.xcpng import XCPNgProvider

__all__ = [
    "CAPABILITY_METHODS",
    "Capability",
    "CommandProvider",
    "DockerProvider",
    "PodmanProvider",
    "Provider",
    "Resource",
    "ResourceKind",
    "ResourceState",
    "Snapshot",
    "Unsupported",
    "PROVIDER_TYPES",
    "XCPNgProvider",
    "build_api_provider",
    "build_command_provider",
    "is_command_type",
]
