# Dubdeck Architecture

This document explains how Dubdeck is built and — more importantly — *why*. It is
the orientation map for contributors: read it before touching the provider layer,
the transports, the config schema, or the auth model. Reference docs for end users
live elsewhere (`install.md`, `configuration.md`, the per-provider docs); this file
is about the design rationale that makes the codebase make sense.

Dubdeck is a FastAPI backend plus a React frontend styled as a desktop OS (taskbar,
draggable windows, system tray, animated wallpaper). It controls VMs, containers,
and compose stacks across whatever hypervisors and hosts you point it at — start,
stop, suspend, snapshot, tail logs, and watch live status, from one browser tab.

---

## 1. The core generalization

Dubdeck began as a single-purpose lab controller with a hardcoded model: a `Host`
that was either Parallels or KVM, an `Enclave` that owned a gateway VM plus member
VMs, and VM names baked into the code. That model could only ever describe one
person's lab. The generalization that makes Dubdeck a *product* replaces it with six
orthogonal concepts:

```
Host        — a machine Dubdeck can execute on (transport: ssh | local)
Provider    — an instance of a provider type bound to a host or URL
              (parallels, libvirt, docker, compose, proxmox, hyperv, virtualbox)
Resource    — what a provider manages: kind = vm | container | stack
Capability  — what a provider can do to its resources (start, stop, suspend,
              snapshot, logs, …) — the UI renders only what's declared
Group       — a named collection of resources for display + bulk ops
              (replaces "enclave"; gateway semantics generalize to policies)
Module      — an optional feature that can be toggled in settings
              (each provider type is a module; egress is a module)
```

Read that as a pipeline: a **Host** is somewhere we can run commands; a **Provider**
is a typed adapter bound to a host (or, for API providers, a URL) that knows how to
talk to one management system; the things a provider manages are **Resources**, each
tagged with a **kind** (`vm`, `container`, `stack`); what a provider can *do* to a
resource is expressed as a set of **Capabilities**; **Groups** collect resources for
display and bulk operations; and **Modules** are the toggleable units that decide
which of all the above is actually loaded.

The payoff: adding support for a new kind of infrastructure is writing one provider
class, not threading a new special case through the whole app. Adding a machine or a
VM is a config edit, never a code change.

### Why capabilities instead of a fixed verb set

Different infrastructure can do different things. A Parallels VM can be snapshotted
and suspended; a Docker container can be restarted and has logs but no snapshots; a
compose stack is up/down with no per-resource logs. Rather than give every resource
the union of all verbs and have most of them error, each provider **declares** a
`frozenset[Capability]`, and the UI renders only the buttons for capabilities a
resource's provider actually declares. A container row shows Restart and Logs; a VM
row shows Snapshot and Suspend; neither shows actions its provider can't perform.

The contract is bidirectional and enforced by tests (see §7): a declared capability's
method must work, and an undeclared capability's method must raise `Unsupported`. The
base `Provider` class implements every capability method as a default that raises
`Unsupported`; a provider overrides exactly the ones it declares. There is no way for
the declared set and the implemented behavior to silently drift apart.

### Why Groups replaced "enclaves"

The original model pinned a "gateway" VM at the top of each enclave and started it
first, because the lab's firewall VMs had to be up before anything behind them could
reach the network. That is a real and common pattern — but "gateway" is too specific.
Groups generalize it into **policies**:

- `start_first` — members that come up first (in order) and stop last. The old
  gateway tier, with no Tailscale or firewall assumption baked in.
- `ready_probe` — wait until a designated member reports `RUNNING` before starting
  the rest. This is state-based: it replaced an earlier hardcoded "wait for the
  gateway's networking to answer" probe with a generic "is it running yet" check.
- `snapshot_before_stop` — snapshot every non-`start_first` member before stopping
  it, so a botched session is recoverable.

A group can also be **`auto:`** — instead of an explicit member list, it tracks a
provider's full live resource list. This is essential for churn-heavy providers like
Docker, where containers appear and disappear and nobody wants to hand-maintain a
member list.

---

## 2. The two provider families

