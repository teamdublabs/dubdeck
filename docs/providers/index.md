# Providers

A **provider** is one instance of a provider type that Dubdeck uses to manage a
set of resources. You declare providers in `config.yaml`; the UI renders only the
actions each provider has declared it supports.

There are two provider families:

- **Command providers** reach their host over a transport (SSH or local) and drive
  it with CLI tools (`prlctl`, `virsh`, `docker`, `pwsh`, …).
- **API providers** communicate with an HTTP API; no SSH host is required.

---

## Capability matrix

The table below shows which operations each provider supports. Columns correspond
to `Capability` values declared as `frozenset` in each provider's class — the UI
renders buttons only for declared capabilities.

| Provider | Kind | Family | start | stop | force\_stop | suspend | snapshot | logs | disk\_stats | Status |
|---|---|---|:---:|:---:|:---:|:---:|:---:|:---:|:---:|---|
| [parallels](parallels.md) | vm | command | ✓ | ✓ | ✓ | ✓ | ✓ | — | ✓ | stable |
| [libvirt](libvirt.md) | vm | command | ✓ | ✓ | ✓ | ✓ | ✓ | — | ✓ | stable |
| [docker](docker.md) | container | command | ✓ | ✓ | — | — | — | ✓ | — | stable |
| [compose](compose.md) | stack | command | ✓ | ✓ | — | — | — | — | — | stable |
| [proxmox](proxmox.md) | vm | api | ✓ | ✓ | ✓ | ✓ | ✓ | — | ✓ | beta |
| [hyperv](hyperv.md) | vm | command | ✓ | ✓ | ✓ | ✓ | ✓ | — | — | experimental |

**snapshot** = both `snapshot_list` and `snapshot_create`; these are always
declared together.

**Status:**
- **stable** — exercised against real hardware in the reference deployment and
  covered by the full test suite.
- **beta** — tested against API mocks and documented response shapes; not yet
  battle-tested against live hardware. Bug reports welcome.
- **experimental** — implemented and unit-tested against documented command output;
  not yet exercised against live hardware. You are an early user.

---

## Provider docs

| Provider | Type | Resources |
|---|---|---|
| [parallels](parallels.md) | `parallels` | Parallels Desktop VMs on a Mac host |
| [libvirt](libvirt.md) | `libvirt` | KVM/QEMU VMs via `virsh` on a Linux host |
| [docker](docker.md) | `docker` | Docker containers on any host running the Docker CLI |
| [compose](compose.md) | `compose` | Docker Compose stacks in a `stacks_dir` directory |
| [proxmox](proxmox.md) | `proxmox` | QEMU VMs and LXC containers on a Proxmox VE cluster |
| [hyperv](hyperv.md) | `hyperv` | Hyper-V VMs on a Windows host via SSH + PowerShell |

---

## Adding a provider to your config

Every provider must be declared in `config.yaml` with a unique `id` and a `type`
from the list above. Command providers also need a `host`; API providers need `url`
and token fields. See [configuration.md](../configuration.md) for the full schema.

```yaml
hosts:
  server01:
    transport: ssh
    address: 192.0.2.10
    user: labuser

providers:
  - id: server01-kvm
    type: libvirt
    host: server01

  - id: server01-docker
    type: docker
    host: server01
```

Once declared, resources managed by a provider can be referenced in `groups:` as
`provider-id/resource-id`.
