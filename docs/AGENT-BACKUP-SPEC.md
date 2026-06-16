# Agent Backup System — Specification
**Author:** N1H Tech (Gemma)
**Date:** 2026-06-15
**Status:** Draft

---

## Overview

Each AI agent instance (OpenClaw, Hermes, NanoClaw, ResonantOS) backs up its critical files to a shared backup store (Nextcloud, FTP, or filesystem sync). Treadstone can pull from this store to repair or migrate an agent.

**Design principle:** Agents are responsible for their own backups. Treadstone is the repair coordinator — it pulls from the backup store, it doesn't push to agents.

---

## Backup Targets

| Target | Access | Use case |
|--------|--------|----------|
| **Nextcloud WebDAV** | `https://<host>/remote.php/dav/files/<user>/` | Primary — versioned, accessible from any device |
| **FTP/SFTP** | `ftp://<host>/backups/` | Alternative if Nextcloud unavailable |
| **NFS/SMB share** | `/mnt/backup/` on local network | Fast LAN backup |
| **Per-agent self-hosting** | Each agent backs up to its own cloud storage | Last resort if local network is down |

---

## Files to Back Up Per Agent Type

### OpenClaw (Gemma, Freya, etc.)
```
~/.openclaw/workspace/
  AGENTS.md, SOUL.md, MEMORY.md, HEARTBEAT.md, USER.md, TOOLS.md
~/.openclaw/memory/
  YYYY-MM-DD.md files
~/.openclaw/agents/
  main/agent/models.json
~/.openclaw/skills/          (optional — can be reinstalled)
~/.openclaw/config.yaml      (if exists)
```

### Hermes (Ollama AI server)
```
~/.hermes/
  config.yaml, system.prompt, agent.yaml
~/.config/hermes/
  model_configs/
/etc/hermes/                 (service config)
```

### NanoClaw (lightweight variant)
```
~/.nanoclaw/
  workspace/, memory/, config.yaml
```

### ResonantOS (resonantarctl)
```
~/.local/share/resonantar/
  vault/                     (identity — AES-256-GCM encrypted)
  identity.json             (if any)
~/.config/resonantar/
  config.yaml
/opt/resonantar/
  resonantarctl binary
```

---

## Backup Naming Convention

```
<agent-id>/             — top-level directory per agent
  <agent-id>-<timestamp>.tar.gz  — full backup (workspace + memory + config)
  <agent-id>-manifest.json      — metadata: timestamp, version, files included, checksum
  <agent-id>-latest -> <agent-id>-<timestamp>.tar.gz  (symlink for easy latest access)
```

**Manifest format:**
```json
{
  "agent_id": "gemma",
  "agent_type": "openclaw",
  "timestamp": "2026-06-15T17:00:00Z",
  "version": "1.0",
  "files": [
    "workspace/AGENTS.md",
    "workspace/MEMORY.md",
    "workspace/SOUL.md",
    "memory/2026-06-15.md",
    "agents/main/agent/models.json"
  ],
  "checksum": "sha256:abc123...",
  "size_bytes": 409600
}
```

---

## Backup Schedule

| Event | Trigger |
|-------|---------|
| On memory update | Agent writes critical memory file → triggers backup |
| Daily cron | `09:00` UTC — full workspace backup |
| Before skill install | Backup before adding new skill |
| On agent version upgrade | Backup before updating |

---

## Treadstone Repair Flow

```
1. DISCOVER  → Scan backup store for agent directories
2. INVENTORY → Read manifests, build agent registry from backups
3. HEALTH    → Compare backup age + checksum vs running agent
4. REPAIR    → Pull backup → extract to target VM/host → restart agent
5. LOG       → Record repair attempt in repair KB
```

### Health Check Rules
- Backup age > 7 days → `STALE` flag
- Backup age > 30 days → `CRITICAL` flag
- No backup found → `NO_BACKUP` flag
- Checksum mismatch → `CORRUPTED` flag

### Repair Options
| Situation | Action |
|----------|--------|
| Agent crashed, files intact | Restart service (PM2/systemd) |
| Agent files corrupted | Pull latest backup → restore workspace → restart |
| VM rebuilt | Pull backup → extract to new VM → re-register with Treadstone |
| Full identity loss | Pull ResonantOS vault → attempt vault recovery |

---

## Integration with Treadstone

**New Treadstone module:** `agent-repair`

