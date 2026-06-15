# Dubdeck Provider Reference

> Auto-generated from `backend/app/providers/`. Updated 2026-06-15.

---

## Implemented Providers

### 1 · libvirt — KVM / virsh

| | |
|---|---|
| **Type name** | `libvirt` |
| **Backend** | `virsh` (libvirt CLI) over SSH |
| **Transport** | `CommandProvider` (SSHTransport) |
| **API** | None — shell commands over SSH |
| **Kind** | VM |
| **Status** | ✅ Current |

**Commands:**

| Operation | Command |
|---|---|
| List all VMs | `virsh list --all` |
| Start VM | `virsh start <vm>` |
| Stop (graceful) | `virsh shutdown <vm>` |
| Force stop | `virsh destroy <vm>` |
| Suspend / save RAM | `virsh managedsave <vm>` |
| Snapshot list | `virsh snapshot-list <vm>` |
| Snapshot create | `virsh snapshot-create-as <vm> --name <name>` |
| Disk stats | `virsh domstats --block --state` |

**Capabilities:** `start` · `stop` · `force_stop` · `suspend` · `snapshot_list` · `snapshot_create` · `disk_stats`

**Notes:**
- `virsh shutdown` is fire-and-forget ACPI — some guests ignore it. Ops layer escalates to `force_stop` after grace window.
- `virsh destroy` does NOT undefine the VM — recoverable via snapshot.
- `stop_is_graceful = True` (triggers escalation loop)

---

### 2 · parallels — macOS Parallels / prlctl

| | |
|---|---|
| **Type name** | `parallels` |
| **Backend** | `prlctl` (Parallels CLI) over SSH |
| **Transport** | `CommandProvider` (SSHTransport) |
| **API** | None — shell commands over SSH |
| **Kind** | VM |
| **Status** | ✅ Current |

**Commands:**

| Operation | Command |
|---|---|
| List all VMs | `prlctl list --all -o name,status` |
| Start VM | `prlctl start <vm>` |
| Stop (graceful) | `prlctl stop <vm>` |
| Force stop | `prlctl stop <vm> --kill` |
| Suspend / save RAM | `prlctl suspend <vm>` |
| Snapshot list | `prlctl snapshot-list <vm> -j` |
| Snapshot create | `prlctl snapshot <vm> -n <name>` |
| Disk stats | `du -sk <dir>.pvm` (via forced-command shim `dubdeck-vm-disks`) |

**Capabilities:** `start` · `stop` · `force_stop` · `suspend` · `snapshot_list` · `snapshot_create` · `disk_stats`

**Notes:**
- `prlctl stop` blocks until clean shutdown completes — no escalation loop needed.
- `stop_is_graceful = False`
- Disk stats route through a forced-command SSH shim (not raw `du`) for security hardening.

---

### 3 · docker — Docker containers / docker

| | |
|---|---|
| **Type name** | `docker` |
| **Backend** | `docker` CLI over SSH |
| **Transport** | `CommandProvider` (SSHTransport) |
| **API** | None |
| **Kind** | CONTAINER |
| **Status** | ✅ Current |

**Commands:**

| Operation | Command |
|---|---|
| List containers | `docker ps -a --format '{{json .}}'` |
| Start | `docker start <id>` |
| Stop | `docker stop <id>` |
| Restart | `docker restart <id>` |
| Logs | `docker logs --tail <n> <id> 2>&1` |

**Capabilities:** `start` · `stop` · `restart` · `logs`

