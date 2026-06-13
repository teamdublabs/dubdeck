# libvirt provider

Manages **KVM/QEMU virtual machines** on a Linux host via `virsh`. Dubdeck
reaches the host over SSH (or local transport) and drives libvirt entirely
through the `virsh` CLI. Resources are `kind: vm`, keyed by **domain name**
(the VM name as libvirt knows it).

## Capabilities

| Capability | `virsh` command |
|---|---|
| start           | `virsh start '<domain>'` |
| stop            | `virsh shutdown '<domain>'` (graceful ACPI signal; see note below) |
| force_stop      | `virsh destroy '<domain>'` (hard power-off; domain is preserved, not deleted) |
| suspend         | `virsh managedsave '<domain>'` (freezes RAM to disk; `virsh start` resumes) |
| snapshot_list   | `virsh snapshot-list '<domain>'` |
| snapshot_create | `virsh snapshot-create-as '<domain>' --name '<name>'` |
| disk_stats      | `virsh domstats --block --state` — sum of `block.N.physical` per domain |

No `logs` or `restart` — neither is a first-class single-call `virsh` operation.
Snapshot restore and delete are deliberately absent (destructive ops stay manual).

### Stop vs. force_stop — the ACPI nuance

`virsh shutdown` sends an **ACPI power-button signal** to the guest OS — a
polite request to shut down gracefully. This works well for well-behaved guests
with ACPI support, but some older or deliberately minimal guests (e.g. stripped-
down security-lab VMs) ignore the signal and never power off. Because `virsh
shutdown` returns immediately with exit code 0 regardless of whether the guest
will actually stop, `stop_is_graceful` is `True` in this provider and Dubdeck's
ops layer polls the domain state and escalates to `virsh destroy` if the guest
does not reach the stopped state within the grace window.

`virsh destroy` is a **hard power-off** — it pulls the virtual plug. The name is
a libvirt-ism; the domain definition is not removed and the VM can be started
again normally. If you want the guest removed from libvirt entirely, you would
run `virsh undefine` — that is deliberately out of scope.

## Host prerequisites

- **Linux host** with **libvirt** and **QEMU/KVM** installed.
  - Debian/Ubuntu: `apt install libvirt-daemon-system qemu-kvm`
  - Fedora/RHEL: `dnf install libvirt qemu-kvm`
- `virsh` available in the SSH user's `PATH` (ships with `libvirt-client`).
- The SSH user must be in the **`libvirt`** group (or equivalent) so that
  `virsh` can connect to the local `qemu:///system` socket without `sudo`:

  ```sh
  sudo usermod -aG libvirt labuser   # then re-login
  virsh list --all                    # must work without sudo
  ```

  On some distributions the group is named `kvm` or `libvirt-qemu` — check
  `ls -l /var/run/libvirt/libvirt-sock` or your distro's documentation.
- The libvirt daemon must be running:

  ```sh
  sudo systemctl enable --now libvirtd
  ```

## Config

```yaml
hosts:
  server01:
    transport: ssh
    address: 192.0.2.10    # IP or hostname the backend can SSH to
    user: labuser
    stats: linux           # host load/mem stats

providers:
  - id: server01-kvm
    type: libvirt
    host: server01

groups:
  research-lab:
    label: "Research Lab"
    members:
      - server01-kvm/gateway-vm
      - server01-kvm/workstation-a
      - server01-kvm/workstation-b
    policies:
      start_first: [server01-kvm/gateway-vm]
      ready_probe: { ref: server01-kvm/gateway-vm }
      snapshot_before_stop: true
```

If the backend runs on the same host as libvirt, use `transport: local` to skip
SSH entirely:

```yaml
hosts:
  this-box:
    transport: local
    stats: linux

providers:
  - id: local-kvm
    type: libvirt
    host: this-box
```

An `auto:` group can be used to track the host's full VM list without a
hand-written member list — useful when the VM roster changes frequently:

```yaml
groups:
  all-vms:
    label: "All VMs"
    auto: server01-kvm
```

## Notes

- **Domain names are the resource id.** `virsh list --all` returns names, not
  UUIDs. Resource refs in groups follow the form `provider-id/domain-name` (e.g.
  `server01-kvm/workstation-a`).
- **State mapping.** libvirt state strings map as follows: `running` →
  RUNNING, `shut off` → STOPPED, `paused` → PAUSED, `pmsuspended` → SUSPENDED.
  Any other state (e.g. `idle`, `crashed`, `in shutdown`) maps to UNKNOWN until
  the next poll resolves it.
- **Disk stats sum physical blocks.** The `disk_stats` capability runs `virsh
  domstats --block --state` and sums the `block.N.physical` bytes reported for
  each disk attached to a domain. This reflects actual on-disk usage, not the
  virtual disk's provisioned capacity, and covers all attached disks.
- **Snapshot restore and delete are out of scope.** The same no-destructive-ops
  boundary that applies to every other provider applies here. Use `virsh
  snapshot-revert` or `virsh snapshot-delete` manually if needed.
- **Managed save vs. pause.** `virsh managedsave` writes the full RAM image to
  disk and powers the domain off; the next `virsh start` automatically restores
  from the saved state. This is different from `virsh save` (which requires a
  target file argument) and from `virsh suspend` (which pauses in memory without
  persisting). Dubdeck uses `managedsave` so the host's RAM is freed while the
  VM is suspended.