Every provider implements the same `Provider` interface (`list_resources()` plus one
method per capability). Under that interface there are two structurally different
families, and supporting both is what proves the abstraction is genuinely about
"managing resources" rather than secretly being "run an SSH command":

### CommandProvider — over a Transport

`parallels`, `libvirt`, `docker`, `compose`, `hyperv`, and `virtualbox` are all
**CommandProviders**. They build a command string, run it through a host-bound
`Transport`, and parse the output with pure functions. The provider class holds the
transport and never knows or cares whether that transport is real SSH, a local
subprocess, or a test fake.

```python
class Transport(Protocol):
    async def run(self, command: str, timeout: float = 15.0) -> CommandResult: ...
```

Each family member differs only in the commands it emits and the parsers it runs:
`libvirt` shells out to `virsh`, `docker` to the Docker CLI, `hyperv` to PowerShell
cmdlets, `virtualbox` to `VBoxManage`, and so on. The shared `CommandProvider` base just runs a command and raises
on a non-zero exit.

### ApiProvider — over an HttpClient

`proxmox` is an **ApiProvider**. It owns an HTTP client and talks to a REST API; it
has no transport and runs no shell commands. Proxmox returns a task id (UPID) from
mutating calls and you poll a task-status endpoint until it completes — a flow with
nothing in common with running `virsh`. The HTTP side has its own seam, `HttpClient`,
mirroring the transport seam: a real `httpx`-backed client for production and a fake
that returns seeded responses for tests, so providers never import `httpx` directly
and tests never make a network call.

The point of shipping an API provider alongside the command providers is precisely
that it forced the interface to be HTTP-and-SSH-agnostic. If the only providers were
SSH-shaped, the abstraction would have quietly grown SSH assumptions. Proxmox keeps
the contract honest.

---

## 3. The transport layer

A `Transport` runs a command on exactly one machine and returns a `CommandResult`.
The key design choice is that **a transport is bound to its host at construction**:
the caller passes only a command, never a target. (An earlier design passed a target
on every call and pooled connections across hosts; per-host binding makes the fakes
trivial — a `FakeTransport` keys on the command alone because the host is already
fixed — and removes a whole class of "wrong host" bugs.)

Three implementations:

- **`SSHTransport`** — one persistent connection per host. Its reconnect and retry
  behavior encodes real operational incidents and must not be "simplified":
  - **Stale-retry.** A reused connection can be dead even when it still reports as
    open (the remote idle-reaped it). On the first failure the transport drops the
    connection, reconnects once, and retries — so a stale socket never surfaces as a
    user-visible error.
  - **Never-retry-timeout.** A timeout is treated as fundamentally different from a
    broken connection: when a command times out it was *already running* on the
    remote, so retrying could execute a mutating command a second time. Timeouts are
    surfaced, never retried. This distinction is load-bearing — collapsing it would
    risk double-starting or double-stopping a machine.
  - **Keepalives** keep the pooled connection healthy between polls.
- **`LocalTransport`** — runs commands as local subprocesses, so Dubdeck can manage
  the machine it runs on without SSHing to itself.
- **`FakeTransport`** — returns canned `CommandResult`s keyed by command. Every test
  uses this; no test opens a socket.

Because every provider goes through this one seam, the rule "tests never touch real
infrastructure" is structural, not a matter of discipline.

---

## 4. Configuration as data

`config.yaml` is the deployment's infrastructure inventory: **hosts, providers,
groups, and policies**. It is declarative data, not application settings — and it is
the single source of truth for what machines exist and what manages them. Adding a
host or a resource is a config edit, never a code change.

```yaml
hosts:
  server01:
    transport: ssh
    address: 192.0.2.10
    user: labuser
    port: 22
  workstation:
    transport: local

providers:
  - id: server01-kvm
    type: libvirt
    host: server01
  - id: server01-docker
    type: docker
    host: server01
  - id: server01-stacks
    type: compose
    host: server01
    stacks_dir: /home/labuser/docker/stacks
  - id: pve
    type: proxmox
    url: https://192.0.2.20:8006
    token_id: dubdeck@pam!dubdeck
    token_secret_env: DUBDECK_PVE_TOKEN   # read from the environment, never inline

groups:
  research:
    label: "Research Lab"
    members:
      - server01-kvm/research-fw
      - server01-kvm/research-a
    policies:
      start_first: [server01-kvm/research-fw]
      ready_probe: { ref: server01-kvm/research-fw }
      snapshot_before_stop: true
  containers:
    label: "Containers"
    auto: server01-docker      # tracks the provider's full live resource list
```