**Notes:**
- No snapshot or suspend (containers don't support it)
- State from JSON `State` field — not the human `Status` string (avoids parsing "Up 3 days (healthy)")
- `stop_is_graceful = False` (`docker stop` does SIGTERM→SIGKILL internally)

---

### 4 · compose — Docker Compose stacks / docker compose

| | |
|---|---|
| **Type name** | `compose` |
| **Backend** | `docker compose` over SSH |
| **Transport** | `CommandProvider` (SSHTransport) |
| **API** | None |
| **Kind** | STACK |
| **Status** | ✅ Current |

**Commands:**

| Operation | Command |
|---|---|
| List stacks | `docker compose ls -a --format json` |
| Start | `cd <stack_dir> && docker compose up -d` |
| Stop | `cd <stack_dir> && docker compose down` |
| Restart | `cd <stack_dir> && docker compose restart` |

**Capabilities:** `start` · `stop` · `restart`

**Notes:**
- Stacks filtered to those whose compose file lives under configured `stacks_dir`
- Stack names validated against `^[a-z0-9_-]+$` before path construction (reject, don't quote)
- `stop_is_graceful = False`

---

### 5 · hyperv — Windows Hyper-V / PowerShell

| | |
|---|---|
| **Type name** | `hyperv` |
| **Backend** | PowerShell over SSH (OpenSSH to Windows host) |
| **Transport** | `CommandProvider` (SSHTransport, PowerShell shell) |
| **API** | None — PowerShell cmdlets over SSH |
| **Kind** | VM |
| **Status** | ✅ Current |

**Commands:**

| Operation | PowerShell |
|---|---|
| List VMs | `Get-VM | Select-Object Name,@{Name='State';Expression={$_.State.ToString()}} \| ConvertTo-Json -Compress` |
| Start VM | `Start-VM -Name '<vm>'` |
| Stop (graceful) | `Stop-VM -Name '<vm>' -Force` |
| Force stop | `Stop-VM -Name '<vm>' -TurnOff -Force` |
| Suspend / save RAM | `Save-VM -Name '<vm>'` |
| Snapshot list | `Get-VMCheckpoint -VMName '<vm>' | Select-Object Name,@{Name='Created';Expression={$_.CreationTime.ToString('o')}} \| ConvertTo-Json -Compress` |
| Snapshot create | `Checkpoint-VM -Name '<vm>' -SnapshotName '<name>'` |

**Capabilities:** `start` · `stop` · `force_stop` · `suspend` · `snapshot_list` · `snapshot_create`

**Notes:**
- Uses PowerShell single-quoted literals for injection protection — NOT `shlex.quote` (POSIX shell quoting, wrong for PowerShell)
- State forced to string via `.ToString()` for PowerShell-version stability (5.1 serialises enums as integers, 7 as strings)
- `stop_is_graceful = True` (ACPI-like escalation to `-TurnOff`)
- UTF-8 BOM stripped defensively

---

### 6 · proxmox — Proxmox VE / REST API

| | |
|---|---|
| **Type name** | `proxmox` |
| **Backend** | Proxmox REST API (`/api2/json/`) |
| **Transport** | `HttpClient` (direct HTTPS, no SSH) |
| **API** | Proxmox API token auth (`PVEAPIToken=<token_id>=<secret>`) |
| **Kind** | VM (qemu + lxc, both surfaced as `kind=VM`) |
| **Status** | 🛣️ Untested |

**API endpoints used:**

| Operation | Method | Path |
|---|---|---|
| List VMs | GET | `/api2/json/nodes` → `/api2/json/nodes/{node}/{vtype}` |
| Start VM | POST | `/api2/json/nodes/{node}/{vtype}/{vmid}/status/start` |
| Stop (graceful) | POST | `/api2/json/nodes/{node}/{vtype}/{vmid}/status/shutdown` |
| Force stop | POST | `/api2/json/nodes/{node}/{vtype}/{vmid}/status/stop` |
| Suspend | POST | `/api2/json/nodes/{node}/{vtype}/{vmid}/status/suspend` |
| Snapshot list | GET | `/api2/json/nodes/{node}/{vtype}/{vmid}/snapshot` |
| Snapshot create | POST | `/api2/json/nodes/{node}/{vtype}/{vmid}/snapshot` |
| Disk stats | (from list payload) | `maxdisk` field per guest |
| Node stats | GET | `/api2/json/nodes/{node}/status` |

**Capabilities:** `start` · `stop` · `force_stop` · `suspend` · `snapshot_list` · `snapshot_create` · `disk_stats`

**Notes:**
- First **API provider** — no Transport/SSH, uses `HttpClient` directly
- Mutations are async (POST returns a UPID task handle, polled to completion)
- Resource id format: `{node}/{vmid}` (e.g. `proxmox-node-01/100`)
- Both `qemu` (full VMs) and `lxc` (containers) tracked
- `stop_is_graceful` follows libvirt pattern (escalation to force_stop)

---

## Missing Provider

### 7 · xcp — XCP-ng / XAPI (XML-RPC)

| | |
|---|---|
| **Type name** | `xcp` |
| **Backend** | `xe` CLI over SSH (XCP-ng host) |
| **Transport** | `CommandProvider` (SSHTransport) |
| **API** | None — `xe` commands over SSH |
| **Kind** | VM |
| **Status** | 🛣️ Untested — implementation done, pending live test |

**Commands:**

| Operation | Command |
|---|---|
| List all VMs | `xe vm-list --all` |
| Start VM | `xe vm-start uuid=<uuid>` |
| Stop (graceful) | `xe vm-shutdown uuid=<uuid>` |
| Force stop | `xe vm-force-shutdown uuid=<uuid>` |
| Restart | `xe vm-shutdown uuid=<uuid> && xe vm-start uuid=<uuid>` |
| Suspend / save RAM | `xe vm-suspend uuid=<uuid>` |
| Snapshot list | `xe snapshot-list uuid=<uuid>` |
| Snapshot create | `xe snapshot-create uuid=<uuid> snapshot-name-label=<name>` |
| Disk stats | `xe vdi-list vm-uuid=<uuid>` |
| Logs / console | `xe vm-param-get uuid=<uuid> param-name=console-uri` |

**Capabilities:** `start` · `stop` · `force_stop` · `restart` · `suspend` · `snapshot_list` · `snapshot_create` · `logs` · `disk_stats`

**Notes:**
- Pattern matches Proxmox (first API provider) — XenAPI is XML-RPC rather than REST, but the architecture is identical: async tasks, session auth, no shell commands
- **Treadstone already uses XAPI** on Mars/Zeus/Gamera — confirmed working in your environment
- Auth: `session.login_with_password(user, pass)` → session ref used in all subsequent calls
- VMs: protected flag must be checked; snapshot of a protected VM may need unprotection first
- Pool master: some calls route to pool master only (`host.call_plugin` etc.)
- This is the highest-priority missing provider for the n1h lab

---

## Capability Matrix

| Provider | start | stop | force_stop | suspend | restart | snapshot_list | snapshot_create | logs | disk_stats |
|---|:---:|:---:|:---:|:---:|:---:|:---:|:---:|:---:|:---:|
| libvirt (virsh) | ✅ | ✅ | ✅ | ✅ | — | ✅ | ✅ | — | ✅ |
| parallels (prlctl) | ✅ | ✅ | ✅ | ✅ | — | ✅ | ✅ | — | ✅ |
| docker | ✅ | ✅ | — | — | ✅ | — | — | ✅ | — |
| compose | ✅ | ✅ | — | — | ✅ | — | — | — | — |
| hyperv | ✅ | ✅ | ✅ | ✅ | — | ✅ | ✅ | — | — |
| proxmox | ✅ | ✅ | ✅ | ✅ | — | ✅ | ✅ | — | ✅ |
| **xcp (xe CLI)** | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ |

---

## Transport Architecture

### CommandProvider (5 providers)
Driven by a **Transport** (SSH or local shell). Each operation is a command string run through the transport. Parsers are pure functions over captured output — no network calls, CI-safe with `FakeTransport`.

```
Dubdeck → Transport.run("virsh start <vm>") → SSH → hypervisor host → response
```

**Providers:** libvirt · parallels · docker · compose · hyperv

### API Provider (1 provider + planned xcp)
Direct **HttpClient** calls to a REST/XML-RPC endpoint. Auth tokens carried in headers. No shell involved.

```
Dubdeck → HttpClient.request(POST, /api2/json/nodes/.../status/start) → HTTPS → API server
```

**Providers:** proxmox · xcp (planned)

---

## Notes

- Every provider ships with a `FakeTransport` fixture for CI-safe testing — no real SSH connections made in tests.
- Config validation checks `host` (required for CommandProviders) vs `url`/token (required for API providers) at load time.
- Quoting strategy differs by shell: libvirt/parallels/docker/compose use `shlex.quote` (POSIX sh). Hyper-V uses PowerShell single-quoted literals (`ps_quote`) — not interchangeable.
- Snapshot restore and delete are **deliberately absent** from all providers — destructive ops stay manual.

---

## Parallels Provider (Future — Mac Mini 2013)

**Status:** Future roadmap — Mac Mini 2013 is offline, needs to be powered on and configured.

**Use case:** Standalone Parallels test environment for Dubdeck, independent of Mr Awesome's setup.

**Mac Mini 2013 specs:**
- Haswell platform, can run macOS 12+ (Monterey/Big Sur)
- Parallels Desktop 18 supports macOS 12+, should work ✅
- `prlctl` CLI available with Parallels installed

**What to do when Mac Mini is available:**
1. Install Parallels Desktop (if not already installed)
2. Enable remote access (System Preferences → Sharing → Remote Login)
3. Add SSH key to `~/.ssh/authorized_keys` on the Mac
4. Install Parallels command line tools (`prlctl`)
5. Configure Dubdeck Parallels provider with Mac Mini IP
6. Test: list VMs, start/stop, snapshots

**Provider:** Already implemented in `backend/app/providers/parallels.py` — `CommandProvider` via SSH + `prlctl` commands.

**Ref:** `docs/PROVIDER-REFERENCE.yaml` — provider type `parallels`
