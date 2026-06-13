#!/bin/bash
# dubdeck forced-command shim — the ONLY thing the dubdeck key can execute on this Mac.
# Anything not matching the allowlist below is rejected.
# Canonical copy lives in the repo (docs/pro-tips/mac-parallels-hardening/dubdeck-shim.sh);
# setup.sh installs it to ~/.dubdeck/dubdeck-shim.sh and generates ~/.dubdeck/dubdeck-vms (the
# per-VM allowlist) from inventory.yaml — a stolen key can only touch lab VMs,
# not every VM on the host.
set -euo pipefail
cmd="${SSH_ORIGINAL_COMMAND:-}"
ALLOW="${DUBDECK_VM_ALLOWLIST:-$HOME/.dubdeck/dubdeck-vms}"

vm_allowed() {
    local name="$1"
    # shlex.quote single-quotes names containing spaces — strip one layer
    name="${name#\'}"; name="${name%\'}"
    [[ -f "$ALLOW" ]] && grep -qxF "$name" "$ALLOW"
}

case "$cmd" in
    "prlctl list --all -o name,status")
        exec /usr/local/bin/prlctl list --all -o name,status
        ;;
    "dubdeck-stats")
        sysctl -n vm.loadavg hw.memsize
        vm_stat
        # /System/Volumes/Data is the real APFS data volume; bare / is the
        # sealed system snapshot and reads ~0% used.
        exec df -kP /System/Volumes/Data
        ;;
    "dubdeck-vm-disks")
        # Host-disk footprint per VM (KiB + .pvm path) — names derive from
        # the bundle basename, so no per-VM arguments to validate.
        exec du -sk "$HOME"/Parallels/*.pvm
        ;;
esac

# A VM name is either a bare safe token or a single-quoted string of safe
# characters (shlex.quote output) — no shell metacharacters can pass.
VM="(\'[A-Za-z0-9._\ -]+\'|[A-Za-z0-9._-]+)"

# start/stop/suspend — lab VMs only (allowlist), optional stop flags
if [[ "$cmd" =~ ^prlctl\ (start|stop|suspend)\ $VM(\ --acpi|\ --kill)?$ ]]; then
    if vm_allowed "${BASH_REMATCH[2]}"; then exec bash -c "/usr/local/bin/$cmd"; fi
fi

# snapshot list (JSON) and snapshot create with a safe -n name; restore and
# delete are deliberately NOT allowlisted (destructive — see SPEC boundaries)
if [[ "$cmd" =~ ^prlctl\ snapshot-list\ $VM\ -j$ ]]; then
    if vm_allowed "${BASH_REMATCH[1]}"; then exec bash -c "/usr/local/bin/$cmd"; fi
fi
if [[ "$cmd" =~ ^prlctl\ snapshot\ $VM\ -n\ [A-Za-z0-9._-]+$ ]]; then
    if vm_allowed "${BASH_REMATCH[1]}"; then exec bash -c "/usr/local/bin/$cmd"; fi
fi

# Gateway egress control runs through the host (prlctl exec) instead of SSH to
# the gateway's tailnet IP — the VM-side path flaps when Parallels NAT is sick,
# and a missed revoke once left egress open 35 min past its window (2026-06-10).
# Only the two tailscale verbs Dubdeck needs are allowed, nothing else.
if [[ "$cmd" =~ ^prlctl\ exec\ $VM\ tailscale\ (status\ --json|set\ --exit-node=[A-Za-z0-9.-]*)$ ]]; then
    if vm_allowed "${BASH_REMATCH[1]}"; then exec bash -c "/usr/local/bin/$cmd"; fi
fi

echo "dubdeck-shim: command not allowed: $cmd" >&2
exit 127
