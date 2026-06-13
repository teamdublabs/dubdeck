# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.1.0] - 2026-06-13

### Added

**Providers**
- Parallels provider — manage macOS Parallels Desktop VMs over SSH (start, stop, force-stop, suspend, snapshot list/create, disk stats)
- libvirt/KVM provider — manage KVM virtual machines via `virsh` over SSH (same capability set as Parallels)
- Docker provider — manage containers on any SSH-accessible Linux host (start, stop, restart, logs)
- Docker Compose provider — manage Compose stacks under a configurable `stacks_dir` (up, down, restart)
- Proxmox provider *(beta)* — manage QEMU and LXC guests via the Proxmox REST API with token auth; includes async UPID task polling and optional TLS verification override for self-signed certificates
- Hyper-V provider *(experimental)* — manage Hyper-V virtual machines on Windows hosts via OpenSSH + PowerShell; JSON-only command surface, checkpoint-based snapshots, PowerShell-safe quoting

**Core architecture**
- Provider/Transport abstraction — uniform `Provider` protocol over pluggable transports (`SSHTransport`, `LocalTransport`, `FakeTransport`) and HTTP clients (`HttpxClient`) so every provider type is independently testable without touching real infrastructure
- Capability-driven UI — the frontend renders only the actions each provider declares; no hardcoded VM semantics
- Groups — named collections of resources with configurable policies: `start_first` ordering, `ready_probe` state gate, and `snapshot_before_stop`; `auto:` groups track a provider's full resource list without manual enumeration
- Provider contract test suite — a parametrized suite every provider must pass with its fakes (capability ↔ method parity, stable resource IDs, state mapping coverage, command/parameter injection resistance)
- SSE live status — server-sent events stream with stale-while-revalidate so the UI stays responsive when a host is slow or unreachable

**Auth & settings**
- Single-user argon2id authentication, on by default — password set at first run, no default credentials; HTTP-only signed session cookie (SameSite=Lax, 8 h idle expiry); login rate-limited per IP
- `auth.disabled` escape hatch for localhost-only deployments, enforced only when the bind address is loopback
- SQLite-backed settings service — typed key/value store with module toggle support; editable from the Settings window in the UI

**Optional modules**
- Tailscale egress module *(default off)* — per-group internet-access toggle with server-side auto-revoke that survives backend restarts; permanent exit nodes excluded from sweep

**UI shell**
- Desktop-OS metaphor — draggable windows, taskbar with start menu and system tray, neon particle wallpaper
- One window per resource group; window positions persisted in localStorage
- Ops log viewer — append-only audit trail of all mutating actions
- Container/stack logs viewer — tails live logs from LOGS-capable providers
- Onboarding screen for unconfigured deployments with a copy-pasteable minimal config
- Per-host/provider diagnostic card surfacing SSH and API error details when a host is unreachable
- Settings window — module toggles, branding name, change password

[Unreleased]: https://github.com/teamdublabs/dubdeck/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/teamdublabs/dubdeck/releases/tag/v0.1.0
