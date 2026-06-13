# Compose-stack provider

Manages **compose stacks** — multi-container projects controlled with
`docker compose`. Resources are `kind: stack`; each stack is one compose project
under your configured `stacks_dir`.

## Capabilities

| Capability | Action |
|---|---|
| start   | `cd <stacks_dir>/<name> && docker compose up -d` |
| stop    | `cd <stacks_dir>/<name> && docker compose down` |
| restart | `cd <stacks_dir>/<name> && docker compose restart` |

Ops `cd` into the stack directory and let `docker compose` discover the compose
file, so both `compose.yaml` and `docker-compose.yml` work.

No logs/snapshot/suspend at the stack level — for per-container logs, point a
[Docker provider](docker.md) at the same host.

## The `stacks_dir` convention

A compose provider **requires** `stacks_dir`: the directory that holds your
stacks, one per subdirectory:

```
/home/labuser/docker/stacks/
  blog/compose.yaml
  wiki/compose.yaml
  monitoring/compose.yaml
```

This is the layout [Dockge](https://github.com/louislam/dockge) uses, and it
works with or without Dockge. Dubdeck lists stacks via `docker compose ls -a`
and **keeps only the projects whose compose file lives under `stacks_dir`**, so
unrelated compose projects elsewhere on the host never show up.

Each stack's compose file may be named `compose.yaml` or `docker-compose.yml` —
ops `cd` into `<stacks_dir>/<name>/` and let `docker compose` discover it.

## Config

```yaml
hosts:
  linux01:
    transport: ssh
    address: 192.0.2.10
    user: labuser

providers:
  - id: linux01-stacks
    type: compose
    host: linux01
    stacks_dir: /home/labuser/docker/stacks   # required

groups:
  stacks:
    label: "Compose Stacks"
    auto: linux01-stacks
```

Host prerequisites are the same as the [Docker provider](docker.md): Docker
Engine, the Compose v2 plugin, and an SSH user in the `docker` group.

## Notes

- A stack's state is **running** if any of its services is running
  (`docker compose ls` reports e.g. `running(2)` or mixed `running(1),
  exited(1)`), otherwise **stopped**.
- Stack names are validated against `^[a-z0-9_-]+$` before they ever reach a
  command line — a name with a slash, space, or shell metacharacter is rejected
  outright, not just quoted, so it can't escape `stacks_dir`.
