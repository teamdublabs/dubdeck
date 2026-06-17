# VirtualBox provider

> **Experimental.** Implemented and unit-tested against the documented
> `VBoxManage` output shapes, but not yet exercised against a live VirtualBox
> host in this project's CI. It should work; you are an early user — bug
> reports (and captured `VBoxManage list -l vms` output from a real host) are
> very welcome.

Manages **Oracle VM VirtualBox virtual machines** on a Linux, macOS, or Windows
host. Dubdeck reaches the host over SSH and drives it entirely through
**`VBoxManage`** — every list operation runs `VBoxManage list -l vms` (the
`-l` long form is the only single-call shape that surfaces per-VM state), and
power operations call the appropriate `VBoxManage startvm`/`controlvm`
subcommand. Resources are `kind: vm`, keyed by **VM name**.

## Capabilities

| Capability | `VBoxManage` command |
|---|---|
| start           | `VBoxManage startvm '<vm>' --type headless` (headless; no GUI window on the host) |
| stop            | `VBoxManage controlvm '<vm>' acpipowerbutton` (graceful in-guest shutdown; escalates to force on timeout) |
| force_stop      | `VBoxManage controlvm '<vm>' poweroff` (hard pull-the-plug; VM definition is preserved) |
| suspend         | `VBoxManage controlvm '<vm>' savestate` (freezes RAM to disk; `startvm` resumes from it) |
| snapshot_list   | `VBoxManage snapshot '<vm>' list` |
| snapshot_create | `VBoxManage snapshot '<vm>' take '<name>'` |

No `logs` or `disk_stats` in v1 — neither is a single-call `VBoxManage`
operation (logs need a VM console capture; per-VM disk attribution needs
parsing `showvminfo` and walking the disk directory). Snapshot restore and
delete are deliberately absent (the same no-destructive-ops boundary as
every other VM provider).

### Stop vs. force_stop — the ACPI nuance

`VBoxManage controlvm acpipowerbutton` sends the same **ACPI power-button
signal** as `virsh shutdown` (libvirt) and bare `prlctl stop` (Parallels):
a polite request to the guest OS to shut down gracefully. This works for
well-behaved guests, but minimal or stripped-down guests (security-lab VMs,
single-purpose routers) often ignore the signal and never power off. Because
`acpipowerbutton` returns immediately with exit code 0 regardless of whether
the guest will actually stop, `stop_is_graceful` is `True` in this provider
and Dubdeck's ops layer polls the VM state and escalates to `VBoxManage
controlvm poweroff` (the hard pull-the-plug) if the guest doesn't reach a
stopped state within the grace window.

`poweroff` is a **hard power-off**, not a delete — the VM definition is
preserved and `startvm` resumes it normally. If you want the VM unregistered
from VirtualBox, run `VBoxManage unregistervm` manually; that's deliberately
out of scope.

### Headless start

`VBoxManage startvm` opens a GUI window on the host by default — useless
over SSH (no display, command hangs). Dubdeck always passes `--type headless`
so the VM boots normally but no GUI window is attached. (Headless here means
"no display attachment", not "no OS" — the guest runs exactly the same.)

## Host prerequisites

- A host running **VirtualBox 5.x, 6.x, or 7.x** with `VBoxManage` on the
  `PATH` of the SSH user.
- The SSH user must be able to run `VBoxManage list vms` and see the VMs you
  want to manage. By default VirtualBox stores per-user VM lists under
  `~/VirtualBox VMs/`; run VBoxManage as the same user that owns the VMs you
  want to control (or ensure that user's `VBOX_USER_HOME` is reachable).
- For VMs running headless already (e.g. on a Linux server with no GUI), no
  additional setup is needed.
- For VMs on a workstation host that you'd otherwise manage from the GUI,
  the SSH user just needs read/write access to the per-user VirtualBox
  configuration directory.

### A note on the Windows + VBox + SSH path

The provider assumes a POSIX-style SSH shell (`bash`/`zsh`) for command
parsing — `VBoxManage` itself is identical across platforms, but the SSH
shim from Windows to a Linux guest runs through different quoting rules.
For the typical lab deployment (Linux or macOS host running VirtualBox),
this works out of the box. If you want to drive a Windows-hosted VBox
through OpenSSH-with-PowerShell, the hyperv provider has the right quoting
discipline for that path.

## Config

VirtualBox is a command provider like Docker, libvirt, or Parallels — it
binds to an SSH host:

```yaml
hosts:
  vboxhost:
    transport: ssh
    address: 192.0.2.40     # IP or tailnet IP the backend can SSH to
    user: labuser
    stats: linux            # host load/mem stats

providers:
  - id: vboxhost-virtualbox
    type: virtualbox
    host: vboxhost

groups:
  vbox-lab:
    label: "VBox Lab"
    auto: vboxhost-virtualbox   # track the host's live VM list
```

If the backend runs on the same host as VirtualBox, use `transport: local`
to skip SSH entirely:

```yaml
hosts:
  this-box:
    transport: local
    stats: linux

providers:
  - id: local-virtualbox
    type: virtualbox
    host: this-box
```

## Notes

- **VM names are the resource id.** `VBoxManage list -l vms` returns names,
  not UUIDs. Resource refs in groups follow the form `provider-id/vm-name`
  (e.g. `vboxhost-virtualbox/gateway-04`). VM names must be unique per host
  for Dubdeck to distinguish them; the `(UUID: …)` tail on the `Name:` line
  in the long output is stripped automatically.
- **State mapping.** VirtualBox state strings map as follows:
  `powered off` → STOPPED, `running` → RUNNING, `saved` → SUSPENDED
  (matches `savestate`), `paused` → PAUSED. Any other value (transient
  `starting`/`stopping`/`saving`/`restoring`/`teleporting`, or crash states
  like `aborted`) maps to UNKNOWN until the next poll resolves it.
- **`VBoxUserDir` and `VBOX_USER_HOME`.** VirtualBox stores VM metadata and
  snapshots under a per-user directory; if the SSH user isn't the same user
  that owns the VMs in the GUI, point `VBOX_USER_HOME` at the right
  directory in the user's shell profile so `VBoxManage` sees the same VM
  list as the GUI.
- **Snapshot restore and delete are out of scope.** The same no-destructive-ops
  boundary that applies to every other VM provider applies here. Use
  `VBoxManage snapshot <vm> restore` or `VBoxManage snapshot <vm> delete`
  manually if needed.
- **`startvm` and `--type headless` are mandatory.** The default start type
  attempts to open a window on the host's display; over SSH (or any
  headless server) that hangs forever. Dubdeck always passes `--type
  headless`; the guest boots normally, it just doesn't try to render a
  window. (If you want a different start type, run `VBoxManage startvm`
  manually with the flags you want.)
