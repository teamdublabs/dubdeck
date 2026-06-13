# XCP-ng Provider for Dubdeck — Specification
**Author:** N1H Tech (Gemma)  
**Date:** 2026-06-13  
**Status:** Draft — for proposed contribution to teamdublabs/dubdeck  
**Based on:** Dubdeck v0.1.0 provider architecture  

---

## Context

Mr.Awesome built Dubdeck in 24 hours using Fable 5, deliberately architecting it so a successor AI agent could continue the work. The Fable 5 ban hit before the handoff was complete. Mr.Awesome is US-based, has Qwen3.6 on his own Ollama server, and is a member of the ResonantOS community.

N1H Tech operates 5 XCP-ng hypervisors (Mars, Zeus, Gamera, Saturn + others) and needs Dubdeck to work with XCP-ng as a first-class provider. This spec defines the provider so it can be implemented directly in the Dubdeck codebase.

---

## XCP-ng vs libvirt — Why a Separate Provider

XCP-ng is a stripped XenServer fork. Unlike KVM/libvirt, it:

- Has **no `virsh`** in the traditional sense — `xe` is the CLI tool, not `virsh`
- Uses **XenAPI XML-RPC** (port 443 HTTPS) as its primary management API
- Identifies VMs by **UUID** (not name-label alone — names can collide across pools)
- Has a **pool model** — VMs belong to pools; pool-master is the control endpoint
- Supports **templates, snapshots, VM resilience** via a different command surface

The libvirt provider in Dubdeck uses `virsh` commands. An XCP-ng provider needs its own command builders using `xe`. The structure is analogous but the commands are different.

---

## Resource Identification

XCP-ng VMs have two identifiers:
- **UUID** — globally unique, stable across pool-failover. Used as the canonical resource `id`.
- **name-label** — human-readable, can collide. Used as `name` in Resource.

```
ref format: <provider-id>/<vm-uuid>
example:  lab-xcpng/01234567-89ab-cdef-0123-456789abcdef
```

The provider must use UUID in all `xe` commands to avoid ambiguity.

---

## Command Surface

### VM Lifecycle

| Operation | xe command |
|---|---|
| List all VMs | `xe vm-list --all` |
| Start VM | `xe vm-start uuid=<uuid>` |
| Graceful stop | `xe vm-shutdown uuid=<uuid>` |
| Force stop | `xe vm-force-shutdown uuid=<uuid>` |
| Suspend (RAM to disk) | `xe vm-suspend uuid=<uuid>` |
| Pause | `xe vm-pause uuid=<uuid>` |
| Unpause | `xe vm-unpause uuid=<uuid>` |
| Restart | `xe vm-reboot uuid=<uuid>` (graceful) |
| Force restart | `xe vm-reboot force=true uuid=<uuid>` |

### Snapshot Lifecycle

| Operation | xe command |
|---|---|
| List snapshots | `xe snapshot-list uuid=<uuid>` |
| Create snapshot | `xe snapshot-create uuid=<uuid> snapshot-name-label=<name>` |
| Delete snapshot | `xe snapshot-delete uuid=<snapshot-uuid>` |
| Revert to snapshot | `xe snapshot-revert uuid=<snapshot-uuid>` |

