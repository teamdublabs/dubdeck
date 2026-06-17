# Configuration reference

Dubdeck's infrastructure configuration lives in a single YAML file — `config.yaml`
by default. It is declarative data: the hosts Dubdeck can execute on, the provider
instances that manage resources on those hosts, and the groups shown in the UI.

**What lives here vs. in settings**

`config.yaml` is for infrastructure (hosts, providers, groups). Things a user flips
at runtime — auth on/off, module toggles, branding — live in the app's settings
store (the Settings window in the UI) and are persisted in SQLite. You never need to
restart to change settings; you do need to restart to pick up config changes.

**Secrets never go in this file.** SSH keys are mounted separately; API token
secrets are referenced by environment variable name, never written inline.

---

## Top-level structure

```yaml
hosts:      # dict[name, Host]
providers:  # list[Provider]
groups:     # dict[name, Group]
modules:    # dict[name, any]  — opaque per-module config
```

A missing or empty `config.yaml` is valid — Dubdeck boots into an onboarding
screen instead of crashing.

---

## `hosts`

A host is a machine Dubdeck can execute commands on. The dict key is the host's
name, referenced by providers and egress module config.

```yaml
hosts:
  linux01:
    transport: ssh
    address: 192.0.2.10
    user: labuser
    port: 22
    stats: linux

  this-box:
    transport: local
    stats: linux
```

### Host fields

| Field | Type | Required | Default | Description |
|---|---|---|---|---|
| `transport` | `ssh` \| `local` | no | `ssh` | How to run commands. `ssh` opens an SSH connection; `local` runs subprocesses on the machine Dubdeck itself runs on (no SSH). |
| `address` | string | **yes, if ssh** | — | IP address or hostname the backend can reach. Prefer stable IPs (tailnet/VPN IPs) over DNS names that might resolve differently in your deployment. |
| `user` | string | **yes, if ssh** | — | SSH username. No default — this must be explicit. |
| `port` | integer | no | `22` | SSH port. |
| `stats` | `linux` \| `macos` \| `null` | no | `linux` | Source for host load/memory stats shown in the UI. `linux` reads `/proc` + `free` + `df`. `macos` uses the dubdeck-stats shim (see `docs/pro-tips`). `null` disables host stats for this machine. |

**Validation:** an `ssh` host with no `address` or no `user` is a startup error.
A `local` host ignores `address`, `user`, and `port`.

---

## `providers`

A provider is one instance of a provider type, bound to a host (command providers)
or a URL (API providers). The `id` is the stable name used to reference this
provider's resources throughout the config.

```yaml
providers:
  - id: linux01-kvm
    type: libvirt
    host: linux01
```

### Provider families

There are two provider families. Which fields are required depends on the family:

**Command providers** (parallels, libvirt, docker, compose, hyperv, virtualbox)
reach their host via a transport. They require `host`.

**API providers** (proxmox) talk to an HTTP API. They require `url`, `token_id`,
and `token_secret_env`. They do not accept `host`.

### Provider fields

| Field | Type | Required | Default | Description |
|---|---|---|---|---|
| `id` | string | yes | — | Unique name for this provider instance. Referenced as the `provider-id` part of resource refs (`provider-id/resource-id`). Must be unique across all providers. |
| `type` | string | yes | — | Provider type. One of: `parallels`, `libvirt`, `docker`, `compose`, `proxmox`, `hyperv`, `virtualbox`. |
| `host` | string | **yes, command providers** | — | Host name (must match a key in `hosts:`). |
| `stacks_dir` | string | **yes, compose** | — | Directory holding `<name>/compose.yaml` stacks. Required for compose providers, ignored for all others. |
| `url` | string | **yes, proxmox** | — | Proxmox API base URL, e.g. `https://192.0.2.20:8006`. |
| `token_id` | string | **yes, proxmox** | — | Proxmox API token ID in `user@realm!tokenid` form. |
| `token_secret_env` | string | **yes, proxmox** | — | Name of the environment variable holding the token secret. The secret is never written here — it is read from the environment at startup. If the variable is unset, the provider fails fast with a clear error. |
| `verify_tls` | boolean | no | `true` | Proxmox only. Set to `false` to skip TLS certificate verification (e.g. for a self-signed Proxmox certificate). A warning is logged at startup when this is off. |

### Validation

- Duplicate provider `id` values are a startup error.
- A `type` not in the registry is a startup error.
- Command providers require `host`; API providers require `url` + token fields.
- Compose providers require `stacks_dir`.
- A provider's `host` must reference a name declared in `hosts:`.

