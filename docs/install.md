# Installing Dubdeck

Dubdeck runs as a single Docker container with a built-in frontend. The backend
SSHes (or runs locally) to the hosts declared in your `config.yaml`; it needs an
SSH key and a known_hosts file to reach them.

---

## Docker Compose quickstart (recommended)

**Prerequisites:** Docker Engine with the Compose v2 plugin.

### 1. Get the files

```sh
git clone https://github.com/teamdublabs/dubdeck.git
cd dubdeck
```

### 2. Prepare your SSH credentials

Dubdeck needs an SSH private key to reach remote hosts and a `known_hosts` file
for host-key verification. Put them somewhere outside the repo:

```sh
mkdir -p ~/.dubdeck/secrets
cp ~/.ssh/id_ed25519 ~/.dubdeck/secrets/ssh_key
chmod 600 ~/.dubdeck/secrets/ssh_key
# Populate known_hosts — scan each host you'll declare in config.yaml:
ssh-keyscan 192.0.2.10 >> ~/.dubdeck/secrets/known_hosts
```

### 3. Write your config

```sh
cp config.example.yaml ~/.dubdeck/config.yaml
$EDITOR ~/.dubdeck/config.yaml
```

Declare your hosts, providers, and groups. See [configuration.md](configuration.md)
for the full reference. The example file is annotated — it is the fastest way to
understand the shape.

### 4. Update compose.yaml volume paths

Edit `compose.yaml` so the volumes point to your credentials and config:

```yaml
volumes:
  - ~/.dubdeck/config.yaml:/config.yaml:ro
  - ~/.dubdeck/secrets/ssh_key:/run/secrets/ssh_key:ro
  - ~/.dubdeck/secrets/known_hosts:/run/secrets/known_hosts:ro
  - dubdeck-data:/data
```

### 5. Start

```sh
docker compose up -d
```

The container listens on `127.0.0.1:8400` (mapped to the internal port 8000).

### 6. Open the UI

Navigate to `http://127.0.0.1:8400` in a browser on the same machine. On first
run you will be prompted to set an admin password before anything else is
accessible. Auth is enabled by default — see [SECURITY.md](SECURITY.md) for the
loopback-only opt-out.

### Environment variables

The container image reads these env vars (set in `compose.yaml` or an env file):

| Variable | Default | Purpose |
|---|---|---|
| `DUBDECK_CONFIG` | `/config.yaml` | Path to `config.yaml` inside the container |
| `DUBDECK_DB` | `/data/dubdeck.db` | SQLite database path |
| `DUBDECK_STATIC` | `/app/static` | Built frontend directory |
| `DUBDECK_SSH_KEY` | `/run/secrets/ssh_key` | SSH private key (informational; used by the SSH transport) |
| `DUBDECK_KNOWN_HOSTS` | `/run/secrets/known_hosts` | Known hosts file |
| `DUBDECK_BIND` | `127.0.0.1` | Address uvicorn binds to — must match the real bind for the auth-disabled check |
| `DUBDECK_ALLOWED_HOSTS` | `127.0.0.1,localhost` | Comma-separated Host header allowlist (DNS-rebinding guard) |
| `DUBDECK_LOG_LEVEL` | `INFO` | Python logging level |

Provider-specific secrets (e.g. a Proxmox API token) are passed as additional
env vars named in your config. See [configuration.md](configuration.md#api-providers-proxmox).

### PUID (Docker Desktop for Mac)

Under Docker Desktop with VirtioFS, bind-mounted files are owned by your host
UID. Build with `--build-arg PUID=501` (or your UID) so the SSH key stays
readable inside the container:

```sh
docker compose build --build-arg PUID=$(id -u)
docker compose up -d
```

---

## Bare-metal (development or advanced)

Use this path to run the backend and frontend separately without Docker, for
example during development.

**Prerequisites:**
- Python 3.12+ with [uv](https://github.com/astral-sh/uv)
- Node.js LTS with npm

### Backend

```sh
cd backend
uv sync             # installs dependencies into .venv
uv run fastapi dev app/main.py
```

The dev server reloads on file changes and serves on `http://127.0.0.1:8000`.
Point it at your config with the `DUBDECK_CONFIG` env var:

```sh
DUBDECK_CONFIG=../config.yaml uv run fastapi dev app/main.py
```

### Frontend

In a separate terminal:

```sh
cd frontend
npm install
npm run dev
```

The Vite dev server proxies `/api` requests to the backend.

### Production frontend build

```sh
cd frontend
npm run build   # output in frontend/dist/
```

The built `dist/` is what the Docker image serves. Point the backend at it with
`DUBDECK_STATIC=frontend/dist`.

### Running the full check suite

```sh
bash scripts/check.sh
```

This runs `ruff` + `pytest` (backend) and `tsc` + `vitest` + `npm run build`
(frontend). Every pull request must leave this green.

---

## Upgrading

With Docker Compose, pull the new image and rebuild:

```sh
docker compose pull   # or: docker compose build --pull
docker compose up -d --build
```

The SQLite database (`dubdeck-data` volume) persists across upgrades — settings,
the ops log, and the auth credential are preserved. Your `config.yaml` is
mounted from outside the container and is never touched by Dubdeck.

---

## Next steps

- [configuration.md](configuration.md) — full `config.yaml` schema reference
- [providers/index.md](providers/index.md) — provider capability matrix
- [SECURITY.md](SECURITY.md) — threat model and hardening advice