Note: Snapshot create returns a new UUID. Snapshot list for a VM shows child snapshots only (not the VM's current state).

### Disk Stats

| Operation | xe command |
|---|---|
| VM disk info | `xe vdi-list vm-uuid=<uuid>` |
| SR disk usage | `xe sr-list` + `xe sr-param-get uuid=<sr> param-name=physical-size` |

### Host Stats

| Operation | xe command |
|---|---|
| Host list | `xe host-list` |
| Host CPU/RAM | `xe host-data-source-list uuid=<host>` |
| Pool master | `xe pool-list` |

---

## Parser Specifications

### `parse_vm_list(output: str) -> dict[str, VMState]`

Input: `xe vm-list --all` output — multi-line, columnar with labels.

```
Name-label (u)                      : my-vm
PV CPU weight (u)                   : 1
PV arguments (u)                    : 
...
Power-state (u)                     : running
```

One block per VM. Extract:
- `name-label` → `name` (display name)
- `uuid` → `id` (canonical identifier)
- `power-state` → `state` mapping:
  - `running` → `ResourceState.RUNNING`
  - `halted` → `ResourceState.STOPPED`
  - `suspended` → `ResourceState.SUSPENDED`
  - `paused` → `ResourceState.PAUSED`
  - _ → `ResourceState.UNKNOWN`

VMs with `is-a-template = true` are excluded from the resource list.

### `parse_snapshot_list(output: str) -> list[Snapshot]`

Input: `xe snapshot-list uuid=<vm-uuid>` output.

```
uuid ( RO)                         : abcdef12-3456-7890-abcd-ef1234567890
name-label ( RO)                   : dubdeck-20260613-103000
...
snapshot-of ( RO)                   : 01234567-89ab-cdef-0123-456789abcdef
```

For each snapshot:
- `uuid` → `name` (used as identifier)
- `name-label` → display name  
- Timestamp parsing: parse `name-label` for ISO-style dates (`dubdeck-YYYYMMDD-HHMMSS`) to populate `created`; otherwise use empty string

### `parse_host_stats(output: str) -> dict`

Input: `xe host-list` + per-host stats. Returns `{"<host-uuid>": {"load_1m": float, "mem_total": int, "mem_used": int, ...}}`

---

## Capability Set

```python
capabilities = frozenset({
    Capability.START,
    Capability.STOP,           # graceful (xe vm-shutdown)
    Capability.FORCE_STOP,    # xe vm-force-shutdown
    Capability.SUSPEND,       # xe vm-suspend
    Capability.SNAPSHOT_LIST,
    Capability.SNAPSHOT_CREATE,
    Capability.DISK_STATS,     # via xe vdi-list
})
```

**NOT implemented initially:**
- `CAPABILITY.RESTART` — XCP-ng has no single-command restart; implement as stop+start if needed
- `CAPABILITY.LOGS` — XCP-ng console log is accessible but requires separate handling
- Snapshot restore/delete — deliberately absent in libvirt provider; same reasoning applies

---

## Auth Model

XCP-ng has two auth modes:

### Mode 1 — Username/Password (simpler)
```bash
xe host-login username=root password=<pw> host=<address>
```
Not recommended for production — credentials on command line.

### Mode 2 — API Token (recommended)
XCP-ng API token via session login:
```bash
curl -k -H "Content-Type: text/xml" \
  -d '<?xml version="1.0"?><methodCall><methodName>session.login_with_password</methodName>...</methodCall>' \
  https://<host>/api/
```
Token returned, used in subsequent requests. Token cached and refreshed.

### SSH Tunnel Option (recommended for Dubdeck)
Use the existing `SSHTransport` — SSH to the XCP-ng host, run `xe` commands locally (no API token needed, no HTTPS cert issues). This is the cleanest approach for homelab use:
```yaml
hosts:
  __HOST__:
    transport: ssh
    address: __IP__
    user: root
    port: 22
providers:
  - id: lab-xcpng
    type: xcpng
    host: __HOST__
```

`xe` commands run as `xe <subcommand>` on the host — no path needed, `xe` is in PATH on XCP-ng by default.

---

## File Layout

```
backend/app/providers/
├── __init__.py          # add "xcpng" to PROVIDER_TYPES and is_command_type
├── xcpng.py             # ← new file: XCP-ng provider (xe command builders + parsers)
```

No new transport needed — `SSHTransport` handles the SSH hop to the XCP-ng host. Commands run via `xe` on the remote host.

---

## Config Example

```yaml
hosts:
  __HOST__:
    transport: ssh
    address: __IP__
    user: root
    port: 22
    stats: linux

providers:
  - id: lab-xcpng
    type: xcpng
    host: __HOST__

groups:
  research:
    label: "Research Lab"
    members:
      - lab-xcpng/01234567-89ab-cdef-0123-456789abcdef
      - lab-xcpng/fedcbade-badc-feff-0123-456789abcdef
    policies:
      start_first:
        - lab-xcpng/01234567-89ab-cdef-0123-456789abcdef
      ready_probe:
        ref: lab-xcpng/01234567-89ab-cdef-0123-456789abcdef
      snapshot_before_stop: true
```

---

## Priority Implementation Order

| Phase | Work | Rationale |
|---|---|---|
| 1 | `xe vm-list --all` parser + `list_resources()` | Foundation — everything depends on listing |
| 2 | Start/stop/force_stop/suspend | Core lifecycle ops |
| 3 | Snapshot list + create | Medium complexity, valuable |
| 4 | Disk stats | Low priority for v1 |
| 5 | Host stats | Nice-to-have for the status dashboard |

---

## Key Differences from libvirt Provider

| Aspect | libvirt (KVM) | XCP-ng |
|---|---|---|
| CLI tool | `virsh` | `xe` |
| VM identifier | name (string) | UUID (canonical), name-label (display) |
| Graceful stop | `virsh shutdown` | `xe vm-shutdown` |
| Force stop | `virsh destroy` | `xe vm-force-shutdown` |
| Suspend | `virsh managedsave` | `xe vm-suspend` |
| Snapshot create | `virsh snapshot-create-as` | `xe snapshot-create` |
| Template VMs | Excluded | Excluded via `is-a-template` flag |
| Pool awareness | No | Yes — pool-master is control point |
| Connection | libvirtd socket | SSHTransport → `xe` on host |

---

## Test Strategy

Same FakeTransport pattern as all other providers:
- `tests/fixtures/xe_vm_list_*` — captured `xe vm-list` output files
- `tests/fixtures/xe_snapshot_list_*` — captured `xe snapshot-list` output
- `FakeTransport` receives `xe vm-start uuid=...` and returns success/failure
- Contract tests verify declared capabilities match working methods

---

## N1H Tech Deployment Context

XCP-ng hypervisors available for testing:
| Host | IP | Role |
|---|---|---|
| Mars | __IP__ | Primary, 56GB RAM, 78 VMs |
| Zeus | __IP__ | Secondary (currently Dead) |
| Gamera | __IP__ | XCP-ng (currently Dead) |
| Saturn | __IP__ | XCP-ng |

Credentials: `root / __PASSWORD__` (Mars)

---

## Questions for Mr.Awesome

1. **Provider registration** — what's the process for adding a new provider type to Dubdeck? Is it just adding to `PROVIDER_TYPES` in `providers/__init__.py` and implementing the class, or is there a more formal PR process?
2. **Test fixtures** — should I add `tests/fixtures/xe_*.txt` files with captured real output from an XCP-ng host, or are fake fixtures acceptable for initial PR?
3. **Pool vs standalone** — should the provider handle pool-master failover (if the master goes down, commands route to another pool member), or assume single-host for v1?
4. **API token preference** — given that Dubdeck runs SSHTransport anyway, the SSH approach avoids API token management entirely. Is that acceptable or do you want API-token as the primary path?