---

## `groups`

A group is a named collection of resources shown as one desktop window. The dict
key becomes the group's internal id.

```yaml
groups:
  research:
    label: "Research Lab"
    members:
      - linux01-kvm/research-fw
      - linux01-kvm/research-a
    policies:
      start_first: [linux01-kvm/research-fw]
      ready_probe: { ref: linux01-kvm/research-fw }
      snapshot_before_stop: true

  containers:
    label: "Containers"
    auto: linux01-docker
```

### Group fields

| Field | Type | Required | Default | Description |
|---|---|---|---|---|
| `label` | string | yes | — | Human-readable window title shown in the UI. |
| `members` | list[string] | no | `[]` | Explicit list of resource refs (`provider-id/resource-id`). Mutually exclusive with `auto`. |
| `auto` | string | no | — | Provider id. When set, the group tracks that provider's full live resource list instead of a fixed member list. Essential for Docker containers, which churn. Mutually exclusive with `members`. |
| `policies` | GroupPolicies | no | see below | Ordering and safety policies applied on bulk start/stop. |

### Resource refs

A resource ref is `provider-id/resource-id`. For most providers the resource id
is the VM or container name. For Proxmox it is `node/vmid` (e.g. `pve/pve01/100`
where `pve` is the provider id and `pve01/100` is the Proxmox node/vmid).

### `policies`

| Field | Type | Required | Default | Description |
|---|---|---|---|---|
| `start_first` | list[string] | no | `[]` | Resource refs to start first (in order) when a group starts, and stop last when it stops. Each ref must also be a member of this group. Generalises the "gateway tier" — bring the network up before the lab machines. |
| `ready_probe` | `{ ref: string }` | no | — | Wait until this resource reaches the `running` state before starting the remaining members. The ref must be a member. Without this, remaining members start immediately after the `start_first` resources are started. |
| `snapshot_before_stop` | boolean | no | `false` | Take a snapshot of every member (except `start_first` members) before stopping it. Keeps a recovery point after a lab session. Requires the provider to declare `snapshot_create`. |

### Validation

- A group cannot have both `members` and `auto`.
- An `auto` value must reference a known provider id.
- Every `members` entry must reference a known provider id.
- Every `start_first` ref must be a member of the same group.
- The `ready_probe` ref must be a member of the same group.

---

## `modules`

Module config lives under `modules:` as a dict keyed by module name. Core never
parses this section — each module reads its own key. Module toggles (on/off) live
in the settings store (the UI), not here.

```yaml
modules:
  egress:
    gateways:
      research:
        host: linux01        # via a core host's transport
        exit_node: relay-01
        mode: on-demand
```

The egress module is the only built-in optional module. It is off by default and
must be enabled in Settings. See `docs/pro-tips` for a worked egress setup.

---

## Full worked example

This example shows a two-host setup with KVM VMs, Docker containers, and Proxmox.

```yaml
hosts:
  server01:
    transport: ssh
    address: 192.0.2.10
    user: labuser
    port: 22
    stats: linux

  this-box:
    transport: local
    stats: linux

providers:
  # KVM VMs on a remote Linux server
  - id: server01-kvm
    type: libvirt
    host: server01

  # Docker containers on the same server
  - id: server01-docker
    type: docker
    host: server01

  # Compose stacks on the same server (Dockge-compatible layout)
  - id: server01-stacks
    type: compose
    host: server01
    stacks_dir: /home/labuser/docker/stacks

  # Proxmox VE — token secret in env var, never inline
  # - id: pve
  #   type: proxmox
  #   url: https://192.0.2.20:8006
  #   token_id: dubdeck@pam!dubdeck
  #   token_secret_env: DUBDECK_PVE_TOKEN
  #   verify_tls: true

groups:
  # A curated group with a gateway ordering policy
  research:
    label: "Research Lab"
    members:
      - server01-kvm/research-fw
      - server01-kvm/research-a
      - server01-kvm/research-b
    policies:
      start_first: [server01-kvm/research-fw]
      ready_probe: { ref: server01-kvm/research-fw }
      snapshot_before_stop: true

  # Auto-groups — track the provider's full live resource list
  containers:
    label: "Containers"
    auto: server01-docker

  stacks:
    label: "Compose Stacks"
    auto: server01-stacks
```

To use the Proxmox provider, uncomment the provider block and pass the secret:

```sh
# In compose.yaml environment: section, or an env file:
DUBDECK_PVE_TOKEN=xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx
```
