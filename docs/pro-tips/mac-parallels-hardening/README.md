# Pro Tip: Mac + Parallels SSH Hardening

> **This is an OPTIONAL example for Mac + Parallels deployments.**
> It is NOT required to run Dubdeck. If you are running Dubdeck on Linux or
> without Parallels, skip this entirely.

## What this does and why

Dubdeck's backend controls Parallels VMs via SSH to the Mac host. The naive
approach — using your regular user key — is risky: a stolen key would give the
attacker a full interactive shell on your workstation.

This hardening uses two mechanisms together:

1. **Loopback-only sshd on port 2222.** A second, user-level `sshd` instance
   runs alongside the system one, bound exclusively to `127.0.0.1:2222`. The
   Docker container reaches it via `host.docker.internal:2222`. Nothing on your
   LAN or Tailnet can connect directly — the port is simply not listening there.

2. **Forced-command shim key.** A dedicated ed25519 key is enrolled in
   `~/.ssh/authorized_keys` with `restrict` and `command="..."` options pointing
   at `dubdeck-shim.sh`. A stolen copy of this key is useless off the host:
   it can only send the exact commands the shim allows, and those commands only
   work on the loopback sshd that is only reachable from within the host.

Together, the attack surface for "Docker container can talk to Parallels" is
limited to a narrow, explicitly enumerated set of `prlctl` operations on an
allowlisted set of VMs.

This is one battle-tested way to accomplish this. There are others (e.g., a
Unix socket forwarded into the container, or a local REST shim process).

## Files

| File | Purpose |
|---|---|
| `com.dubdeck.sshd.plist` | launchd LaunchAgent that keeps the loopback sshd running at login. Copy to `~/Library/LaunchAgents/` and bootstrap with `launchctl`. |
| `setup.sh` | One-shot setup script. Installs the plist, builds the VM allowlist from `inventory.yaml`, distributes the restricted key to remote targets, scans host keys into `secrets/known_hosts`, and verifies the shim. Run manually — never automated. |
| `dubdeck-shim.sh` | The forced-command shim itself. The dubdeck key can only execute what is explicitly listed here: `prlctl list`, `prlctl start/stop/suspend` (allowlisted VMs only), snapshot list/create, and `prlctl exec <vm> tailscale ...` for egress control. Everything else returns exit 127. |

## Quick start

1. Generate a dedicated key pair:
   ```bash
   ssh-keygen -t ed25519 -C "dubdeck-container" -f ~/docker/stacks/dubdeck/secrets/dubdeck_key
   ```
2. Create `~/.dubdeck/sshd_config` pointing the user sshd at port 2222 on
   127.0.0.1 and referencing the shim key in `AuthorizedKeysFile`.
3. Run `setup.sh` — it bootstraps the LaunchAgent, installs the key on remote
   targets (see the `SERVER01` and `SLAB_FW_02` variables), and verifies the
   whole chain.
4. Update `docker-compose.yml` (or your equivalent) so the container uses
   `host.docker.internal:2222` with the generated key.

Replace `labuser` with your actual macOS username and `192.0.2.x` addresses
with your actual Tailscale IPs throughout.

## Security properties

- The key cannot be used from outside the host (loopback binding).
- The key cannot open a shell even from the host (forced-command + `restrict`).
- The key cannot touch VMs not listed in `~/.dubdeck/dubdeck-vms` (allowlist built
  from `inventory.yaml` at setup time).
- Snapshot restore and delete are explicitly NOT in the shim (destructive ops
  require a human in the loop).
