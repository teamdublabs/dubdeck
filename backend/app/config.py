"""Config loading — config.yaml is the source of truth for hosts, providers,
and groups. Still "inventory is data, not code"; secrets come from env vars
referenced by name, never inline.

Schema v2 (Phase 2): the hardcoded Host(hypervisor)/Enclave(gateway) model is
gone. A host is a machine we can execute on (ssh|local); a provider is an
instance of a provider type bound to a host; a group is a named collection of
resources for display + bulk ops, with policies that generalize the old
gateway-first semantics. Module config (e.g. egress) lives under `modules:` as
an opaque passthrough — core never parses it, the module does.
"""

import logging
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, model_validator

log = logging.getLogger(__name__)


class Host(BaseModel):
    transport: Literal["ssh", "local"] = "ssh"
    address: str | None = None  # required for ssh; ignored for local
    user: str | None = None  # required for ssh; no personal default
    port: int = 22
    # Host load/mem stats source. "linux" = /proc + free + df; "macos" = the
    # dubdeck-stats shim; null = no host stats for this machine.
    stats: Literal["linux", "macos"] | None = "linux"

    @model_validator(mode="after")
    def validate_ssh_fields(self) -> "Host":
        if self.transport == "ssh":
            if not self.address:
                raise ValueError("ssh host: 'address' is required")
            if not self.user:
                raise ValueError("ssh host: 'user' is required")
        return self


class Provider(BaseModel):
    id: str
    type: str  # validated against the registry in Config.validate_references
    # host binds command providers (parallels/libvirt/docker/compose) to a host;
    # required for them, unused by API providers. Optional here, enforced per
    # family in validate_references.
    host: str | None = None
    # compose-only: directory holding `<name>/compose.yaml` stacks. Required
    # when type == "compose" (enforced in validate_references), ignored otherwise.
    stacks_dir: str | None = None
    # --- API-provider fields (proxmox): no host, a URL + API token instead. ---
    url: str | None = None
    token_id: str | None = None
    # Name of the env var holding the token secret — never the secret inline.
    token_secret_env: str | None = None
    # Homelab self-signed escape hatch; the wiring layer logs a warning when off.
    verify_tls: bool = True
    # --- xcpng-only: no URL needed, just host + username/password. ---
    username: str | None = None
    password: str | None = None
    # --- docker/podman: configurable binary name (defaults to "docker"). ---
    binary_name: str | None = None


class ReadyProbe(BaseModel):
    """Wait until this resource reaches RUNNING before starting the rest of a
    group (state-based, generalizing the old gateway-tailscale wait)."""

    ref: str  # provider-id/resource-id


class GroupPolicies(BaseModel):
    # Members started first (in order) and stopped last — the old "gateway"
    # tier. Each is a provider-id/resource-id ref that must also be a member.
    start_first: list[str] = []
    ready_probe: ReadyProbe | None = None
    # Snapshot every member EXCEPT the start_first ones (stateless infra)
    # before stopping it, so a botched lab session is recoverable.
    snapshot_before_stop: bool = False


class Group(BaseModel):
    label: str
    # Explicit members as provider-id/resource-id refs. Mutually exclusive with
    # `auto`. `auto` tracks a provider's full live resource list (for Docker,
    # where containers churn and nobody hand-lists them).
    members: list[str] = []
    auto: str | None = None  # provider id
    policies: GroupPolicies = GroupPolicies()


def split_ref(ref: str) -> tuple[str, str]:
    """provider-id/resource-id → (provider, resource). resource may contain
    slashes (e.g. proxmox node/vmid), so split once from the left."""
    provider, sep, resource = ref.partition("/")
    if not sep or not provider or not resource:
        raise ValueError(f"invalid resource ref {ref!r} — expected provider-id/resource-id")
    return provider, resource


