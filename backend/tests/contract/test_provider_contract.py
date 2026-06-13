"""Provider contract suite — the definition of "a provider works".

Every provider must pass this with its fakes. Two families register here:
transport-backed CommandProviders (parallels/libvirt/docker/compose) and
HttpClient-backed API providers (proxmox). The four universal guarantees run
against both via a provider factory; the fifth (command-line injection) is
specific to command builders and runs over the command providers, while API
providers get an equivalent path-injection guarantee of their own.

1. declared capabilities ↔ implemented methods (calling an undeclared capability
   raises Unsupported; a declared one never does);
2. list_resources returns stable, non-empty ids on real fixture output;
3. state mapping covers the fixture (no UNKNOWN leaks for known states);
4. injection: hostile resource names are quoted (command builders) or rejected
   (compose stack names, proxmox node/vmid) — never injected raw.
"""

import shlex
from collections.abc import Callable
from dataclasses import dataclass

import pytest

from app.providers import CAPABILITY_METHODS, Capability, Provider, ResourceState, Unsupported
from app.providers import compose as cmp
from app.providers import docker as dk
from app.providers import hyperv as hv
from app.providers import libvirt as lv
from app.providers import parallels as pl
from app.providers.registry import build_command_provider
from app.transports import CommandResult
from tests.conftest import FIXTURES
from tests.proxmox_fakes import healthy_provider as proxmox_provider

# stacks_dir used everywhere the compose provider is constructed in this suite.
COMPOSE_STACKS_DIR = "/srv/stacks"

HOSTILE_NAMES = [
    "vm; rm -rf /",
    "vm name with spaces",
    'vm"quote',
    "vm'apos",
    "vm$(whoami)",
    "vm`id`",
    "vm&&echo pwned",
]


class OkTransport:
    """Permissive transport: returns `listing` for the list command, empty-OK
    for everything else. Records calls. Used to exercise capability methods
    without canning every command variant."""

    def __init__(self, list_cmd: str, listing: str):
        self._list_cmd = list_cmd
        self._listing = listing
        self.calls: list[str] = []

    async def run(self, command: str, timeout: float = 15.0) -> CommandResult:
        self.calls.append(command)
        return CommandResult(stdout=self._listing if command == self._list_cmd else "")


def _command_provider(type_name: str, list_cmd: str, fixture: str):
    listing = (FIXTURES / fixture).read_text()
    options = {"stacks_dir": COMPOSE_STACKS_DIR} if type_name == "compose" else None
    return build_command_provider(
        type_name, f"{type_name}-test", OkTransport(list_cmd, listing), options
    )


@dataclass
class GenericCase:
    """One provider under the four universal guarantees. `make` builds the
    provider with its fakes; `sample_rid` is a valid id for invoking capability
    methods (a real id its fakes recognise)."""

    name: str
    make: Callable[[], Provider]
    sample_rid: str
    expect_id: str
    expect_state: ResourceState


GENERIC_CASES = [
    GenericCase(
        "parallels",
        lambda: _command_provider("parallels", pl.PRLCTL_LIST, "prlctl_list.txt"),
        "res-1",
        "gateway-01",
        ResourceState.STOPPED,
    ),
    GenericCase(
        "libvirt",
        lambda: _command_provider("libvirt", lv.VIRSH_LIST, "virsh_list.txt"),
        "res-1",
        "gateway-02",
        ResourceState.RUNNING,
    ),
    GenericCase(
        "docker",
        lambda: _command_provider("docker", dk.DOCKER_PS, "docker_ps.txt"),
        "res-1",
        "web",
        ResourceState.RUNNING,
    ),
    GenericCase(
        "compose",
        lambda: _command_provider("compose", cmp.COMPOSE_LS, "docker_compose_ls.txt"),
        "res-1",
        "blog",
        ResourceState.RUNNING,
    ),
    GenericCase(
        "hyperv",
        lambda: _command_provider("hyperv", hv.GET_VM, "hyperv_get_vm.json"),
        "res-1",
        "DC01",
        ResourceState.RUNNING,
    ),
    GenericCase(
        "proxmox",
        lambda: proxmox_provider(),
        "pve/100",  # a real guest its fakes recognise
        "pve/100",
        ResourceState.RUNNING,
    ),
]

GENERIC_IDS = [c.name for c in GENERIC_CASES]


async def _invoke(provider, cap: Capability, rid: str):
    if cap is Capability.SNAPSHOT_CREATE:
        await provider.snapshot_create(rid, "snap")
    elif cap is Capability.SNAPSHOT_LIST:
        await provider.snapshot_list(rid)
    elif cap is Capability.DISK_STATS:
        await provider.disk_stats()
    elif cap is Capability.LOGS:
        await provider.logs(rid)
    else:  # start/stop/force_stop/suspend/restart
        await getattr(provider, CAPABILITY_METHODS[cap])(rid)