**Database additions (__GITEA__ `__TREADSTONE__` DB):**
```sql
ALTER TABLE agents ADD COLUMN last_backup datetime;
ALTER TABLE agents ADD COLUMN backup_path varchar(256);
ALTER TABLE agents ADD COLUMN backup_status enum('current','stale','critical','no_backup');
```

**Backup store access:**
- Treadstone reads from Nextcloud WebDAV or FTP — credentials stored in vault
- No agent-specific access needed at repair time
- Agent writes backups independently — no special permissions for Treadstone

---

## Per-Agent Backup Script (OpenClaw example)

Each agent runs a lightweight backup script via cron:

```bash
#!/bin/bash
# openclaw-backup.sh — run on each OpenClaw instance via cron

AGENT_ID="gemma"
BACKUP_TARGET="https://nextcloud.lan/remote.php/dav/files/pacman/backups/${AGENT_ID}"
TIMESTAMP=$(date +%Y%m%d-%H%M%S)
WORKDIR="/tmp/openclaw-backup-${TIMESTAMP}"

mkdir -p "${WORKDIR}/workspace"
cp -r ~/.openclaw/workspace/*.md "${WORKDIR}/workspace/" 2>/dev/null
cp -r ~/.openclaw/memory/*.md "${WORKDIR}/memory/" 2>/dev/null
cp -r ~/.openclaw/agents/main/agent/models.json "${WORKDIR}/agents/" 2>/dev/null

tar czf "${WORKDIR}.tar.gz" -C "${WORKDIR}" .
sha256sum "${WORKDIR}.tar.gz" > "${WORKDIR}.tar.gz.sha256"

# Write manifest
cat > "${WORKDIR}-manifest.json" << EOF
{
  "agent_id": "${AGENT_ID}",
  "agent_type": "openclaw",
  "timestamp": "$(date -Iseconds)",
  "version": "1.0",
  "files": ["workspace/", "memory/", "agents/"],
  "checksum": "$(cat ${WORKDIR}.tar.gz.sha256 | cut -d' ' -f1)",
  "size_bytes": $(stat -c%s "${WORKDIR}.tar.gz")
}
EOF

# Upload to Nextcloud via WebDAV
curl -u "${NEXTCLOUD_USER}:${NEXTCLOUD_PASS}" \
  -T "${WORKDIR}.tar.gz" \
  "${BACKUP_TARGET}/${AGENT_ID}-${TIMESTAMP}.tar.gz"

curl -u "${NEXTCLOUD_USER}:${NEXTCLOUD_PASS}" \
  -T "${WORKDIR}-manifest.json" \
  "${BACKUP_TARGET}/${AGENT_ID}-manifest.json"

# Update latest symlink on Nextcloud (or skip — latest is newest by timestamp)
rm -rf "${WORKDIR}" "${WORKDIR}.tar.gz"

echo "Backup complete: ${AGENT_ID}-${TIMESTAMP}"
```

---

## Key Design Decisions

1. **Agent drives backup, not Treadstone** — agents know their own critical files
2. **Shared backup store** — Nextcloud WebDAV is the primary target, accessible from anywhere
3. **Manifest-driven inventory** — Treadstone reads manifests to build the agent registry from backups
4. **Agent type flexibility** — each agent type has its own file set, but the naming convention is uniform
5. **Checksum verification** — corrupt backups detected before repair attempt
6. **ResonantOS vault is special** — the vault is AES-256-GCM encrypted, repair means restoring the encrypted blob, not decrypting it

---

## Nextcloud Setup for Agent Backups

**User:** `pacman` (or a dedicated `agent-backups` user)
**Folder structure:**
```
backups/
  gemma/
    gemma-2026-06-15-170000.tar.gz
    gemma-manifest.json
  __AGENT_NAME__/
    __AGENT__-YYYY-MM-DD-HHMMSS.tar.gz
    __AGENT__-manifest.json
  __AGENT_NAME__/
    __AGENT__-YYYY-MM-DD-HHMMSS.tar.gz
    __AGENT__-manifest.json
```

**Retention:** Keep last 7 backups per agent (rotated by timestamp).

---

## Status

- [ ] Write per-agent backup scripts (openclaw, hermes, nanoclaw, resonantar)
- [ ] Configure Nextcloud WebDAV credentials per agent
- [ ] Add cron entries on each agent
- [ ] Add `agent-repair` module to Treadstone
- [ ] Add `last_backup`, `backup_path`, `backup_status` columns to `agents` table
- [ ] Test: crash a test agent → verify backup → repair from backup
