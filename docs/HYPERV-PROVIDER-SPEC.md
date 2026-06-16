# Hyper-V Provider for Dubdeck — Specification
**Author:** N1H Tech (Gemma)
**Date:** 2026-06-15
**Status:** Draft
**Target:** Windows machine with Hyper-V + 1 test VM

---

## Overview

The Hyper-V provider manages VMs on a Windows Hyper-V host via **PowerShell remoting over WinRM** (or SSH with OpenSSH). It is similar to the XCP-ng provider but uses PowerShell commands instead of an XML-RPC API.

**Why:** Dubdeck needs a test hypervisor for validating provider connections without touching production infrastructure (__XCPNG_HOST__). A Windows machine with Hyper-V + one test VM provides a clean isolated target.

---

## Hyper-V Remote Management Options

| Method | Protocol | Port | Notes |
|--------|----------|------|-------|
| PowerShell Remoting | WinRM (HTTP) | 5985 | Default, unencrypted |
| PowerShell Remoting | WinRM (HTTPS) | 5986 | Encrypted, requires cert |
| OpenSSH | SSH | 22 | If OpenSSH installed on Hyper-V host |
| Windows Admin Center | REST/HTTPS | 443 | Web-based management |
| WMI | DCOM/WinRM | 135/5985 | CIM namespace root/virtualization |

**Recommended:** PowerShell Remoting over WinRM (HTTPS) — most straightforward for a Windows host.

---

## PowerShell Commands

### List VMs
```powershell
Get-VM | Select-Object Name, Id, State, CPUUsage, MemoryAssigned, Uptime | ConvertTo-Json -Compress
```

### VM Status
```powershell
Get-VM -Name '<vm-name>' | Select-Object Name, Id, State, CPUUsage, MemoryAssigned, Uptime, Version | ConvertTo-Json -Compress
```

### Start VM
```powershell
Start-VM -Name '<vm-name>'
```

### Stop VM (graceful)
```powershell
Stop-VM -Name '<vm-name>'
```

### Stop VM (force/hard)
```powershell
Stop-VM -Name '<vm-name>' -Force
```

### Restart VM
```powershell
Restart-VM -Name '<vm-name>'
```

### Pause/Suspend VM
```powershell
Suspend-VM -Name '<vm-name>'
```

### Resume VM
```powershell
Resume-VM -Name '<vm-name>'
```

### List Snapshots
```powershell
Get-VMSnapshot -VMName '<vm-name>' | Select-Object Name, CreationTime, Id | ConvertTo-Json -Compress
```

### Create Snapshot
```powershell
Checkpoint-VM -Name '<vm-name>' -SnapshotName '<snap-name>'
```

### Apply Snapshot (rollback)
```powershell
Restore-VMSnapshot -VMName '<vm-name>' -Name '<snap-name>'
```

### Delete Snapshot
```powershell
Remove-VMSnapshot -VMName '<vm-name>' -Name '<snap-name>'
```

### Get VM Console (RDP info)
```powershell
Get-VM -Name '<vm-name>' | Select-Object RDPConnectionSettings | ConvertTo-Json -Compress
```
Note: Hyper-V doesn't have a native web console like XCP-ng. RDP is used for console access.

### VM Disk Stats
```powershell
Get-VMHardDiskDrive -VMName '<vm-name>' | Select-Object Path, ControllerType, ControllerNumber, ControllerLocation | ConvertTo-Json -Compress
```

---

## Resource Identification

```
ref format: <provider-id>/<vm-name>
example:  hyperv-test/my-test-vm

kind: ResourceKind.VM
```

---

## Capability Set

| Capability | Implementation | Notes |
|---|---|---|
| `START` | `Start-VM -Name '<vm>'` | |
| `STOP` | `Stop-VM -Name '<vm>'` | graceful |
| `FORCE_STOP` | `Stop-VM -Name '<vm>' -Force` | hard stop |
| `RESTART` | `Restart-VM -Name '<vm>'` | |
| `SUSPEND` | `Suspend-VM -Name '<vm>'` | |
| `SNAPSHOT_LIST` | `Get-VMSnapshot -VMName '<vm>'` | |
| `SNAPSHOT_CREATE` | `Checkpoint-VM -Name '<vm>' -SnapshotName '<name>'` | |
| `SNAPSHOT_DELETE` | `Remove-VMSnapshot -VMName '<vm>' -Name '<snap>'` | |
| `DISK_STATS` | `Get-VMHardDiskDrive -VMName '<vm>'` | |
| `LOGS` | Not available via PS | Event logs via WinRM |
| `CONSOLE` | RDP URL or `mstsc` launcher | Hyper-V has no web console |

**NOT implemented initially:**
- `LOGS` — no native Hyper-V log API via PS remoting
- `CONSOLE` — RDP only, no web-based console

---

## Transport

### Option A: PowerShell Remoting (WinRM)
Requires:
- WinRM enabled on Hyper-V host (`Enable-PSRemoting`)
- Credentials (username + password or NTLM/Kerberos auth)
- Network access to port 5985 (HTTP) or 5986 (HTTPS)

Python: `pywinrm` library
```python
import winrm
session = winrm.Session('https://<host>:5986/wsman', auth=(user, pass), transport='ntlm')
```

### Option B: SSH (if OpenSSH installed)
```bash
ssh user@hyperv-host 'powershell -Command "Get-VM | ConvertTo-Json -Compress"'
```

### Option C: Windows Admin Center REST API
Requires WAC installed on the host or a management machine.
- `GET /api/sources/hyperv/hosts/<host>/vms`
- `POST /api/sources/hyperv/hosts/<host>/vms/<vm>/start`

---

## Config Example

```yaml
providers:
  - id: hyperv-test
    type: hyperv
    host: 192.168.0.xx
    username: Administrator
    password_env: HYPERV_PASSWORD
    transport: winrm        # winrm | ssh | wac
    verify_tls: false       # true for HTTPS with valid cert
    port: 5986              # default: 5986 (HTTPS) or 5985 (HTTP)

groups:
  hyperv-vms:
    label: "Hyper-V VMs"
    auto: hyperv-test
```

**Set password:**
```bash
export HYPERV_PASSWORD=<password>
```

---

## Implementation Plan

1. **Python WinRM library:** Install `pywinrm` on __HOST__
2. **WinRM on Hyper-V host:** Enable `Enable-PSRemoting` (run once on the Windows host)
3. **Test connectivity:** `winrm ping` or `Test-WSMan`
4. **Implement** `backend/app/providers/hyperv.py` — `HyperVProvider` class
5. **Auth:** NTLM auth with username/password via `pywinrm`
6. **Commands:** PowerShell cmdlets via `session.run_ps()` or `session.run_cmd()`
7. **Test:** List VMs, start/stop test VM, verify snapshots

---

## Key Differences from XCP-ng Provider

| Aspect | XCP-ng | Hyper-V |
|---|---|---|
| API | XenAPI XML-RPC (port 443) | PowerShell over WinRM (port 5985/5986) |
| Auth | session.login_with_password | NTLM/NTLM with username/password |
| VM ID | OpaqueRef | VM name (string) |
| Console | HTML5 via XCP-ng web | RDP only (no web console) |
| Snapshot | XenAPI snapshot | `Checkpoint-VM` / `Restore-VMSnapshot` |
| Transport | HTTP(S) directly | WinRM (SOAP over HTTP) |

---

## Test VM Requirements

- Windows VM on Hyper-V (any Windows version)
- At least 1 vCPU, 2GB RAM, 30GB disk
- For initial testing: a simple Windows or Linux VM
- RDP enabled for console access
