# Pro Tips

These are worked examples from a real Dubdeck deployment. Each section describes one
battle-tested way to do something — **none of it is required to run Dubdeck**. You may
have a different setup that works better for you. Use these as starting points, not
prescriptions.

---

## 1. Mac + Parallels: loopback sshd and forced-command key hardening

*Applies to: macOS hosts running Parallels Desktop where Dubdeck drives VMs via `prlctl`.*

### The problem

Dubdeck's backend is a Docker container. Docker-on-Mac cannot run `prlctl` directly
— the command only works on the Mac host, not inside a Linux container. So the
backend SSHes to the Mac host to invoke `prlctl` on your behalf.

The obvious approach — using your regular user key for that SSH connection — is a bad
idea: if the container is ever compromised, a stolen key gives an attacker a full
interactive shell on your workstation.

### The solution: two layers of containment

**Layer 1 — loopback-only sshd on a dedicated port.**
Run a second, user-level `sshd` instance bound exclusively to `127.0.0.1:2222`.
The Docker container reaches it via `host.docker.internal:2222`. Nothing on your LAN
or Tailnet can connect to this port — it literally isn't listening there. This
eliminates remote attack surface entirely.

**Layer 2 — a forced-command key.**
A dedicated ed25519 key is enrolled in `~/.ssh/authorized_keys` with SSH's `restrict`
and `command="..."` options pointing at a shim script (`dubdeck-shim.sh`). Even if this
key is stolen, the attacker cannot use it to open a shell — the forced-command
replaces whatever the SSH client requests with the shim. The shim rejects anything
that isn't on an explicit allowlist.

Combined: the shim key is only reachable from within the host (loopback binding), and
only executes a narrow, explicitly enumerated set of `prlctl` operations on an
allowlisted set of VMs.

### How it fits together

There are three files in `docs/pro-tips/mac-parallels-hardening/`:

