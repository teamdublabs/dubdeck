# Security

This document describes Dubdeck's threat model, the protections it provides, what
it deliberately does not protect, and guidance for hardening a deployment.

---

## What Dubdeck is and is not

Dubdeck is a **control plane for lab infrastructure**. Its API can start, stop,
suspend, and snapshot VMs and containers. The security goal is to ensure that only
an authorised operator can trigger those actions.

**Dubdeck is not a replacement for network isolation.** The right mental model is
defense-in-depth:

1. **Network boundary first.** Bind Dubdeck to loopback (`127.0.0.1`) and expose
   it only through a VPN or reverse proxy you control. Do not put it on a public
   interface. This is the largest single risk reduction available.
2. **Auth layer second.** Single-user authentication (on by default) is the gate
   in front of the API. Even on a local-only setup it is a useful second factor.

Neither layer substitutes for the other.

---

## Threat model

**Assets:** the ability to start/stop/snapshot lab resources; access to the ops
log; the admin password hash.

**Out-of-scope threats:**

- An attacker with access to the machine Dubdeck runs on — they own the process.
- A malicious provider host — Dubdeck trusts the hosts it is configured to reach.
- Destructive operations — Dubdeck deliberately has no delete/undefine/snapshot-restore
  operations in v1.

**In-scope threats:**

| Threat | Mitigation |
|---|---|
| Unauthenticated API access | Single-user auth (argon2id password, HMAC session cookie). Enabled by default. |
| DNS rebinding | Host-allowlist middleware rejects any `Host:` header not in `DUBDECK_ALLOWED_HOSTS`. |
| Drive-by CSRF from a cross-site page | The session cookie is `SameSite=Lax` (the primary control), and `Sec-Fetch-Site: cross-site` requests to `/api/*` are additionally rejected as defense-in-depth. |
| Auth disabled on a non-loopback bind | The server refuses to start if `auth.enabled=false` while bound to a non-loopback address. Enforced in code, not just documented. |
| Credential brute-force | Login rate-limited: 5 attempts per IP per 60 seconds. Excess attempts return HTTP 429. |
| Session token theft enabling permanent access | Password change rotates the server-side signing secret — every previously-issued session token is immediately invalidated. |

---

## Authentication detail

### Password

The admin password is set on first run (no default credentials — the API returns
403 until a password is set). It is hashed with **argon2id** (via `argon2-cffi`)
before storage in SQLite.

Minimum length: 8 characters. To change the password: Settings → Change password
(re-checks the current password even with a valid session).

### Session cookie

After a successful login, the server issues a session cookie:

- Name: `dubdeck_session`
- Signing: HMAC-SHA256 with a server-side secret persisted in SQLite (survives
  restarts; rotated on password change)
- Idle TTL: 8 hours, sliding — reset on every authenticated request
- Flags: `HttpOnly`, `SameSite=Lax`, `Secure` when served over HTTPS

The `Secure` flag is not set over plain HTTP (the default loopback setup). If you
expose Dubdeck over HTTPS through a reverse proxy, the flag is set automatically.

### Session scope

Sessions are per-browser. Changing the password (via `/api/auth/password`) rotates
the signing secret, which invalidates all other sessions immediately.

### Public API paths

These routes are reachable without a session:
`/api/health`, `/api/auth`, `/api/setup`, `/api/login`, `/api/logout`.

All other `/api/*` paths require a valid session when auth is enabled.

---

## Disabling auth (loopback-only)

`auth.enabled=false` (set in the Settings window or via `PATCH /api/settings`) is
only permitted when the server is bound to a loopback address. The allowed loopback
values are `127.0.0.1`, `::1`, and `localhost`. Any other value — including
`0.0.0.0` — is rejected at startup.

This guard is enforced in two places:

1. At startup: `assert_auth_or_loopback(auth_enabled, DUBDECK_BIND)` aborts the
   process if the combination is unsafe.
