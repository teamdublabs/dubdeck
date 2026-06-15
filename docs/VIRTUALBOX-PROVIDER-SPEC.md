# VirtualBox Provider for Dubdeck — Specification
**Author:** N1H Tech (Gemma)
**Date:** 2026-06-15
**Status:** Draft — VirtualBox remote management has constraints

---

## Overview

VirtualBox is a Type 2 hypervisor (runs as an app on a host OS). Unlike XCP-ng/Proxmox which have native network APIs, VirtualBox is designed to be managed **locally** — VBoxManage talks to VBoxSVC on the same machine.

**Feasibility assessment: YES, but with constraints.**

---

## VirtualBox Remote Management Options

| Method | How it works | Feasibility |
|--------|-------------|-------------|
| **VBoxManage over SSH** | `ssh user@vbox-host VBoxManage <cmd>` | ✅ **Best approach** — same as Parallels |
| **vboxwebsrv (SOAP API)** | Old deprecated SOAP service (removed in v7.x) | ❌ Not available in modern VirtualBox |
| **python-virtualbox library** | COM/XPCOM calls to local vboxsvc | ❌ Local only, no network API |
| **VRDE (RDP display)** | Remote display only, not management | ❌ Display only, no control |

**Conclusion:** VBoxManage over SSH is the right approach — identical to how the Parallels provider works.

---

## VBoxManage Commands

### List VMs
```bash
VBoxManage list vms --long | grep -E "^Name:|UUID:|State:"
# Or simpler:
VBoxManage list vms
# Returns: "web-vm" {12345678-1234-1234-1234-123456789abc}
```

### VM Status
```bash
VBoxManage showvminfo <uuid|name> --machinereadable | grep ^VMState=
# Example: VMState="running"
```

### Start VM
```bash
VBoxManage startvm <uuid|name> --type headless
# --type: headless | gui | sdl | emergencystop
```

### Stop VM (graceful)
```bash
VBoxManage controlvm <uuid|name> savestate
# Saves RAM to disk and stops — cleanest stop for VirtualBox
```

### Stop VM (force/hard)
```bash
VBoxManage controlvm <uuid|name> poweroff
```

### Pause/Resume
```bash
VBoxManage controlvm <uuid|name> pause
VBoxManage controlvm <uuid|name> resume
```

### Snapshot List
```bash
VBoxManage snapshot <uuid|name> list --machinereadable
# Returns: SnapshotName="dubdeck-2026-06-15" UUID="..." SnapshotMachineAddress="..."
```

### Snapshot Create
```bash
VBoxManage snapshot <uuid|name> take <snap-name>
```

### Snapshot Restore
```bash
VBoxManage snapshot <uuid|name> restore <snap-name>
```

### Snapshot Delete
```bash
VBoxManage snapshot <uuid|name> delete <snap-name>
```

### Console (RDP)
```bash
VBoxManage showvminfo <uuid|name> | grep VRDP
# VRDE: enabled (Address 0.0.0.0, Ports 3389, ...)
# Connect via RDP client to <host-ip>:3389
```
Note: VirtualBox VRDE uses RDP (3389) — not a web console. The frontend would open an RDP URL or provide the port number.

---

## Resource Identification

```
ref format: <provider-id>/<vm-uuid>
example:  virtualbox-local/web-vm

kind: ResourceKind.VM
```

---

## Capability Set

| Capability | Implementation | Notes |
|---|---|---|
| `START` | `VBoxManage startvm <vm> --type headless` | |
| `STOP` | `VBoxManage controlvm <vm> savestate` | graceful — saves RAM to disk |
| `FORCE_STOP` | `VBoxManage controlvm <vm> poweroff` | hard stop |
| `RESTART` | savestate + startvm | |
| `SUSPEND` | `VBoxManage controlvm <vm> pause` | pause (not suspend-to-disk) |
| `SNAPSHOT_LIST` | `VBoxManage snapshot <vm> list` | |
| `SNAPSHOT_CREATE` | `VBoxManage snapshot <vm> take <name>` | |
| `SNAPSHOT_DELETE` | `VBoxManage snapshot <vm> delete <name>` | |
| `DISK_STATS` | `VBoxManage showvminfo <vm> --machinereadable | grep "^SATA"` | |
| `CONSOLE` | VRDE port from `showvminfo` → RDP URL | `rdp://<host>:<port>` |

**NOT implemented initially:**
- `LOGS` — VirtualBox doesn't have a shell-accessible console log
- `VM.clone` / `VM.copy` — advanced operations

---

## Config Example

```yaml
hosts:
  vbox-host:
    transport: ssh
    address: 192.168.0.xx
    user: <user>
    stats: linux    # or macos

providers:
  - id: virtualbox-local
    type: virtualbox
    host: vbox-host

groups:
  vbox-vms:
    label: "VirtualBox VMs"
    auto: virtualbox-local
```

---

## Implementation Notes

### State Parsing
`VBoxManage showvminfo <vm> --machinereadable` outputs key=value pairs:
```
VMState="running"
VMStateChangedTime="2026-06-15T10:30:00+0000"
SnapshotCount="3"
```

### Snapshot Parsing
`VBoxManage snapshot <vm> list --machinereadable` outputs:
```
SnapshotName="dubdeck-2026-06-15" UUID="abc123" SnapshotMachineAddress="..."
```

### VRDE/RDP Console
```bash
VBoxManage showvminfo <vm> --machinereadable | grep "^VRDEConnection"
# Or:
VBoxManage showvminfo <vm> | grep VRDP
# VRDE: enabled (Address 0.0.0.0, Ports 3389, ...)
```
The console URL would be `rdp://<host-ip>:3389` — the frontend opens this in an RDP client or web RDP client.

### VirtualBox Installation on Host
The VBoxManage binary must be on the remote host. On Linux/macOS it's installed with VirtualBox. The user running the SSH session needs permission to access VBoxSVC.

---

## Key Differences from Parallels Provider

| Aspect | Parallels | VirtualBox |
|---|---|---|
| CLI binary | `prlctl` | `VBoxManage` |
| Snapshot restore | manual only | `restore` available |
| Snapshot delete | manual only | `delete` available |
| Console | prlctl RDP | VRDE RDP (port 3389) |
| State format | `Status: running` | `VMState="running"` |
| Graceful stop | `prlctl stop` (ACPI) | `savestate` (RAM to disk) |
| Suspend | `prlctl suspend` | `pause` (not to disk) |

---

## N1H Tech Target Machine

**Mac Mini 2013 or Mac Pro 2009 (modded):**
- Either could run VirtualBox as a host (if macOS VirtualBox is installed)
- VBoxManage over SSH from __HOST__ → works the same as the Parallels provider
- But: Mac Mini 2013 is Parallels target first — VirtualBox is a secondary option

**Better target:** A dedicated Linux VM or bare-metal machine running VirtualBox 7.x.
