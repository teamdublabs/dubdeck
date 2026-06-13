# Docker provider

Manages **containers** on a host that runs the Docker CLI. This is the first
non-VM provider — its resources are `kind: container`, and it adds the **logs**
capability the VM providers don't have.

## Capabilities

| Capability | Action |
|---|---|
| start   | `docker start <name>` |
| stop    | `docker stop <name>` (SIGTERM → SIGKILL after Docker's own grace timeout) |
| restart | `docker restart <name>` |
| logs    | `docker logs --tail <n> <name>` (stdout+stderr merged) |

No snapshot, suspend, or disk-stats — those aren't Docker concepts. The UI
renders only the buttons above for container rows.

Resources are keyed by **container name**, not container ID: IDs change every
time a compose service is recreated, names are stable.

## Host prerequisites

- Docker Engine with the Compose v2 plugin (`docker` + `docker compose`).
- The SSH user Dubdeck connects as must be able to run `docker` **without sudo**
  — add it to the `docker` group:

  ```sh
  sudo usermod -aG docker labuser   # then re-login
  docker ps                          # must work without sudo
  ```

  (Membership in the `docker` group is root-equivalent on that host. Use a host
  and user you already trust with container control.)

## Config

```yaml
hosts:
  linux01:
    transport: ssh
    address: 192.0.2.10
    user: labuser

providers:
  - id: linux01-docker
    type: docker
    host: linux01

groups:
  containers:
    label: "Containers"
    auto: linux01-docker     # tracks the host's full container list
```

An `auto:` group is the natural fit: containers come and go, so you don't want a
hand-written member list. The group window shows every container the host
reports (`docker ps -a`), running or stopped, and refreshes as they churn.

To curate instead — show only a few containers — use an explicit `members:` list
of `linux01-docker/<container-name>` refs in place of `auto:`.

## Notes

- State is read from Docker's machine-readable `State` field
  (`running`/`exited`/`paused`/…), not the human `Status` string — so health
  suffixes like `Up 3 days (healthy)` never confuse the displayed state.
  Transient states (`restarting`, `removing`, `dead`) show as **unknown** until
  the next poll resolves them.
- Logs merge stdout and stderr so the viewer shows the real interleaved tail.
