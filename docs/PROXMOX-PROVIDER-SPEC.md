# Proxmox Provider for Dubdeck — Specification
**Author:** N1H Tech (Gemma)
**Date:** 2026-06-15
**Status:** Draft
**Target:** Proxmox VE 8.x on Freya (__IP__)

---

## Overview

The Proxmox provider manages VMs and LXC containers on a Proxmox VE host via the REST API.
It is analogous to the XCP-ng provider but uses the Proxmox API instead of XenAPI.

**Installation target:** Freya (__IP__) — physical host, clean Debian install, 7.2GB RAM, 110GB disk.

---

## Proxmox API

### Auth
```
POST /api2/json/access/ticket
Body: {"username": "root@pam", "password": "<pass>"}
200: {"data": {"ticket": "PVE=...", "token": "...", "username": "root@pam", "CSRFPreventionToken": "..."}}
```
The `ticket` is a cookie value (`PVE=...`). All subsequent requests use:
- Cookie: `PVEAuthCookie=PVE=...`
- Header: `CSRFPreventionToken: <token>`

### Nodes
```
GET /api2/json/nodes
→ { "data": [{"node": "__HOST__", "status": "online", ...}] }
```

### VMs (QEMU/KVM)
```
GET /api2/json/nodes/<node>/qemu
→ {"data": [{ "vmid": "100", "name": "vm-name", "status": "running", "type": "qemu", ... }]}

GET /api2/json/nodes/<node>/qemu/<vmid>/status/current
→ full VM status
```

### LXC Containers
```
GET /api2/json/nodes/<node>/lxc
→ {"data": [{ "vmid": "100", "name": "container-name", "status": "running", "type": "lxc" }]}
```

### VM Lifecycle Actions
```
POST /api2/json/nodes/<node>/qemu/<vmid>/status/start
POST /api2/json/nodes/<node>/qemu/<vmid>/status/stop
POST /api2/json/nodes/<node>/qemu/<vmid>/status/shutdown   # graceful
POST /api2/json/nodes/<node>/qemu/<vmid>/status/reset
POST /api2/json/nodes/<node>/qemu/<vmid>/status/suspend
POST /api2/json/nodes/<node>/qemu/<vmid>/status/resume
```

### Snapshot Lifecycle
```
GET  /api2/json/nodes/<node>/qemu/<vmid>/snapshot
POST /api2/json/nodes/<node>/qemu/<vmid>/snapshot
     Body: {"snapname": "<name>"}
DELETE /api2/json/nodes/<node>/qemu/<vmid>/snapshot/<snapname>
POST /api2/json/nodes/<node>/qemu/<vmid>/snapshot/<snapname>/rollback
     Body: {"snapname": "<name>"}
```

### Console (noVNC/SPICE)
```
GET /api2/json/nodes/<node>/qemu/<vmid>/terminal
→ {"data": {"port": 5900, "tickit": "..."}}
Console URL: /?console=...
```

### Cluster-wide resources
```
GET /api2/json/cluster/resources
→ all nodes + VMs + storage in one call
```

---

## Resource Identification

```
ref format: <provider-id>/<node>/<vmid>
example:  proxmox-__HOST__/__HOST__/100

kind: ResourceKind.VM (for qemu), ResourceKind.CONTAINER (for lxc)
```

---

## Capability Set

| Capability | Implementation | Notes |
|---|---|---|
| `START` | `POST /nodes/<node>/qemu/<vmid>/status/start` | |
| `STOP` | `POST /nodes/<node>/qemu/<vmid>/status/stop` | immediate |
| `FORCE_STOP` | `POST /nodes/<node>/qemu/<vmid>/status/reset` | hard reset |
| `RESTART` | stop + start | |
| `SUSPEND` | `POST /nodes/<node>/qemu/<vmid>/status/suspend` | |
| `SNAPSHOT_LIST` | `GET /nodes/<node>/qemu/<vmid>/snapshot` | |
| `SNAPSHOT_CREATE` | `POST /nodes/<node>/qemu/<vmid>/snapshot` | |
| `SNAPSHOT_DELETE` | `DELETE /nodes/<node>/qemu/<vmid>/snapshot/<snap>` | |
| `LOGS` | `GET /nodes/<node>/qemu/<vmid>/config` + `POST /nodes/<node>/qemu/<vmid>/status/start` → journal | via kvm status |
| `DISK_STATS` | `GET /cluster/resources` → `disk` field | |
| `CONSOLE` | `GET /nodes/<node>/qemu/<vmid>/terminal` → port/URL | noVNC console |

**NOT implemented initially:**
- LXC container support (can be added later)
- Storage management

---

## Config Example

```yaml
providers:
  - id: proxmox-__HOST__
    type: proxmox
    host: __IP__
    username: root@pam
    token_secret_env: PROXMOX_PASSWORD
    verify_tls: false

groups:
  __HOST__-vms:
    label: "Freya VMs"
    auto: proxmox-__HOST__
```

**Set password:**
```bash
export PROXMOX_PASSWORD=__PASSWORD__
```

---

## N1H Tech Deployment

| | |
|---|---|
| Host | Freya (__IP__) |
| OS | Debian (physical host) |
| RAM | 7.2GB total, ~6.5GB available |
| Disk | 110GB, 81GB free |
| Proxmox install | TBD (clean Debian → Proxmox VE 8.x) |
| Default user | root@pam |
| Password | `__PASSWORD__` (Freya's root password) |

**Backup note:** Pacman is backing up Freya before installing Proxmox. Freya was previously the Ops Center hub (agent-ops-center, treadstone, picoclaw-ops, treadstone-ops services running).

---

## Implementation Plan

1. **Install Proxmox VE 8.x** on Freya (clean install, not in-place upgrade)
2. **API access:** Enable `root@pam` access (default, password auth works)
3. **TLS:** Proxmox self-signed cert — `verify_tls: false`
4. **Implement** `backend/app/providers/proxmox.py` — `ProxmoxProvider` class
5. **Auth:** Cookie + CSRF token from `/access/ticket`, reuse until 401
6. **Test:** List VMs, start/stop a test VM, verify console URL

---

## Key Differences from XCP-ng Provider

| Aspect | XCP-ng | Proxmox |
|---|---|---|
| API | XenAPI XML-RPC (port 443) | REST API JSON (port 8006) |
| Auth | session.login_with_password | access/ticket → cookie |
| VM ID | OpaqueRef | vmid (integer, e.g. 100) |
| Container type | qemu only | qemu + lxc |
| Snapshot | XenAPI snapshot | Proxmox snapshot (snapname) |
| Console | XCP-ng HTML5 console | noVNC via `/terminal` endpoint |
| Cluster | pool model | multi-node cluster |
