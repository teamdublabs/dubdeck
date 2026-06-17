# AGENTS.md — pointer

The full Dubdeck agent guide lives at **[docs/AGENTS.md](docs/AGENTS.md)**.
This top-level file exists so coding agents (Claude Code, Cursor, Aider, pi, …)
pick it up at startup regardless of which directory the session starts in.

**TL;DR** — Dubdeck is a FastAPI + React control panel for VMs and containers,
driven by a declarative `config.yaml`. To set it up:

- **Production:** Docker Compose, see [docs/AGENTS.md § Path A](docs/AGENTS.md#path-a--docker-compose-production).
- **Development (with hot-reload):** backend + frontend split, see [docs/AGENTS.md § Path B](docs/AGENTS.md#path-b--dev-backend--frontend-split).
- **Demo (no real infra):** `backend/demo_server.py`, see [docs/AGENTS.md § Path C](docs/AGENTS.md#path-c--demo-no-real-infrastructure).

Before considering any change done, run **`bash scripts/check.sh`** — it is the
same gate CI runs (gitleaks → ruff → pytest → tsc → vitest → build).

The non-negotiables are listed in
[docs/AGENTS.md § Hard rules](docs/AGENTS.md#hard-rules--do-not-violate). The
biggest one: **tests never make real SSH or network calls** — use the
`FakeTransport` fixture in `backend/tests/conftest.py`.