class Config(BaseModel):
    # All default-empty so a missing/blank config.yaml yields a valid, empty
    # Config — the app boots into the onboarding screen instead of crashing.
    hosts: dict[str, Host] = {}
    providers: list[Provider] = []
    groups: dict[str, Group] = {}
    modules: dict[str, Any] = {}  # opaque per-module config; core never reads it

    @property
    def is_empty(self) -> bool:
        return not self.hosts and not self.providers and not self.groups

    @model_validator(mode="after")
    def validate_references(self) -> "Config":
        from app.providers import PROVIDER_TYPES, is_command_type

        provider_ids: set[str] = set()
        for p in self.providers:
            if p.id in provider_ids:
                raise ValueError(f"duplicate provider id {p.id!r}")
            provider_ids.add(p.id)
            if p.type not in PROVIDER_TYPES:
                raise ValueError(f"provider {p.id!r}: unknown provider type {p.type!r}")
            if is_command_type(p.type):
                # transport-backed: must name a known host.
                if not p.host:
                    raise ValueError(f"provider {p.id!r}: '{p.type}' providers require a 'host'")
                if p.host not in self.hosts:
                    raise ValueError(f"provider {p.id!r}: unknown host {p.host!r}")
                if p.type == "compose" and not p.stacks_dir:
                    raise ValueError(f"provider {p.id!r}: compose providers require 'stacks_dir'")
            else:
                # API provider (proxmox or xcpng).
                if p.type == "xcpng":
                    # xcpng uses host + username/password, not URL + token
                    if not p.host:
                        raise ValueError(f"provider {p.id!r}: 'xcpng' requires a 'host'")
                    if not p.password and not p.token_secret_env:
                        raise ValueError(
                            f"provider {p.id!r}: 'xcpng' requires 'password' or "
                            "'token_secret_env'"
                        )
                else:
                    if not p.url:
                        raise ValueError(f"provider {p.id!r}: '{p.type}' providers require a 'url'")
                    if not p.token_id or not p.token_secret_env:
                        raise ValueError(
                            f"provider {p.id!r}: '{p.type}' providers require "
                            "'token_id' and 'token_secret_env'"
                        )

        for name, group in self.groups.items():
            if group.auto and group.members:
                raise ValueError(f"group {name!r}: set either 'members' or 'auto', not both")
            if group.auto and group.auto not in provider_ids:
                raise ValueError(f"group {name!r}: auto references unknown provider {group.auto!r}")
            member_refs = set(group.members)
            for ref in group.members:
                provider, _ = split_ref(ref)
                if provider not in provider_ids:
                    raise ValueError(f"group {name!r}: member {ref!r} references unknown provider")
            for ref in group.policies.start_first:
                if ref not in member_refs:
                    raise ValueError(f"group {name!r}: start_first {ref!r} is not a member")
            probe = group.policies.ready_probe
            if probe and probe.ref not in member_refs:
                raise ValueError(f"group {name!r}: ready_probe {probe.ref!r} is not a member")
        return self

    def provider_host(self, provider_id: str) -> str:
        for p in self.providers:
            if p.id == provider_id:
                return p.host
        raise KeyError(f"unknown provider {provider_id!r}")

    def group_for_ref(self, ref: str) -> tuple[str, Group] | None:
        # Explicit membership wins; fall back to the auto: group that tracks this
        # ref's provider (Docker containers live in auto groups with no listed
        # members, so without this they'd be unaddressable for per-resource ops).
        for name, group in self.groups.items():
            if ref in group.members:
                return name, group
        provider_id = ref.partition("/")[0]
        for name, group in self.groups.items():
            if group.auto and group.auto == provider_id:
                return name, group
        return None


def load_config(path: str | Path) -> Config:
    # A missing file is the fresh-install case: boot empty and let the UI guide
    # the user to write a config. A file that exists but is malformed/invalid
    # still raises — never silently mask a real misconfiguration.
    p = Path(path)
    if not p.exists():
        log.warning("config file %s not found — starting with an empty config", p)
        return Config()
    data = yaml.safe_load(p.read_text()) or {}
    return Config.model_validate(data)