**`com.dubdeck.sshd.plist`** — a macOS launchd `LaunchAgent` that keeps the loopback
sshd running at login. It points `sshd` at a config file in `~/.dubdeck/sshd_config`
(you create this, pointing sshd at port 2222 on 127.0.0.1 and referencing the shim
key's `AuthorizedKeysFile`). Copy it to `~/Library/LaunchAgents/` and bootstrap it
with `launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.dubdeck.sshd.plist`.

**`dubdeck-shim.sh`** — the forced-command shim. When the Dubdeck container SSHes in
with the dubdeck key, this script is what runs — not a shell. It reads
`SSH_ORIGINAL_COMMAND` and matches it against a regex allowlist. The only commands
that are permitted are:

- `prlctl list --all -o name,status` — list all VMs
- `prlctl start|stop|suspend <vm>` — power ops, gated to an allowlisted set of VM names
- `prlctl snapshot-list <vm> -j` and `prlctl snapshot <vm> -n <name>` — snapshots
- `prlctl exec <vm> tailscale status --json` and `tailscale set --exit-node=...` —
  for egress control (see section 2)
- A small `dubdeck-stats` command for host CPU/memory/disk stats

Anything else — including a bare shell attempt — gets `exit 127`. The VM allowlist
lives in `~/.dubdeck/dubdeck-vms` and is built from your `config.yaml` at setup time, so
the shim can only touch your declared lab VMs, not anything else on the host.

**`setup.sh`** — a one-shot script you run manually on the workstation (never
automated). It:

1. Installs the shim to `~/.dubdeck/dubdeck-shim.sh`
2. Builds the VM allowlist from `config.yaml`
3. Bootstraps the LaunchAgent (if not already running) and verifies the port
4. Installs the restricted dubdeck public key on SSH-reachable targets
5. Scans host keys into a `known_hosts` file so the container pins them
6. Runs a verification sequence: checks the shim allows the expected commands and
   rejects a raw shell and a non-allowlisted VM name

The script uses RFC 5737 placeholder IPs (`192.0.2.x`) for direct SSH targets —
replace these with your actual Tailscale IPs (see section 3 on why you should
use IP addresses, not hostnames).

### Resulting security properties

- The key cannot be used from outside the host (loopback binding).
- The key cannot open a shell even from within the host (forced-command + `restrict`).
- The key cannot touch VMs not in the allowlist.
- Snapshot restore and delete are deliberately absent from the shim — those are
  destructive operations that require a human in the loop.

### Note on egress through `prlctl exec`

For Parallels-hosted gateway VMs, the egress shim uses `prlctl exec <vm> tailscale
...` rather than SSHing directly to the VM's Tailscale IP. This is intentional: the
VM's own Tailscale path can be unreliable when Parallels NAT is having a bad day
(the NAT stack and Tailscale's UDP ports can conflict after a cold start). The host
channel via `prlctl exec` doesn't go through the VM's network stack and stays
reachable even when the VM's tailnet path is flapping. The egress engine (section 2)
is aware of this and lets you pick the transport per gateway.

---

## 2. Tailscale egress module: temporary internet for lab gateways

*Applies to: anyone using Tailscale exit nodes to route a lab enclave's internet
traffic, who wants push-button control with an automatic off switch.*

### What this is and why it exists

In a segmented lab setup, it is common to have a gateway VM that connects an isolated
network to the internet via a Tailscale exit node — traffic from lab VMs behind the
gateway is tunneled through a relay node on your tailnet before hitting the internet.
This is useful for vulnerability research (you want to control when the lab "goes
online") or for keeping lab VM traffic separated from your normal traffic.

The problem: it is easy to forget to turn it off. Leaving an exit node set on a
gateway means all lab traffic routes through it indefinitely — often unintentionally.

The egress module solves this with a **server-side auto-revoke timer** and **no
persistent "on" state without an expiry**. Every enable comes with a duration. When
the duration expires, the backend revokes the exit node automatically — even if the
UI tab is closed, even if you restarted the backend, even if the enable came from the
CLI. Revocation failure (gateway temporarily unreachable) is not terminal: the window
row stays in SQLite and a periodic sweep retries every 30 seconds until it lands.

### How to enable it

**Step 1: config.yaml**

Add a `modules.egress` section. Each gateway is keyed by its group name and declares
how the backend reaches Tailscale on it, and which exit node to use:

```yaml
modules:
  egress:
    gateways:

      # Gateway VM running on a KVM/libvirt host — direct SSH
      lab-enclave:
        address: <your-tailnet-ip>   # see section 3 — use the IP, not a hostname
        user: labuser
        exit_node: relay-node-01     # the tailnet exit node to route through
        mode: on-demand              # on-demand (default) or permanent

      # Gateway VM running under Parallels on the Mac — use prlctl exec
      # (avoids relying on the VM's own tailnet path, which can flap)
      parallels-lab:
        exec_on: mac-host            # core host name from the hosts: block
        vm: gateway-fw               # the Parallels VM to exec into
        exit_node: relay-node-01
        mode: on-demand
```

`exec_on` / `vm` (Parallels), `host` (command on a core host's transport), and
`address` (a direct SSH target owned by the egress module) are the three ways to
reach a gateway. Use whichever matches your topology. See `config.example.yaml` for
an inline comment summary of each.

`mode: permanent` marks a gateway whose exit node should never be toggled by Dubdeck
— the reconciler skips it on startup, and the enable/extend/revoke routes return 409
for it. Use this for a gateway you always want online (e.g. a trusted relay VM that's
not under a time limit).

**Step 2: enable the module in Settings**

The egress module is **off by default** — even if `modules.egress` is present in
`config.yaml`, the module is not loaded until it is enabled in Settings → Modules.
Toggle it on and restart the backend (`docker compose up -d`; module toggles apply
at restart). Once enabled, the Tailscale egress controls appear in the group windows
for any group named in `gateways`.

### Auto-revoke semantics

When you click "Enable egress" in the UI (or POST to
`/api/groups/{group-name}/egress` with a `duration_s` body), the backend:

1. Runs `tailscale set --exit-node=<exit_node>` on the gateway.
2. Records `(group, expires_at)` in SQLite.
3. Schedules an in-memory timer to revoke when `duration_s` elapses.

When revoke fires (timer or manual), the backend runs
`tailscale set --exit-node=` (empty string = clear exit node) and deletes the row.

**What survives a backend restart:**

On startup, `reconcile()` runs before the sweeper starts:

- Any group with a row in `egress_windows` where `expires_at > now`: the timer is
  re-armed for the remaining duration. The window continues as if nothing happened.
- Any group with an expired row: the backend probes the gateway (`tailscale status
  --json`) and revokes the exit node if it's still active, then cleans up the row.
- Any group with no row but an active exit node on the gateway: the backend revokes
  it and logs an "orphaned window" warning. This is the crash-safety path — if the
  backend crashed between writing the row and the timer firing, the next startup
  cleans it up.

The maximum duration is capped at 4 hours (`MAX_DURATION`). Requests for longer are
clamped silently (the response body flags `"clamped": true`). The `extend` endpoint
adds to the current expiry rather than the current time, up to the same cap.

### API summary

| Method | Path | Effect |
|---|---|---|
| `POST` | `/api/groups/{name}/egress` | Enable for `duration_s` seconds (default 1800) |
| `POST` | `/api/groups/{name}/egress/extend` | Extend the current window by `duration_s` |
| `DELETE` | `/api/groups/{name}/egress` | Revoke immediately |

Status for each gateway is included in the main status snapshot under
`status.modules.egress.{group}` with fields `internet` (bool or null if gateway
unreachable), `expires_at` (Unix timestamp or null), and `mode`. The frontend egress
controls read from this section.

---

## 3. Tailnet IPs, not hostnames — and why this matters in containers

*Applies to: any deployment involving Tailscale, VMs, or Docker on Mac.*

### The gotcha

You might expect to use a hostname like `gateway-fw` or `lab-host` in your
`config.yaml` — after all, Tailscale's MagicDNS resolves those names everywhere.
Two scenarios break this silently:

**Parallels `.shared` DNS shadowing (macOS).** On a Mac with Parallels Desktop,
Parallels injects its own DNS resolver that handles the `.shared` domain — and it
shadows bare VM hostnames. `gateway-fw` might resolve to a dead `10.211.55.x` address
(Parallels' internal NAT range) instead of the real Tailscale IP. `ssh gateway-fw`
appears to time out for no reason; the actual connection is going to an unreachable
address on Parallels' internal interface. This only manifests with active Parallels
networking, so it can disappear and reappear depending on which VMs are running.

**MagicDNS doesn't work inside Docker containers.** Docker containers on a Mac get
their DNS from the Docker Desktop stub resolver, not from the system resolver that
Tailscale MagicDNS hooks into. A name that resolves fine in your terminal may quietly
NXDOMAIN inside the container where the backend runs.

### The fix

Use the stable Tailscale (or management) IP address in `config.yaml`, always. Never
use a bare hostname for a Tailscale-connected host.

```yaml
# Bad — may resolve to wrong address depending on network conditions
hosts:
  gateway-fw:
    transport: ssh
    address: gateway-fw    # DO NOT DO THIS

# Good — resolves identically everywhere
hosts:
  gateway-fw:
    transport: ssh
    address: 100.64.0.x    # <your-tailnet-ip> — stable across reboots
    user: labuser
```

The Tailscale IP (in the `100.64.0.0/10` CGNAT range) is assigned to a device and
does not change unless you remove and re-add it. You can find it with
`tailscale ip -4` on any device in your tailnet.

The same rule applies to addresses in the egress module config, known_hosts files,
and anywhere else you refer to a Tailscale-connected machine from within a container
or from macOS with Parallels running.

### Generalizing the lesson

The root issue is: **hostname resolution is environment-dependent, IP addresses are
not.** In a Docker-on-Mac setup, the environment between "your terminal" and "the
container" can differ in several ways (DNS resolvers, search domains, `/etc/hosts`
content, what Parallels has injected). When something works in the terminal but not
in the container, DNS is almost always the first thing to check. Using IPs sidesteps
the entire class of problem.

If you are on a pure Linux host without Parallels and without Docker Desktop (running
the Docker daemon natively), MagicDNS typically works fine inside containers because
the system resolver is already the Tailscale one. But if you ever add Parallels,
Docker Desktop, or a non-Tailscale-aware DNS setup, the IP rule will save you a
frustrating debugging session.
