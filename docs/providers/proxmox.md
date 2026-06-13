# Proxmox VE provider

> **Beta.** Tested against API mocks and the documented Proxmox response shapes,
> not yet battle-tested against a live cluster in production. It works, but you
> are an early user — bug reports are very welcome.

Manages **qemu VMs** and **lxc containers** across the nodes of a Proxmox VE
cluster. This is Dubdeck's first **API provider**: it talks to the Proxmox REST
API over HTTP with an API token — there is no SSH host and no shell. Both guest
families surface as `kind: vm` (Proxmox treats them as one guest list).

## Capabilities

| Capability | Proxmox action |
|---|---|
| start          | `POST .../status/start` |
| stop            | `POST .../status/shutdown` (graceful ACPI; escalates to force on timeout) |
| force_stop      | `POST .../status/stop` (hard) |
| suspend         | `POST .../status/suspend` |
| snapshot_list   | `GET .../snapshot` |
| snapshot_create | `POST .../snapshot` |
| disk_stats      | provisioned disk (`maxdisk`) from the guest list |

No `restart` or `logs` — neither is a first-class single-call Proxmox operation.

Mutations are asynchronous: each POST returns a **UPID** (task handle), which
Dubdeck polls (`GET .../tasks/{upid}/status`) to completion. A task that
finishes with `exitstatus` other than `OK`, or that runs past the operation
timeout, is reported as a failure in the ops log.

Resources are keyed **`node/vmid`** (e.g. `pve01/100`). The node is part of the id
because every API path needs it and a bare vmid isn't unique cluster-wide. A group
member ref is then `provider-id/resource-id` — i.e. `provider-id/node/vmid`, e.g.
`pve/pve01/100`.

## Creating an API token

Use a token, not a password — tokens are revocable and scoped. In the Proxmox
web UI:

1. **Datacenter → Permissions → API Tokens → Add.**
   - User: a dedicated user (e.g. `dubdeck@pam`).
   - Token ID: e.g. `dubdeck` → the full token id is `dubdeck@pam!dubdeck`.
   - Leave **Privilege Separation** checked (the token gets its own ACL, narrower
     than the user) and grant the token the roles below explicitly.
2. **Datacenter → Permissions → Add → API Token Permission**, path `/`, the token
   you created, with a role granting:
   - `VM.PowerMgmt` — start/stop/suspend
   - `VM.Audit` — list guests and read status
   - `VM.Snapshot` — list/create snapshots
   - `Sys.Audit` — node status (CPU/RAM for the hosts panel)

   The built-in `PVEVMAdmin` role covers the VM privileges; add `Sys.Audit`
   (e.g. via `PVEAuditor` on `/nodes`) for node stats.
3. Copy the **secret** shown once at creation.

## Config

The token secret is **never** written in the config file — it's read from the
env var named by `token_secret_env`:

```yaml
providers:
  - id: pve
    type: proxmox
    url: https://192.0.2.20:8006     # Proxmox API base URL (include the port)
    token_id: dubdeck@pam!dubdeck     # user@realm!tokenid
    token_secret_env: DUBDECK_PVE_TOKEN
    verify_tls: true                  # see below

groups:
  cluster:
    label: "Proxmox cluster"
    members:
      - pve/pve01/100      # provider-id/node/vmid
      - pve/pve01/101
```

Then provide the secret to the backend (e.g. in the compose stack's
`environment:` or an env file mounted from outside the repo):

```
DUBDECK_PVE_TOKEN=xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx
```

If the env var is unset at startup, the provider fails fast with a clear error
rather than running half-configured.

## TLS verification

Proxmox ships with a self-signed certificate. Two honest options:

- **`verify_tls: true` (default, recommended):** install a proper certificate on
  the Proxmox node (or trust your internal CA on the Dubdeck host). The
  connection is authenticated and encrypted.
- **`verify_tls: false`:** skip certificate verification — pragmatic for a
  homelab with the stock self-signed cert, but the connection is no longer
  protected against a man-in-the-middle who can reach the network path to
  Proxmox. Dubdeck logs a warning at startup when this is off. Prefer a real
  certificate if the path crosses anything you don't fully control.

## Degradation

An unreachable or slow Proxmox endpoint is treated exactly like an unreachable
SSH host: the provider is marked not-reachable in status with the error text,
the rest of the dashboard keeps updating, and the last-known state is served
stale-while-revalidate. A 401 (bad/expired token) surfaces as the provider error
so it's obvious in the UI.
