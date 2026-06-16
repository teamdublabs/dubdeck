# XCP-ng Provider for Dubdeck — Specification
**Author:** N1H Tech (Gemma)
**Date:** 2026-06-15
**Status:** Implemented — in production on __HOST__
**Based on:** Dubdeck v0.1.0 provider architecture

---

## Overview

The XCP-ng provider connects **directly via XenAPI XML-RPC** (HTTPS port 443) — no `xe` CLI, no SSH tunnel, no sudo required. Any machine that can reach the XCP-ng host's port 443 can manage it.

This is a production implementation, not a draft.

---

## Architecture

```
Dubdeck backend (__HOST__)
    │
    │  xmlrpc.client.ServerProxy("https://<xcp-ng-host>:443/", ssl_context)
    │
    └───► XCP-ng host (__XCPNG_HOST__: __XCPNG_HOST_IP__)
              XenAPI XML-RPC endpoint (port 443)
```

**Key design decisions:**
- Pure Python `xmlrpc.client` — no external dependencies beyond the standard library
- Session authentication: `session.login_with_password("root", password)` → session ref cached and reused
- XenAPI object style: `session.xenapi.VM.get_all_records()` via a session-wrapping proxy
- No `xe` CLI, no SSH, no sudo — connects directly from wherever Dubdeck runs
- Self-signed certs handled by setting `ssl.verify_mode = ssl.CERT_NONE`

---

## Resource Identification

XCP-ng VMs are identified by their **XenAPI OpaqueRef** (e.g. `OpaqueRef:343fa1d8-947a-43eb-99ca-c49c1752ef71`). This is the canonical `id` used in Dubdeck resource refs.

```
ref format: <provider-id>/<vm-opaqueref>
example:  __HOST__-xcp/OpaqueRef:343fa1d8-947a-43eb-99ca-c49c1752ef71
```

The human-readable `name-label` is stored as `Resource.name`.

---

## Implementation

**File:** `backend/app/providers/xcpng.py`

### XenAPISession class
Wraps `xmlrpc.client.ServerProxy` with:
- Automatic session login/logout via context manager
- `xenapi` property returning a session-wrapped proxy (`session.xenapi.VM.get_all_records()`)
- `_call_single()` helper that unwraps `Status`/`Value` and raises on `Failure`

### XCPNgProvider class
- Inherits `Provider` (not `CommandProvider`) — no Transport needed
- Built by `_build_xcpng()` in `main.py` — bypasses the `HttpClient` requirement
- `_async_session()` caches the XenAPI session across async calls

### Key methods

| Method | XenAPI call | Notes |
|---|---|---|
| `list_resources()` | `VM.get_all_records()` | Excludes templates |
| `start(rid)` | `VM.start(rid)` | May return `NO_HOSTS_AVAILABLE` if no host can run the VM |
| `stop(rid)` | `VM.shutdown(rid)` | Graceful shutdown |
| `force_stop(rid)` | `VM.hard_shutdown(rid)` | Immediate power off |
| `restart(rid)` | `VM.reboot(rid)` | Graceful reboot |
| `suspend(rid)` | `VM.suspend(rid)` | RAM to disk |
| `snapshot_list(rid)` | `VM.get_snapshots(rid)` | Child snapshots only |
| `snapshot_create(rid, name)` | `VM.snapshot(rid, name)` | Returns new snapshot ref |
| `disk_stats()` | `VM.get_all_records()` + `VDI.get_record()` | Per-VM disk usage |
| `logs(rid)` | `VM.get_consoles()` → `console.get_location()` | Returns console URL |
| `console(rid)` | `VM.get_consoles()` → `console.get_location()` | Returns HTML5 console URL |

### Console URL
The console URL returned is an XCP-ng HTML5 console path:
```
https://<xcp-ng-host>/console?uuid=<vm-uuid>
```
Opens in a browser — XCP-ng handles auth via its own session (root password or API token).

---

## Config Example

```yaml
providers:
  - id: __HOST__-xcp
    type: xcpng
    host: __XCPNG_HOST_IP__        # XCP-ng host address (must be reachable on port 443)
    username: root             # XenAPI username
    token_secret_env: MARS_PASSWORD  # env var holding the password
    verify_tls: false          # always false for homelab self-signed certs

groups:
  __HOST__-vms:
    label: "__XCPNG_HOST__ VMs"
    auto: __HOST__-xcp             # auto group — tracks all VMs from the provider
```

**Password resolution:** The provider checks `token_secret_env` first, then falls back to inline `password`. Using `token_secret_env` is strongly preferred (password never in config file).

---

## Auth

XenAPI session login:
```python
session.login_with_password("root", "__XCPNG_PASSWORD__")
→ {"Status": "Success", "Value": "OpaqueRef:680ba1be-61bd-41db-b6c3-48fe84a79ba9"}
```

Session refs expire after 24 hours by default on XCP-ng. The `XenAPISession` class caches the session and re-logs in automatically on `logout()`.

---

## Known Limitations

1. **Console auth:** The HTML5 console URL requires the user to log into XCP-ng separately. There is no embedded SSO.
2. **NO_HOSTS_AVAILABLE on start:** Real XCP-ng runtime error — the VM's host can't provide resources. Not a protocol error.
3. **Session expiry:** Sessions expire after 24h. The current implementation re-logs in on the next call after `logout()`.
4. **VM.get_consoles (plural):** The singular `VM.get_console` method does not exist on this XCP-ng version. Always use `VM.get_consoles` (returns a list).

---

## N1H Tech Deployment

| Host | IP | Role | Status |
|---|---|---|---|
| __XCPNG_HOST__ | __XCPNG_HOST_IP__ | Primary hypervisor, 56GB RAM, 78 VMs | ✅ Running |
| __XCPNG_HOST__ | __XCPNG_HOST_IP__ | Secondary | ⚠️ Offline |
| __XCPNG_HOST__ | __XCPNG_HOST_IP__ | XCP-ng | ⚠️ Offline |
| __XCPNG_HOST__ | __XCPNG_HOST_IP__ | XCP-ng | ⚠️ Offline |

**Test environment:** __TREADSTONE__ (__TREADSTONE_IP__, Xen PV guest) → __XCPNG_HOST__ (__XCPNG_HOST_IP__) via direct XenAPI on port 443.

---

## File Layout

```
backend/app/providers/
├── xcpng.py          # XCPNgProvider + XenAPISession + XenAPIWrapper
├── __init__.py       # exports XCPNgProvider
backend/app/main.py   # _build_xcpng() wires the provider
backend/app/config.py # Provider model gains username/password fields
```
