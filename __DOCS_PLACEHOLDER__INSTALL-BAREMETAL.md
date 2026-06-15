# Installing Dubdeck on Bare Metal

This guide covers installing Dubdeck on a Linux bare-metal or VM host **without Docker**. Dubdeck runs under PM2 with a built frontend served statically.

Tested on: **__HOST__** (Debian Trixie, Xen PV guest, __IP__)

---

## Prerequisites

- Debian/Ubuntu (or any Linux with apt)
- Python 3.12+ (`python3 --version`)
- Node.js 20+ and npm
- PM2 (`npm install -g pm2`)
- Git
- Network access from this host to your XCP-ng hypervisors on **port 443 HTTPS**

---

## 1. Clone the repository

```bash
git clone http://__IP__:3000/pacman/dubdeck.git /opt/dubdeck
cd /opt/dubdeck
```

If you already have the repo:

```bash
cd /opt/dubdeck
git remote add gitea http://__IP__:3000/pacman/dubdeck.git
git pull gitea main
```

---

## 2. Set up the Python backend

```bash
cd /opt/dubdeck/backend

# Create a virtual environment
python3 -m venv .venv

# Install dependencies
.venv/bin/pip install -e .
```

**Note:** If you get `ModuleNotFoundError: No module named 'asyncssh'` errors later, the system `asyncssh` package may be interfering. The `.venv` isolation should prevent this. If it persists, check `pip list` inside the venv.

---

## 3. Build the frontend

```bash
cd /opt/dubdeck/frontend
npm install
npm run build
```

The built files go to `/opt/dubdeck/frontend/dist/`. Copy them to the static directory:

```bash
rm -rf /opt/dubdeck/backend/static
cp -r /opt/dubdeck/frontend/dist /opt/dubdeck/backend/static
```

---

## 4. Configure Dubdeck

```bash
# Create the config file
sudo cp /opt/dubdeck/config.example.yaml /opt/dubdeck/config.yaml
sudo chmod 644 /opt/dubdeck/config.yaml
```

Edit `/opt/dubdeck/config.yaml`. Minimum working config for XCP-ng:

```yaml
providers:
  - id: __HOST__-xcp
    type: xcpng
    host: __IP__       # your XCP-ng host
    username: root
    token_secret_env: MARS_PASSWORD
    verify_tls: false

groups:
  __HOST__-vms:
    label: "Mars VMs"
    auto: __HOST__-xcp
```

**Set the password env var before starting:**

```bash
export MARS_PASSWORD=__PASSWORD__
```

Or add it to PM2's environment (see step 5).

---

## 4b. Add a Docker provider (optional)

Docker provider works with a remote Docker host accessed via SSH.
On the Docker host VM, ensure the user has Docker group membership:

```bash
sudo usermod -aG docker <username>
# log out and back in for group change to take effect
```

On the Dubdeck host, ensure:
- Docker CLI is installed (`~/.local/bin/docker`)
- SSH key auth to the Docker host is set up (`~/.ssh/id_rsa` in known_hosts)
- `DOCKER_HOST=ssh://user@<docker-host-ip>` is set in the environment

Test from the Dubdeck host:
```bash
export DOCKER_HOST=ssh://test@__IP__
docker ps   # should list containers
```

Then add to `config.yaml`:
```yaml
hosts:
  local-docker:
    transport: local
    stats: null

providers:
  - id: local-docker
    type: docker
    host: local-docker

groups:
  containers:
    label: "Containers"
    auto: local-docker
```

Add to `start.sh`:
```bash
export DOCKER_HOST=ssh://test@__IP__
export PATH=/home/pacman/.local/bin:$PATH
```

---

## 5. Register with PM2

```bash
cd /opt/dubdeck

# Create an ecosystem file for PM2
cat > ecosystem.config.js << 'EOF'
module.exports = {
  apps: [{
    name: 'dubdeck',
    script: '.venv/bin/python3',
    args: '-m uvicorn app.main:app --host 0.0.0.0 --port 3001',
    cwd: '/opt/dubdeck/backend',
    env: {
      DUBDECK_CONFIG: '/opt/dubdeck/config.yaml',
      DUBDECK_STATIC: '/opt/dubdeck/backend/static',
      MARS_PASSWORD: '__PASSWORD__',
    },
    interpreter: 'none',
    autorestart: true,
    max_restarts: 10,
    max_memory_restart: '1G',
  }]
}
EOF
```

