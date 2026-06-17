# AGENTS.md — Dubdeck

Dubdeck is a desktop-OS-themed control panel for VMs and containers: FastAPI
backend, React frontend, single Docker image, all driven by a declarative
`config.yaml`. This file tells an AI agent how to **set it up** and the few
hard rules that, if broken, will get a change sent back.

For prose docs read [`README.md`](../README.md), [`install.md`](install.md),
[`configuration.md`](configuration.md), [`architecture.md`](architecture.md),
[`SECURITY.md`](SECURITY.md), and [`CONTRIBUTING.md`](../CONTRIBUTING.md).
This file is the action-oriented cheat sheet; the others are the references.

---

## 0. Pick the setup path

| Path | Use it when… | Output |
|---|---|---|
| **A. Docker Compose (production)** | Default. Reproduces what end users run. | Container on `127.0.0.1:8400` |
| **B. Backend + frontend split (dev)** | Iterating on code with hot-reload. | Backend `:8000`, Vite `:5173` |
| **C. Demo (no real infra)** | Visual / UI work; no SSH keys needed. | `demo_server.py` on `:8042` |

Pick **one**. Do not mix — paths B and C both run a backend on the host, and
ports/cookies will collide if you start both.

---

## Path A — Docker Compose (production)

**Prereqs:** Docker with Compose v2, an SSH keypair the backend can use,
target hosts reachable from the host running Dubdeck.

1. **Inventory file.** Copy the template and edit. **Secrets never go in this file.**
   ```sh
   cp config.example.yaml config.yaml
   $EDITOR config.yaml
   ```
   Declare `hosts`, `providers`, `groups`. See
   [`configuration.md`](configuration.md) for the schema. Hosts reach the backend
   over SSH; providers sit on hosts; groups collect resources for display and
   bulk ops. Use `auto:` groups for churn-heavy providers (Docker, Compose).

2. **SSH credentials.** Outside the repo. The default `compose.yaml` reads from
   `./secrets/` — adjust the `volumes:` block if you keep them elsewhere.
   ```sh
   mkdir -p secrets
   cp ~/.ssh/id_ed25519 secrets/ssh_key            # or a dedicated key
   chmod 600 secrets/ssh_key
   ssh-keyscan 192.0.2.10 >> secrets/known_hosts   # one line per host in config.yaml
   ```

