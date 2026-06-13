"""Phase 5 — Docker + compose-stack providers.

Parser edge cases over the captured fixtures, command-injection posture, and the
provider-level behaviour the contract suite doesn't cover (CONTAINER/STACK kinds,
LOGS merge, the strict compose name allowlist, stacks_dir filtering).
"""

from pathlib import Path

import pytest

from app.config import Config
from app.main import build_providers
from app.opslog import OpsLog
from app.providers import Capability, ResourceKind, ResourceState, Unsupported
from app.providers import compose as cmp
from app.providers import docker as dk
from app.providers.registry import build_command_provider
from app.services.ops import OpRegistry, ResourceOps
from app.transports import FakeTransport

FIXTURES = Path(__file__).parent / "fixtures"
STACKS_DIR = "/srv/stacks"


def _docker_config() -> Config:
    # A single Docker provider surfaced through an auto: group — the canonical
    # Docker shape (containers churn, nobody hand-lists them).
    return Config.model_validate(
        {
            "hosts": {"edge": {"transport": "ssh", "address": "192.0.2.50", "user": "labuser"}},
            "providers": [{"id": "edge-docker", "type": "docker", "host": "edge"}],
            "groups": {"containers": {"label": "Containers", "auto": "edge-docker"}},
        }
    )


async def _ops(db, config, transports) -> ResourceOps:
    ops = OpsLog(db)
    await ops.init()
    return ResourceOps(config, build_providers(config, transports), ops, OpRegistry())


# --- docker parser ---


def test_docker_parse_list_states():
    states = dk.parse_list((FIXTURES / "docker_ps.txt").read_text())
    assert states == {
        "web": ResourceState.RUNNING,
        "api": ResourceState.RUNNING,
        "cache": ResourceState.PAUSED,
        "importer": ResourceState.STOPPED,  # Exited (1)
        "batch": ResourceState.STOPPED,  # Exited (0)
        "edge.proxy": ResourceState.RUNNING,  # dotted name survives
        "migrator": ResourceState.STOPPED,  # created-but-never-started
    }


def test_docker_state_read_from_field_not_status_string():
    # `Status` says "Up 3 days (healthy)" — health/ago noise must not leak into
    # the state, which comes from the machine-readable `State` field.
    line = '{"Names":"svc","State":"running","Status":"Up 3 days (healthy)"}'
    assert dk.parse_list(line) == {"svc": ResourceState.RUNNING}


@pytest.mark.parametrize(
    "state,expected",
    [
        ("restarting", ResourceState.UNKNOWN),
        ("dead", ResourceState.UNKNOWN),
        ("removing", ResourceState.UNKNOWN),
        ("", ResourceState.UNKNOWN),
    ],
)
def test_docker_transient_states_are_unknown(state, expected):
    line = f'{{"Names":"x","State":"{state}","Status":"whatever"}}'
    assert dk.parse_list(line) == {"x": expected}


def test_docker_blank_lines_ignored():
    assert dk.parse_list("\n\n") == {}


def test_docker_logs_command_merges_streams_and_clamps_int():
    cmd = dk.logs_command("web", 50)
    assert cmd == "docker logs --tail 50 web 2>&1"


async def test_docker_provider_kinds_and_caps():
    t = FakeTransport(label="docker")
    t.respond(dk.DOCKER_PS, stdout=(FIXTURES / "docker_ps.txt").read_text())
    provider = build_command_provider("docker", "edge-docker", t)
    resources = await provider.list_resources()
    assert all(r.kind is ResourceKind.CONTAINER for r in resources)
    assert provider.supports(Capability.LOGS)
    assert provider.supports(Capability.RESTART)
    assert not provider.supports(Capability.SNAPSHOT_LIST)
    assert not provider.supports(Capability.SUSPEND)


async def test_docker_logs_returns_text():
    t = FakeTransport(label="docker")
    t.respond("docker logs --tail 200 web 2>&1", stdout="line one\nline two\n")
    provider = build_command_provider("docker", "edge-docker", t)
    assert await provider.logs("web") == "line one\nline two\n"