**Start Dubdeck:**

```bash
pm2 start ecosystem.config.js
pm2 save
```

**Check it started:**

```bash
pm2 logs dubdeck --lines 20
```

You should see `Uvicorn running on http://0.0.0.0:3001`.

---

## 6. Open the UI

Navigate to `http://<this-host-ip>:3001` in a browser.

On first run, you will be prompted to set an admin password. Set it to something strong — this is the Dubdeck admin password (not the XCP-ng password).

---

## 7. Verify the XCP-ng provider is working

After logging in, check the status endpoint:

```bash
curl -s -b cookies.txt -c cookies.txt \
  -X POST http://localhost:3001/api/login \
  -H "Content-Type: application/json" \
  -d '{"password":"your-password"}'

curl -s -b cookies.txt http://localhost:3001/api/status | python3 -c "
import json, sys
d = json.load(sys.stdin)
p = d['providers'].get('__HOST__-xcp', {})
print('reachable:', p.get('reachable'))
print('error:', p.get('error'))
resources = d.get('groups', {}).get('__HOST__-vms', {}).get('resources', [])
print('VMs:', len(resources))
"
```

Expected output:
```
reachable: True
error: None
VMs: 35
```

---

## 8. Updating

```bash
cd /opt/dubdeck
git pull gitea main

# Rebuild frontend if it changed
cd frontend && npm install && npm run build && cp -r dist ../backend/static && cd ..

# Restart Dubdeck (keep env vars with --update-env)
MARS_PASSWORD=__PASSWORD__ pm2 restart dubdeck --update-env
```

---

## 9. Console button (VNC/RDP)

The **⎙ Console** button on each VM opens the XCP-ng HTML5 console in a new browser tab. The URL is generated via `VM.get_consoles()` → `console.get_location()`.

**Auth note:** The XCP-ng console requires you to log in with the XCP-ng root password the first time you open it in a browser session. This is handled by XCP-ng itself, not Dubdeck.

---

## Troubleshooting

### "Application startup failed" — provider validation error
- Check `pm2 logs dubdeck --lines 30` for the specific pydantic validation error
- Common cause: `token_secret_env` not set in PM2 environment, or `host` missing from config

### "No module named 'asyncssh'" on startup
- The backend venv is not being used. Verify:
  ```bash
  .venv/bin/python3 -c "import asyncssh; print('ok')"
  ```
- If this fails: `cd /opt/dubdeck/backend && .venv/bin/pip install asyncssh`

### XCP-ng provider shows reachable but no VMs
- The `_build` result dict is correct but the status API intentionally strips resources for the `/api/status` response. Use the Groups UI in the browser — VMs appear there via the `auto:` group.
- Debug: `curl /api/status` and look for `groups.__HOST__-vms.resources`

### Console button returns "no console available"
- Some VMs (templates, halted VMs without console configured) may not have a console ref. This is expected.
- Check via XenAPI: `VM.get_consoles(<vm-ref>)` returns an empty list for these VMs.

### 401 Unauthorized in browser
- Auth is enabled by default. Log in at `/api/login` first to get a session cookie.
- The session cookie path is `/`. If you're accessing via a reverse proxy, ensure the proxy passes cookies correctly.

---

## File Locations

| File | Path |
|---|---|
| Dubdeck repo | `/opt/dubdeck/` |
| Backend | `/opt/dubdeck/backend/` |
| Frontend build | `/opt/dubdeck/frontend/dist/` |
| Static (served) | `/opt/dubdeck/backend/static/` |
| Config | `/opt/dubdeck/config.yaml` |
| PM2 ecosystem | `/opt/dubdeck/ecosystem.config.js` |
| Database | `/opt/dubdeck/dubdeck.db` |
| PM2 logs | `~/.pm2/logs/` |
