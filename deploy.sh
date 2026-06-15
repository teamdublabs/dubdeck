#!/bin/bash
# Dubdeck Deployment Script — treadstone-engine VM
# Deploys Dubdeck on port 3001, Ops Center stays on port 3000
# Run as root on 192.168.0.13

set -e

DUBDECK_DIR="/opt/dubdeck"
CONFIG_FILE="/opt/dubdeck/config.yaml"
PORT=3001
LOG_FILE="/var/log/dubdeck.log"

echo "=== Dubdeck Deployment ==="
echo "Directory: $DUBDECK_DIR"
echo "Port: $PORT"
echo ""

# ── System deps ──────────────────────────────────────────────────────────────
echo "[1/6] Installing system dependencies..."
apt-get update
apt-get install -y python3-pip python3-venv git npm
echo "Done."

# ── Clone or update ────────────────────────────────────────────────────────
if [ -d "$DUBDECK_DIR" ]; then
    echo "[2/6] Dubdeck already exists — pulling latest..."
    cd "$DUBDECK_DIR" && git pull
else
    echo "[2/6] Cloning Dubdeck from GitHub..."
    git clone https://github.com/teamdublabs/dubdeck.git "$DUBDECK_DIR"
fi

# ── Backend setup ──────────────────────────────────────────────────────────
echo "[3/6] Setting up Python backend..."
cd "$DUBDECK_DIR/backend"
python3 -m venv "$DUBDECK_DIR/.venv"
source "$DUBDECK_DIR/.venv/bin/activate"
pip install --upgrade pip
pip install -e .
deactivate
echo "Backend installed."

# ── Frontend build ──────────────────────────────────────────────────────────
echo "[4/6] Building frontend..."
cd "$DUBDECK_DIR/frontend"
npm install
npm run build
echo "Frontend built."

# ── Config ──────────────────────────────────────────────────────────────────
echo "[5/6] Writing config.yaml..."
cat > "$CONFIG_FILE" << 'EOF'
# Dubdeck config — N1H Tech homelab
# Edit hosts/providers/groups to match your infrastructure

hosts:
  mars:
    transport: ssh
    address: 192.168.0.156
    user: root
    port: 22
    stats: linux

  zeus:
    transport: ssh
    address: 192.168.0.135
    user: root
    port: 22
    stats: linux

  gamera:
    transport: ssh
    address: 192.168.0.131
    user: root
    port: 22
    stats: linux

  saturn:
    transport: ssh
    address: 192.168.0.228
    user: root
    port: 22
    stats: linux

providers:
  # XCP-ng provider — available once merged from PR #1
  # - id: mars-xcpng
  #   type: xcpng
  #   host: mars

groups:
  homelab:
    label: "Homelab VMs"
    auto: local-kvm
EOF
echo "Config written to $CONFIG_FILE"

# ── PM2 startup ──────────────────────────────────────────────────────────────
echo "[6/6] Starting Dubdeck via PM2..."

# Install PM2 if not present
npm list -g pm2 2>/dev/null | grep -q pm2 || npm install -g pm2

cd "$DUBDECK_DIR"
source .venv/bin/activate

# Write startup script
cat > "$DUBDECK_DIR/start.sh" << STARTSCRIPT
#!/bin/bash
source "$DUBDECK_DIR/.venv/bin/activate"
export DUBDECK_CONFIG="$CONFIG_FILE"
export PORT=$PORT
cd "$DUBDECK_DIR/backend"
exec python3 -m uvicorn app.main:app --host 0.0.0.0 --port $PORT
STARTSCRIPT
chmod +x "$DUBDECK_DIR/start.sh"

# Register with PM2
cd "$DUBDECK_DIR"
pm2 delete dubdeck 2>/dev/null || true
pm2 start "$DUBDECK_DIR/start.sh" --name dubdeck
pm2 save

# Start on boot
pm2 startup 2>/dev/null || true
pm2 save

echo ""
echo "=== Deployment complete ==="
echo "Dubdeck:  http://192.168.0.13:$PORT"
echo "Ops Center: http://192.168.0.13:3000"
echo ""
echo "Useful commands:"
echo "  pm2 logs dubdeck          — view logs"
echo "  pm2 restart dubdeck       — restart"
echo "  pm2 stop dubdeck         — stop"
echo "  pm2 list                 — status"
echo ""
echo "Config file: $CONFIG_FILE"
echo "Edit this file to add hosts/providers, then restart: pm2 restart dubdeck"