async def test_docker_snapshot_unsupported():
    provider = build_command_provider("docker", "edge-docker", FakeTransport())
    with pytest.raises(Unsupported):
        await provider.snapshot_list("web")


# --- compose parser ---


def test_compose_parse_list_filters_to_stacks_dir():
    states = cmp.parse_list((FIXTURES / "docker_compose_ls.txt").read_text(), STACKS_DIR)
    # "stray" lives under /opt/other → excluded; the three under /srv/stacks stay.
    assert states == {
        "blog": ResourceState.RUNNING,  # running(3)
        "wiki": ResourceState.RUNNING,  # running(1), exited(1) → any running
        "monitoring": ResourceState.STOPPED,  # exited(3)
    }


def test_compose_parse_list_empty():
    assert cmp.parse_list("", STACKS_DIR) == {}


async def test_compose_provider_kind_stack():
    t = FakeTransport(label="stacks")
    t.respond(cmp.COMPOSE_LS, stdout=(FIXTURES / "docker_compose_ls.txt").read_text())
    provider = build_command_provider("compose", "host-stacks", t, {"stacks_dir": STACKS_DIR})
    resources = await provider.list_resources()
    assert {r.id for r in resources} == {"blog", "wiki", "monitoring"}
    assert all(r.kind is ResourceKind.STACK for r in resources)


def test_compose_up_command_path():
    # cd into the stack dir so compose discovers compose.yaml OR docker-compose.yml
    assert cmp.up_command(STACKS_DIR, "blog") == "cd /srv/stacks/blog && docker compose up -d"
    assert cmp.down_command(STACKS_DIR, "blog") == "cd /srv/stacks/blog && docker compose down"


@pytest.mark.parametrize("evil", ["../etc", "a b", "a;rm", "UP", "stack/../x", "a$b"])
def test_compose_rejects_hostile_stack_names(evil):
    with pytest.raises(ValueError):
        cmp.up_command(STACKS_DIR, evil)
    with pytest.raises(ValueError):
        cmp.down_command(STACKS_DIR, evil)


def test_compose_accepts_valid_names():
    for name in ("blog", "open-webui", "supabase_db", "a1"):
        assert name in cmp.up_command(STACKS_DIR, name)


async def test_compose_stack_up_failure_surfaces():
    t = FakeTransport(label="stacks")
    t.respond(
        "cd /srv/stacks/blog && docker compose up -d",
        stderr="no such file",
        exit_code=1,
    )
    provider = build_command_provider("compose", "host-stacks", t, {"stacks_dir": STACKS_DIR})
    with pytest.raises(RuntimeError, match="no such file"):
        await provider.start("blog")


# --- auto-group ops integration (the group_for_ref fix + restart/logs wiring) ---


async def test_auto_group_member_is_addressable(db):
    # auto: groups have no listed members; a container must still resolve to its
    # group so per-resource ops (restart/logs) can run.
    config = _docker_config()
    located = config.group_for_ref("edge-docker/web")
    assert located is not None and located[0] == "containers"


async def test_docker_restart_runs_and_logs(db):
    config = _docker_config()
    t = FakeTransport(label="edge")
    t.respond("docker restart web")
    t.respond("docker logs --tail 100 web 2>&1", stdout="hello\n")
    ops = await _ops(db, config, {"edge": t})

    result = ops.restart("edge-docker/web")
    assert result["status"] == "restarting"  # returns immediately
    assert result["group"] == "containers"
    await ops.drain()
    assert "docker restart web" in t.calls

    assert await ops.logs("edge-docker/web", 100) == "hello\n"


async def test_logs_unknown_provider_raises_keyerror(db):
    config = _docker_config()
    ops = await _ops(db, config, {"edge": FakeTransport()})
    # provider not in config / not tracked by any group → 404 at the route
    with pytest.raises(KeyError):
        await ops.logs("ghost/x", 50)


async def test_logs_unsupported_on_vm_provider(db, config, transports):
    # libvirt declares no LOGS capability — ops.logs must bubble Unsupported
    # (the route maps it to 404), never silently return nothing.
    ops = await _ops(db, config, transports)
    with pytest.raises(Unsupported):
        await ops.logs("host01-kvm/gateway-02", 100)