2. At runtime: `PATCH /api/settings` with `{"auth.enabled": false}` returns HTTP
   400 if `DUBDECK_BIND` is not loopback.

The `DUBDECK_BIND` env var must match the address uvicorn actually binds to. The
default is `127.0.0.1`.

---

## Host-allowlist and Sec-Fetch-Site middleware

These two checks run before the auth layer and are always active, regardless of
the auth setting:

**Host-allowlist:** the server checks the `Host` header on every request against
`DUBDECK_ALLOWED_HOSTS` (default: `127.0.0.1,localhost,::1`). A DNS-rebinding attack
— a browser being tricked into sending requests to `127.0.0.1` with a foreign
`Host` header — is blocked here. If you reverse-proxy Dubdeck under a custom
hostname, add that hostname to `DUBDECK_ALLOWED_HOSTS`.

**Sec-Fetch-Site:** the primary CSRF control is the session cookie's
`SameSite=Lax` attribute, which keeps a cross-site page from sending the cookie
on a state-changing request. As defense-in-depth, modern browsers also send the
`Sec-Fetch-Site` header on every fetch; for API requests a value of `cross-site`
means the request originated from a page on a different origin, and those
requests are rejected with HTTP 403. (Non-browser clients may omit the header, so
this layer supplements — does not replace — the cookie's `SameSite` protection.)

Both checks are in a single middleware function to avoid the SSE-buffering problem
that arises from stacking two `BaseHTTPMiddleware` wrappers.

---

## SSH key scoping

Dubdeck needs an SSH key to reach remote hosts. Minimize blast radius:

- **Dedicated key pair.** Do not reuse a personal SSH key. Generate a key used
  only by Dubdeck, stored in the secrets directory (outside the repo, never
  committed).

- **Forced-command shim (optional, recommended for Parallels/Mac hosts).** Rather
  than giving the key free shell access, install a forced-command script on the
  host that only accepts the specific commands Dubdeck issues. See
  `docs/pro-tips/mac-parallels-hardening/` for a worked example.

- **Loopback-only sshd (Mac hosts).** If the Mac sshd is needed only for Dubdeck
  in a Docker environment, run it on `127.0.0.1:2222` (not all interfaces). The
  `ListenAddress 127.0.0.1` + custom port keeps the attack surface minimal. See
  the pro-tips hardening doc.

- **Known hosts pinning.** Mount a `known_hosts` file into the container that pins
  the fingerprint of each host. Without it, a host key change can silently succeed;
  with it, any mismatch aborts the connection.

---

## Binding wider than loopback

If you need to access Dubdeck from another machine (e.g. through a reverse proxy):

1. Enable auth (`auth.enabled: true` — the default).
2. Use a reverse proxy that terminates TLS, so the `Secure` cookie flag activates.
3. Set `DUBDECK_ALLOWED_HOSTS` to the hostname your reverse proxy presents.
4. Set `DUBDECK_BIND` to the address uvicorn binds to so the startup loopback
   check has accurate information.

Changing `ports:` in `compose.yaml` from `127.0.0.1:8400:8000` to `8400:8000`
exposes the port on all interfaces — **only do this with auth enabled and behind a
TLS-terminating proxy or inside a trusted private network**.

---

## No destructive operations

Dubdeck v1 intentionally has no delete, undefine, or snapshot-restore operations.
Every action in the API is reversible: start/stop/suspend are state changes;
snapshot_create adds a checkpoint; logs and stats are read-only. This is a design
boundary, not a gap — it means a compromised session cannot destroy lab state.

---

## Reporting a vulnerability

If you find a security issue in Dubdeck, please report it privately before
disclosing publicly:

**Email:** security@teamdub.com

Include a description of the issue, steps to reproduce, and the version/commit you
tested against. We aim to acknowledge reports within 48 hours and to release a fix
within 14 days for critical issues.

Please do not open a public GitHub issue for security vulnerabilities until a fix
is available.