Design rules baked into the loader:

- **Member refs are `provider-id/resource-id`.** A resource id may itself contain a
  slash (Proxmox uses `node/vmid`), so refs split once from the left.
- **Secrets are never inline.** API tokens and SSH keys are referenced by env-var
  name or mounted as files; `config.yaml` only names the user to connect as and the
  env var to read a token from. The config file is safe to commit; the secrets it
  points at are not in it.
- **Cross-validation up front.** Pydantic validators reject unknown provider types,
  duplicate provider ids, command providers missing a host, API providers missing a
  url/token, group members pointing at unknown providers, `start_first`/`ready_probe`
  refs that aren't actually members, and a group that sets both `members` and `auto`.
  Misconfiguration fails fast with a message naming the offending entry.
- **An empty or missing config is valid.** A fresh install with no `config.yaml`
  boots into an onboarding screen rather than crashing; only a *malformed* file
  raises.

**App settings are not in `config.yaml`.** Module toggles, auth state, and branding
live in a SQLite-backed settings service editable from the UI. The split is
deliberate: `config.yaml` is infrastructure you version-control and mount; settings
are operator preferences you change at runtime through the Settings window.

---

## 5. The module system

A module is a unit that can be turned on or off. Each provider type is a module, and
the egress feature (§6) is a module. The implementation is deliberately **boring: a
registry dict plus settings toggles** — no entry-points, no `importlib` plugin magic.
Each provider type registers itself in `providers/registry.py`; that registry is
consulted when building providers from config and when validating config.

A module that is disabled in settings, or simply absent from config, contributes
**nothing**: no providers are constructed, no resources appear in status, and its
routes are effectively dead. Toggling a module on or off takes effect at restart,
because modules can own background tasks (the egress sweeper, for instance) and we
resolve them once at startup rather than hot-swapping a live engine. The toggle
itself persists in settings either way.

A hard architectural constraint: **core never imports from a module.** The dependency
runs one way — modules may depend on core, core may not depend on a module — and this
is enforced by a test, not just convention. It is what lets a module be genuinely
optional.

---

## 6. The egress module (optional)

Egress is an example of a module that lives entirely outside core. It manages
time-boxed internet access for groups of resources by toggling an upstream exit node,
with **server-side auto-revoke**: you grant access for N minutes, and the backend
revokes it when the window expires — even if the browser tab closed or the backend
restarted in the meantime.

The behaviors worth knowing about as a contributor:

- The active window is persisted, so a backend restart **reconciles** on startup:
  it reads the actual upstream state and revokes anything that's active without a
  live window backing it.
- A failed revoke is never terminal — a retry loop keeps trying until it lands, and
  the UI shows an overdue window as an alarm.
- It's **default-off**. Most deployments don't want it; it loads only when both an
  `egress:` config section exists and the settings toggle is on.

Egress is off the v1 core scope on purpose. It's a clean demonstration of the module
boundary: remove the config section and the toggle, and the entire feature — engine,
routes, status section, background task — vanishes without touching anything else.

---

## 7. Auth and request guards

Dubdeck has **single-user auth, on by default.** There are no roles and no multi-user
support in v1 — one admin account, one password.

- **No default credentials, ever.** On first run no password is set; every API route
  except the public ones (health, the setup endpoint, login) returns 403 and the
  frontend shows a setup screen. The admin sets the password there.