3. **Build and start.**
   ```sh
   docker compose up -d --build
   ```
   Container listens on `127.0.0.1:8400` (loopback only — do **not** widen the
   port without re-reading
   [`SECURITY.md`](SECURITY.md#binding-wider-than-loopback)).

4. **Docker Desktop for Mac only:** the bind-mounted key is owned by your host
   UID under VirtioFS. Build with the matching UID so the key stays readable
   inside the non-root container:
   ```sh
   docker compose build --build-arg PUID=$(id -u) && docker compose up -d
   ```

5. **Open** `http://127.0.0.1:8400`. The API returns 403 until you set an admin
   password on first run; that's expected.

**Verify:** `curl -fsS http://127.0.0.1:8400/api/health` returns 200.
`docker compose logs -f dubdeck` shows uvicorn ready and no config errors.

**Upgrade:** `docker compose pull && docker compose up -d --build`. The
`dubdeck-data` volume (SQLite) survives upgrades. `config.yaml` is mounted and
never touched by the container.

---

## Path B — Dev (backend + frontend split)

**Prereqs:** Python 3.12+ with [`uv`](https://docs.astral.sh/uv/), Node.js LTS
with npm.

Two terminals:

```sh
# Terminal 1 — backend (auto-reloads on save)
cd backend
uv sync
DUBDECK_CONFIG=../config.yaml uv run fastapi dev app/main.py
# → http://127.0.0.1:8000

# Terminal 2 — frontend (Vite proxies /api → :8000)
cd frontend
npm install
npm run dev
# → http://127.0.0.1:5173
```

Without `DUBDECK_CONFIG`, the backend boots into the onboarding screen — useful
when you don't have a config yet.

**Verify:** `curl -fsS http://127.0.0.1:8000/api/health` returns 200. Open
`:5173` and confirm the desktop shell renders.

To build the frontend for the production image:
```sh
cd frontend && npm run build      # output in frontend/dist/
```
The Docker image serves this as `DUBDECK_STATIC` (default `/app/static`).

---

## Path C — Demo (no real infrastructure)

For visual work without any SSH keys, hosts, or VMs. The demo server seeds
deterministic fictional data and uses `FakeTransport` internally — nothing
touches real hosts.

```sh
# Terminal 1
cd backend
uv run python demo_server.py
# → http://127.0.0.1:8042

# Terminal 2
cd frontend
npm install
npm run dev
# → http://127.0.0.1:5173 (point it at the demo server's :8042 instead of :8000)
```

The demo is the recommended way to capture screenshots in `docs/screenshot.png`
and to do UI work without lab hardware.

---

## The gate: `scripts/check.sh`

Run this before considering any change done. **It is the same gate CI runs.**

```sh
bash scripts/check.sh
```

Steps, in order: `gitleaks` → `ruff check` → `ruff format --check` → `pytest`
→ `npm run lint` → `tsc -b` → `vitest` → `npm run build`. Exits non-zero on the
first failure. No partial credit.

Individual checks when you only changed one side:

```sh
# Backend
cd backend && uv run ruff check --fix . && uv run ruff format . && uv run pytest

# Frontend
cd frontend && npm run lint && npx tsc -b && npx vitest run && npm run build
```

Install `gitleaks` once (the script assumes it's on `$PATH`): see the install
command at the top of `scripts/check.sh`. Skip the gitleaks step locally by
running the rest directly if you don't have it — but it will run in CI.

---

## Hard rules — do not violate

These are the things that get PRs sent back. Read [`CONTRIBUTING.md`](../CONTRIBUTING.md)
and [`SECURITY.md`](SECURITY.md) for the reasoning behind each.

1. **Tests never make real SSH or network calls.** Every remote call goes through
   a `Transport` (`backend/app/transports/`); tests inject `FakeTransport` via
   the `transports` fixture in `backend/tests/conftest.py`. **A test that needs
   a real connection will not be merged.** Add a fixture under
   `backend/tests/fixtures/` and a `transport.respond(...)` line instead.

2. **Provider parsers are pure functions over captured command output.** If you
   add a hypervisor command: capture real output once into a fixture file, write
   a `def parse_*(text: str)` function with no side effects. See the worked
   example in [`CONTRIBUTING.md`](../CONTRIBUTING.md#the-parser-fixture-pattern).

3. **Secrets are never inlined.** SSH key → mounted file (path A step 2).
   Proxmox token → referenced as `token_secret_env: DUBDECK_PVE_TOKEN` and
   passed via env var. The repo's `.gitignore` already excludes `secrets/`,
   `*.key`, `*.pem`, `id_*` — do not weaken it, and never commit a real key.

4. **`auth.enabled=false` requires a loopback bind.** The server refuses to
   start (and `PATCH /api/settings` refuses to apply) if auth is off while
   bound to a non-loopback address. Enforced in code, not just docs.

5. **No destructive operations in v1.** No `delete`, `undefine`, or
   `snapshot-restore`. Don't add them. Every action in the API is reversible;
   that boundary is by design (see
   [`SECURITY.md`](SECURITY.md#no-destructive-operations)).

6. **Read-only rootfs, non-root user, dropped caps in the container.** Don't
   add capabilities, writable mounts, or root inside the container. The
   `compose.yaml` hardening posture (`read_only: true`, `cap_drop: ALL`,
   `no-new-privileges`) is intentional — match it if you change the image.

7. **Don't widen the bind without re-reading `SECURITY.md`.** Changing
   `ports:` from `127.0.0.1:8400:8000` to `8400:8000` exposes the port on all
   interfaces. Auth must be on, and you need a TLS-terminating reverse proxy
   in front. Also update `DUBDECK_ALLOWED_HOSTS` to match.

---

## Repo map (where to make changes)

```
backend/app/                  — FastAPI routers, auth, settings, ops log
backend/app/transports/       — Transport protocol + SSH / Local / Fake impls
backend/app/providers/        — one file per provider type (libvirt, parallels,
                                docker, compose, proxmox, hyperv, virtualbox)
backend/app/providers/base.py — Provider protocol + Capability enum + default Unsupported
backend/app/providers/registry.py — provider type → class mapping
backend/app/modules/          — optional modules (egress lives here)
backend/tests/                — pytest; conftest.py wires the FakeTransport fixture
backend/tests/fixtures/       — captured command output; add a file per new parser
backend/tests/contract/       — parametrized suite every provider must pass

frontend/src/shell/           — desktop shell (Window, Taskbar, StartMenu, Wallpaper)
frontend/src/apps/            — group windows, Lab Monitor, Ops Log, Settings
frontend/src/api.ts           — typed API client

docs/                         — user-facing docs (this file lives here)
docs/providers/               — per-provider setup guides
docs/pro-tips/                — optional hardening guides

scripts/check.sh              — the local CI gate
.github/workflows/ci.yml      — what CI runs (mirrors scripts/check.sh)
```

Adding a **provider**: one file in `backend/app/providers/`, one parser test
per command, one fixture file per command, register in
`backend/app/providers/registry.py`, declare its `frozenset[Capability]`. The
contract test in `backend/tests/contract/test_provider_contract.py` is
parametrized over every provider — new providers pass it for free.

Adding a **module**: one file in `backend/app/modules/`, an entry in
`backend/app/modules/__init__.py`, a `modules.<name>:` block in
`config.example.yaml` (Core never parses `modules:` — each module reads its own
key), a settings toggle key, and a UI toggle in Settings.

---

## When you're stuck

- **Container won't start / config error:** `docker compose logs dubdeck`. Most
  startup failures are a missing host, missing provider, or a `members` ref
  that doesn't resolve. `config.example.yaml` shows every valid shape.
- **`/api/health` is 200 but the UI is blank:** clear the browser cache. The UI
  is served from `DUBDECK_STATIC` (`/app/static` in the container) and is rebuilt
  by `npm run build` in `frontend/`.
- **Auth forgotten / locked out:** `docker compose exec dubdeck rm /data/dubdeck.db`
  resets the database. This also wipes the ops log and settings — last resort.
- **`docker compose build` fails on the frontend stage:** Node 22 LTS is
  pinned in `Dockerfile` (`node:22-alpine`). Mismatch with your local Node
  version won't break the build, but a stale `frontend/node_modules` can — try
  `rm -rf frontend/node_modules frontend/dist && docker compose build`.
- **`pytest` reaches out to a real host:** you bypassed the `transports`
  fixture. The test should fail loudly; fix the test, not the assertion. See
  rule 1 above.