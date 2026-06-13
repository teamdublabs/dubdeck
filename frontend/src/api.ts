export type VMState =
  | "running"
  | "stopped"
  | "suspended"
  | "paused"
  | "unknown"
  | "starting"
  | "stopping"
  | "suspending"
  | "snapshotting"

export interface HostInfo {
  reachable: boolean
  error: string | null
  stats: {
    load_1m: number; load_5m: number; load_15m: number
    mem_total: number; mem_used: number
    disk_total: number | null; disk_used: number | null
  } | null
}

export interface ProviderInfo {
  reachable: boolean
  error: string | null
}

export interface ResourceNode {
  ref: string
  provider: string
  id: string
  name: string
  kind: string
  state: VMState
  capabilities: string[]
  error: string | null
}

export interface EgressInfo {
  mode: "on-demand" | "permanent"
  internet: boolean | null
  expires_at: number | null
}

export interface GroupStatus {
  label: string
  resources: ResourceNode[]
  error?: string | null
}

export interface StatusSnapshot {
  generated_at: number
  hosts: Record<string, HostInfo>
  providers: Record<string, ProviderInfo>
  groups: Record<string, GroupStatus>
  modules: {
    egress?: Record<string, EgressInfo>
  }
}

export interface LogEntry {
  id: number
  ts: number
  action: string
  target: string
  ok: boolean
  detail: string
}

export interface SnapshotInfo {
  name: string
  created: string
  current: boolean
}

export interface ResourceStats {
  generated_at: number
  resources: Record<string, { disk_bytes: number }>
}

export interface LogQuery {
  limit?: number
  before_id?: number
  action?: string
  failures?: boolean
}

export interface AuthStatus {
  enabled: boolean
  configured: boolean
  authenticated: boolean
  brand: string
}

/** App settings (settings service). Open-keyed: known scalars below, plus
 *  dynamic `modules.<name>.enabled` toggles. */
export interface Settings {
  "auth.enabled"?: boolean
  "ui.branding.name"?: string
  bind?: string
  [key: string]: unknown
}

/** Just the slice of /api/config the Settings UI needs: which modules exist. */
export interface ConfigInfo {
  modules: Record<string, unknown>
}

/** Fired when a gated call returns 401 — a session expired mid-use. AuthGate
 *  listens and drops back to the login screen. Not fired for the auth routes
 *  themselves (login/setup own their own error display). A plain EventTarget
 *  (not window) keeps this usable from node — the unit tests have no DOM. */
export const AUTH_REQUIRED_EVENT = "auth-required"
export const authEvents = new EventTarget()

const AUTH_PATHS = ["/auth", "/login", "/setup", "/logout"]

async function req<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`/api${path}`, {
    ...init,
    headers: { "Content-Type": "application/json", ...(init?.headers ?? {}) },
  })
  if (!res.ok) {
    if (res.status === 401 && !AUTH_PATHS.some((p) => path.startsWith(p))) {
      authEvents.dispatchEvent(new Event(AUTH_REQUIRED_EVENT))
    }
    const body = await res.json().catch(() => ({ detail: res.statusText }))
    throw new Error(body.detail ?? `HTTP ${res.status}`)
  }
  return res.json()
}

export function logParams(q: LogQuery): string {
  const params = new URLSearchParams()
  if (q.limit) params.set("limit", String(q.limit))
  if (q.before_id) params.set("before_id", String(q.before_id))
  if (q.action) params.set("action", q.action)
  if (q.failures) params.set("failures", "true")
  const s = params.toString()
  return s ? `?${s}` : ""
}

/** Split ref at the first "/" and encode each segment, rejoining with "/". */
function resourcePath(ref: string, suffix: string): string {
  const slash = ref.indexOf("/")
  const provider = encodeURIComponent(ref.slice(0, slash))
  const rid = encodeURIComponent(ref.slice(slash + 1))
  return `/resources/${provider}/${rid}/${suffix}`
}

export const api = {
  status: () => req<StatusSnapshot>("/status"),
  log: (q: LogQuery = {}) => req<LogEntry[]>(`/log${logParams(q)}`),
  startResource: (ref: string) => req(resourcePath(ref, "start"), { method: "POST" }),
  stopResource: (ref: string) => req(resourcePath(ref, "stop"), { method: "POST" }),
  suspendResource: (ref: string) => req(resourcePath(ref, "suspend"), { method: "POST" }),
  restartResource: (ref: string) => req(resourcePath(ref, "restart"), { method: "POST" }),
  // Plain-text log tail for LOGS-capable resources. Backend route lands in
  // Phase 5 (Docker); the viewer is built against it now so it just plugs in.
  resourceLogs: (ref: string, n = 200): Promise<string> => {
    const slash = ref.indexOf("/")
    const provider = encodeURIComponent(ref.slice(0, slash))
    const rid = encodeURIComponent(ref.slice(slash + 1))
    return fetch(`/api/resources/${provider}/${rid}/logs?n=${n}`).then(async (res) => {
      if (!res.ok) {
        if (res.status === 401) authEvents.dispatchEvent(new Event(AUTH_REQUIRED_EVENT))
        throw new Error((await res.text()) || `HTTP ${res.status}`)
      }
      return res.text()
    })
  },
  snapshots: (ref: string) => req<SnapshotInfo[]>(resourcePath(ref, "snapshots")),
  createSnapshot: (ref: string, name?: string) =>
    req<{ name: string }>(resourcePath(ref, "snapshots"), { method: "POST", body: JSON.stringify(name ? { name } : {}) }),
  resourcestats: () => req<ResourceStats>("/resourcestats"),
  startGroup: (name: string) =>
    req(`/groups/${encodeURIComponent(name)}/start`, { method: "POST" }),
  stopGroup: (name: string) =>
    req(`/groups/${encodeURIComponent(name)}/stop`, { method: "POST" }),
  enableEgress: (name: string, duration_s: number) =>
    req(`/groups/${encodeURIComponent(name)}/egress`, { method: "POST", body: JSON.stringify({ duration_s }) }),
  extendEgress: (name: string, duration_s: number) =>
    req(`/groups/${encodeURIComponent(name)}/egress/extend`, { method: "POST", body: JSON.stringify({ duration_s }) }),
  revokeEgress: (name: string) =>
    req(`/groups/${encodeURIComponent(name)}/egress`, { method: "DELETE" }),
  authStatus: () => req<AuthStatus>("/auth"),
  setup: (password: string) =>
    req<{ status: string }>("/setup", { method: "POST", body: JSON.stringify({ password }) }),
  login: (password: string) =>
    req<{ status: string }>("/login", { method: "POST", body: JSON.stringify({ password }) }),
  logout: () => req<{ status: string }>("/logout", { method: "POST" }),
  changePassword: (current: string, next: string) =>
    req<{ status: string }>("/auth/password", {
      method: "POST",
      body: JSON.stringify({ current, new: next }),
    }),
  config: () => req<ConfigInfo>("/config"),
  settings: () => req<Settings>("/settings"),
  patchSettings: (updates: Settings) =>
    req<Settings>("/settings", { method: "PATCH", body: JSON.stringify(updates) }),
  dismissError: (target: string) => {
    const slash = target.indexOf("/")
    if (slash === -1) {
      // group-level ref like "group:lab-01" — no slash, encode whole thing
      return req(`/errors/${encodeURIComponent(target)}`, { method: "DELETE" })
    }
    const provider = encodeURIComponent(target.slice(0, slash))
    const rid = encodeURIComponent(target.slice(slash + 1))
    return req(`/errors/${provider}/${rid}`, { method: "DELETE" })
  },
}