- **Password hashing is argon2id** (via `argon2-cffi`), stored in SQLite.
- **Sessions** are HMAC-signed, HTTP-only cookies with `SameSite=Lax` and a sliding
  idle expiry. Login is rate-limited per IP. Changing the password re-checks the
  current one (a hijacked session alone can't rotate the credential) and invalidates
  every other live session.
- **Disabling auth requires a loopback bind.** `auth.enabled = false` is allowed only
  when the backend is bound to a loopback address — and this is **enforced at startup**
  (the process refuses to start otherwise) and again at runtime (you can't flip the
  toggle off over the network if the bind isn't loopback). It is not merely documented.

Auth sits behind two host-level guards that are independent of it and run first:

- **Host allowlist** — requests whose `Host` header isn't in the configured allowlist
  are rejected (DNS-rebinding defense; binding to loopback alone doesn't stop a
  malicious page from using the browser as a proxy).
- **`Sec-Fetch-Site`** — cross-site `/api` requests are blocked (drive-by CSRF
  defense), since no legitimate cross-site caller of a localhost API exists.

The default bind is `127.0.0.1`. Binding wider is an explicit choice that requires
auth to be enabled.

---

## 8. Services and request flow

Between the providers and the HTTP routes sit a few services, all built at startup
and attached to app state:

- **Status** aggregates `list_resources()` across every provider into one snapshot,
  using a **stale-while-revalidate cache** so readers never block: a downed host
  (which hits its connect timeout) can't freeze the poller. Snapshots stream to the
  frontend over Server-Sent Events, pushed promptly after every mutation and on a
  steady heartbeat otherwise, with a polling fallback.
- **Ops** runs the mutating actions — start, stop, suspend, restart, snapshot — on a
  `(provider, resource_id)` pair, applying group policies (escalate a graceful stop
  to a forced one, hold a per-group lock so racing starts dedupe, honor `start_first`
  ordering and the `ready_probe` wait, snapshot-before-stop).
- **Stats** joins per-host CPU/RAM/disk and per-resource disk footprint into the
  status view.
- **Ops log** records every mutating action — who/what/when/result — to SQLite, newest
  first, surviving restarts.

The API surface mirrors the concepts: `/api/groups/{name}/start|stop` for bulk ops,
`/api/resources/{provider}/{rid}/start|stop|suspend|restart|logs|snapshots` for
single resources, `/api/status` and `/api/events` for the live view, `/api/log` for
the ops log, and the auth/settings/config endpoints. The frontend reads capabilities
straight out of the status payload and renders accordingly.

---

## 9. The contract suite: the definition of "a provider works"

There is one parametrized test suite that every provider must pass with its fakes,
and it is the operational definition of a working provider:

- **Capabilities match methods, both ways.** Every declared capability's method runs
  without raising `Unsupported`; every undeclared capability's method raises it.
- **`list_resources()` returns stable ids.** Resource ids must be stable across
  restarts (which is why, for example, the Docker provider keys on container *name*,
  not the container id that changes on recreate).
- **State mapping covers the fixtures.** The provider's parser maps every state in
  its captured fixture output onto the shared `ResourceState` vocabulary.
- **Command injection is handled.** Hostile resource names — spaces, quotes, `;` —
  are fed through every command builder and must be correctly quoted for that
  provider's shell. (The quoting is per-provider: most use POSIX shell quoting, but
  the Hyper-V provider uses PowerShell's single-quote doubling, and the suite asserts
  the right one for each.)

The two provider families plug into the same suite: command providers via a
`FakeTransport`, the API provider via a fake HTTP client. **Every new provider adds
itself here.** If it passes the contract suite, it works; if it can't, it isn't done.

---

## 10. Non-negotiables

These are the invariants every contribution must preserve. They are not style
preferences — each one is load-bearing:

- **Tests never touch real infrastructure.** Every provider ships with fakes and
  captured fixtures; no test opens a network connection or runs a real command.
- **Parsers are pure functions over captured output.** Command output goes in, typed
  data comes out; no I/O inside a parser. This is what makes the fixtures meaningful.
- **All mutating actions hit the ops log.** Every start/stop/suspend/snapshot is
  recorded with its result. The log is the audit trail.
- **No destructive VM operations in v1.** No delete, no undefine, no snapshot-restore.
  The blast radius of a bug in this app is bounded by what it's allowed to do.
- **Default bind is `127.0.0.1`.** Exposing Dubdeck wider is an explicit config choice
  that requires auth enabled and the host allowlist configured.
- **Core never imports from a module.** One-way dependency, enforced by a test.

A change that breaks one of these isn't a smaller version of the feature — it's a
different, less trustworthy application. Keep them.