@pytest.mark.parametrize("case", GENERIC_CASES, ids=GENERIC_IDS)
async def test_declared_capabilities_are_implemented(case: GenericCase):
    provider = case.make()
    for cap in provider.capabilities:
        await _invoke(provider, cap, case.sample_rid)  # must not raise Unsupported


@pytest.mark.parametrize("case", GENERIC_CASES, ids=GENERIC_IDS)
async def test_undeclared_capabilities_raise_unsupported(case: GenericCase):
    provider = case.make()
    for cap in Capability:
        if cap in provider.capabilities:
            continue
        with pytest.raises(Unsupported):
            await _invoke(provider, cap, case.sample_rid)


@pytest.mark.parametrize("case", GENERIC_CASES, ids=GENERIC_IDS)
async def test_list_resources_returns_stable_ids(case: GenericCase):
    provider = case.make()
    resources = await provider.list_resources()
    ids = [r.id for r in resources]
    assert case.expect_id in ids
    assert len(ids) == len(set(ids)), "ids must be unique within a provider"
    assert all(r.id and r.name for r in resources)


@pytest.mark.parametrize("case", GENERIC_CASES, ids=GENERIC_IDS)
async def test_state_mapping_covers_fixture(case: GenericCase):
    provider = case.make()
    resources = await provider.list_resources()
    by_id = {r.id: r for r in resources}
    assert by_id[case.expect_id].state == case.expect_state
    # the real fixture contains only known states — none should leak as UNKNOWN
    assert all(r.state != ResourceState.UNKNOWN for r in resources)


# --- injection: command builders ---------------------------------------------
#
# Each provider declares the quoting function its builders use: POSIX providers
# use shlex.quote; Hyper-V uses PowerShell single-quote escaping (`ps_quote`),
# a different grammar — asserting shlex.quote against a PowerShell command would
# be wrong. Compose has no quoter slot of its own: it REJECTS hostile names (the
# test tolerates the resulting ValueError) rather than quoting them.
COMMAND_BUILDERS = [
    pytest.param(
        [
            pl.start_command,
            pl.stop_command,
            pl.force_stop_command,
            pl.suspend_command,
            pl.snapshot_list_command,
            lambda n: pl.snapshot_create_command(n, "snap"),
        ],
        shlex.quote,
        id="parallels",
    ),
    pytest.param(
        [
            lv.start_command,
            lv.stop_command,
            lv.force_stop_command,
            lv.suspend_command,
            lv.snapshot_list_command,
            lambda n: lv.snapshot_create_command(n, "snap"),
        ],
        shlex.quote,
        id="libvirt",
    ),
    pytest.param(
        [dk.start_command, dk.stop_command, dk.restart_command, lambda n: dk.logs_command(n, 200)],
        shlex.quote,
        id="docker",
    ),
    pytest.param(
        # compose builders take (stacks_dir, name) and REJECT hostile names
        # rather than quote them — the hostile-name test tolerates ValueError.
        [
            lambda n: cmp.up_command(COMPOSE_STACKS_DIR, n),
            lambda n: cmp.down_command(COMPOSE_STACKS_DIR, n),
            lambda n: cmp.restart_command(COMPOSE_STACKS_DIR, n),
        ],
        shlex.quote,
        id="compose",
    ),
    pytest.param(
        # Hyper-V builders run over SSH-to-PowerShell — names are PowerShell
        # single-quote escaped, NOT shlex-quoted.
        [
            hv.start_command,
            hv.stop_command,
            hv.force_stop_command,
            hv.suspend_command,
            hv.snapshot_list_command,
            lambda n: hv.snapshot_create_command(n, "snap"),
        ],
        hv.ps_quote,
        id="hyperv",
    ),
]


@pytest.mark.parametrize("builders,quoter", COMMAND_BUILDERS)
def test_command_builders_quote_hostile_names(builders, quoter):
    for name in HOSTILE_NAMES:
        for builder in builders:
            try:
                cmd = builder(name)
            except ValueError:
                # Rejecting a hostile name outright (compose's strict allowlist)
                # is at least as safe as quoting it — accept that posture.
                continue
            # otherwise the name must appear only in its provider-quoted form — no
            # raw injection of the dangerous string into the command line
            assert quoter(name) in cmd, f"{builder} did not quote {name!r}: {cmd}"


# --- injection: API path building (proxmox) ----------------------------------


@pytest.mark.parametrize("name", HOSTILE_NAMES)
async def test_proxmox_rejects_hostile_path_segments(name):
    """node/vmid land in URL paths; the provider must reject hostile values
    (never percent-encode-and-proceed) before any request is made."""
    provider = proxmox_provider()
    for rid in (f"{name}/100", f"pve/{name}"):
        with pytest.raises(ValueError):
            await provider.start(rid)
