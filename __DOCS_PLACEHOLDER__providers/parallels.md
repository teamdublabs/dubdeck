# Parallels provider

Manages **Parallels Desktop virtual machines** on a macOS host. Dubdeck reaches
the host over SSH (or a local transport if the backend runs on the same machine)
and drives it entirely through **`prlctl`** — every list and status operation
runs `prlctl list --all`, and power operations call the appropriate `prlctl`
subcommand. Resources are `kind: vm`, keyed by **VM name**.

## Capabilities

| Capability | `prlctl` command |
|---|---|
| start           | `prlctl start '<vm>'` |
| stop            | `prlctl stop '<vm>'` (clean shutdown via Parallels Tools; blocks until complete) |
| force_stop      | `prlctl stop '<vm>' --kill` (hard power-off) |
| suspend         | `prlctl suspend '<vm>'` (freezes RAM to disk; `prlctl start` resumes) |
| snapshot_list   | `prlctl snapshot-list '<vm>' -j` |
| snapshot_create | `prlctl snapshot '<vm>' -n '<name>'` |
| disk_stats      | `du -sk ~/Parallels/*.pvm` — on-disk size of each VM bundle |

No `logs` or `restart` — neither maps to a single `prlctl` call. Snapshot
restore and delete are deliberately absent (destructive ops stay manual).

### Stop vs. ACPI

`prlctl stop` (without `--acpi`) shuts the guest down through Parallels Tools —
the same fast path the GUI "Shut Down" button uses. The older `--acpi` flag
emulates pressing the power button; Linux guests without a handler ignore it for
up to ~60 seconds before giving up. Dubdeck uses the Parallels-Tools path, so
`stop_is_graceful` is `False` and no escalation loop is needed — the command
blocks and the result is decisive.

## Host prerequisites

- **macOS** with **Parallels Desktop** installed and licensed.
- `prlctl` available in the SSH user's `PATH` (it ships with Parallels).
- The SSH user must be able to run `prlctl` for the VMs you want to manage.
- The Dubdeck backend must be able to reach the Mac host over SSH — see the
  note on Docker-on-Mac below.

## Docker-on-Mac: why SSH is required

`prlctl` must run on the Mac — it talks directly to the Parallels hypervisor via
a local socket and cannot be forwarded into a container. When the Dubdeck backend
runs as a Docker container on that same Mac, the container cannot invoke `prlctl`
directly; it must SSH back to the host.

The recommended approach is a dedicated loopback-only `sshd` on port 2222 with a
restricted forced-command key so that a compromised container can only execute the
exact `prlctl` commands you enumerate — not an interactive shell. See
[Mac + Parallels SSH hardening](../pro-tips/mac-parallels-hardening/README.md)
for the full setup (launchd plist, shim script, and authorized-key configuration).

If the backend runs natively on the Mac (not in Docker), you can use
`transport: local` and skip the loopback sshd entirely.

## Config

```yaml
hosts:
  mac-studio:
    transport: ssh
    address: 192.0.2.40    # IP the backend can SSH to; on Docker-on-Mac use
                           # host.docker.internal with port 2222
    port: 2222             # loopback-sshd port (omit if using the system sshd)
    user: labuser
    stats: macos           # host load/mem stats

providers:
  - id: mac-studio-parallels
    type: parallels
    host: mac-studio

groups:
  lab-vms:
    label: "Lab VMs"
    auto: mac-studio-parallels   # tracks the host's live VM list
```

If the backend is running directly on the Mac host, use `transport: local`
instead:

```yaml
hosts:
  this-mac:
    transport: local
    stats: macos

providers:
  - id: local-parallels
    type: parallels
    host: this-mac
```

## Notes

- **Disk stats are bundle-level.** The `disk_stats` capability measures the
  on-disk size of each `.pvm` bundle (the container directory Parallels uses for
  every VM). It does not reflect the virtual disk's provisioned size — it reflects
  actual storage consumed, including snapshots within the bundle. The measurement
  is performed by `du -sk ~/Parallels/*.pvm`; when running through the hardened
  shim, the shim translates the allowlisted command alias `dubdeck-vm-disks` into
  that `du` call.
- **VM names are the resource id.** `prlctl list` returns names, not UUIDs.
  Names must be unique on the host for Dubdeck to distinguish them. If two VMs
  share a name the state map will be ambiguous.
- **Snapshot restore and delete are out of scope.** The same no-destructive-ops
  boundary that applies to every other provider applies here. Restoring or
  deleting snapshots must be done manually via the Parallels GUI or `prlctl
  snapshot-switch` / `prlctl snapshot-delete`.
