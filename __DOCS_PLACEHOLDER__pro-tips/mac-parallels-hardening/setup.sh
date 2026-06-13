#!/usr/bin/env bash
# Dubdeck one-time access setup — run this yourself on workstation (no sudo needed).
# Automation deliberately cannot run this: it installs a persistent service and
# grants the container key access to lab machines.
#
# What it does:
#   1. Loads the user-level loopback sshd (127.0.0.1:2222, forced-command key)
#   2. Installs the restricted dubdeck pubkey on server01 + all three gateway VMs
#      (briefly boots vm-fw-01 / vm-fw-02 if they're off, restores state)
#   3. Builds secrets/known_hosts so the container pins every host key
#   4. Verifies: LAN-unreachable, shim allowlist, key restrictions
set -euo pipefail

SECRETS=~/docker/stacks/dubdeck/secrets
PUB="$(cat $SECRETS/dubdeck_key.pub)"
# Direct-SSH targets only. Parallels gateways (vm-fw-01, vm-fw-02) are
# reached via `prlctl exec` on the host channel — the backend never SSHes to
# their tailnet IPs, so they need no key install and no host-key pinning.
SERVER01=192.0.2.20
SLAB_FW_02=192.0.2.30

echo "== 0. forced-command shim (canonical copy from this repo) =="
install -m 755 "$(dirname "$0")/dubdeck-shim.sh" ~/.dubdeck/dubdeck-shim.sh
echo "OK: shim installed"

echo "== 0b. VM allowlist from inventory.yaml =="
# Gateways appear as 'vm: <name>', members as '- <name>'. Names must be
# space-free (true today); the shim rejects any VM not in this file.
awk '/^[[:space:]]+vm:/ {print $2} /^[[:space:]]+- /{print $2}' \
    "$(dirname "$0")/../inventory.yaml" | sort -u > ~/.dubdeck/dubdeck-vms
VM_COUNT=$(wc -l < ~/.dubdeck/dubdeck-vms | tr -d ' ')
[ "$VM_COUNT" -ge 1 ] || { echo "FAIL: empty VM allowlist — check inventory.yaml"; exit 1; }
echo "OK: $VM_COUNT lab VMs allowlisted: $(paste -sd' ' - < ~/.dubdeck/dubdeck-vms)"

echo "== 1. dubdeck sshd LaunchAgent =="
if ! launchctl print "gui/$(id -u)/com.dubdeck.sshd" &>/dev/null; then
    cp "$(dirname "$0")/com.dubdeck.sshd.plist" ~/Library/LaunchAgents/
    launchctl bootstrap "gui/$(id -u)" ~/Library/LaunchAgents/com.dubdeck.sshd.plist
fi
sleep 2
nc -z 127.0.0.1 2222 && echo "OK: listening on 127.0.0.1:2222"
LAN_IP="$(ipconfig getifaddr en0 || true)"
if [ -n "$LAN_IP" ] && nc -z -G 3 "$LAN_IP" 2222 2>/dev/null; then
    echo "FAIL: 2222 reachable on LAN ($LAN_IP) — stop and investigate"; exit 1
fi
echo "OK: not reachable on LAN"

echo "== 2. install restricted key on remote targets =="
install_key() {
    # accept-new: first contact with a fresh host succeeds, a CHANGED key
    # still fails loudly (that's the case worth stopping on).
    ssh -o BatchMode=yes -o StrictHostKeyChecking=accept-new -o ConnectTimeout=10 "labuser@$1" \
        "grep -qF 'dubdeck-container' ~/.ssh/authorized_keys 2>/dev/null || \
         { mkdir -p ~/.ssh && chmod 700 ~/.ssh && echo 'restrict $PUB' >> ~/.ssh/authorized_keys && chmod 600 ~/.ssh/authorized_keys; }" \
        && echo "OK: key on $1"
}
install_key $SERVER01
install_key $SLAB_FW_02

echo "== 3. known_hosts =="
: > /tmp/dubdeck_kh
ssh-keyscan -t ed25519 $SERVER01 $SLAB_FW_02 2>/dev/null >> /tmp/dubdeck_kh
ssh-keyscan -t ed25519 -p 2222 127.0.0.1 2>/dev/null \
    | sed 's/^\[127.0.0.1\]:2222/[host.docker.internal]:2222/' >> /tmp/dubdeck_kh
sort -u /tmp/dubdeck_kh > $SECRETS/known_hosts && rm /tmp/dubdeck_kh
chmod 600 $SECRETS/known_hosts
echo "OK: $(wc -l < $SECRETS/known_hosts | tr -d ' ') host keys pinned"

echo "== 4. shim verification =="
dubdeck_ssh() {
    ssh -i $SECRETS/dubdeck_key -p 2222 -o UserKnownHostsFile=$SECRETS/known_hosts \
        -o HostKeyAlias=host.docker.internal labuser@127.0.0.1 "$@"
}
dubdeck_ssh "prlctl list --all -o name,status" | head -3
echo "next line must be 'command not allowed' (raw shell):"
dubdeck_ssh "id" || true
echo "next line must be 'command not allowed' (non-lab VM, allowlist):"
dubdeck_ssh "prlctl start SomeOtherVM" || true
echo "snapshot-list on a lab VM must work (shim v2 verb):"
dubdeck_ssh "prlctl snapshot-list vm-a -j" | head -2

echo "== done — restart the stack: cd ~/docker/stacks/dubdeck && docker compose up -d =="
