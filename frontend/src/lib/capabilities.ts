// Maps a provider's declared capability set + resource kind to what the UI
// renders. A row shows ONLY the actions its provider declares — a container
// (start/stop/restart/logs) and a VM (start/stop/suspend/snapshot) diverge
// entirely from this table, with no VM assumptions baked into the row.

const KIND_ICON: Record<string, string> = {
  vm: "🖥",
  container: "📦",
  stack: "🗄",
}

const KIND_LABEL: Record<string, string> = {
  vm: "VM",
  container: "Container",
  stack: "Stack",
}

export function kindIcon(kind: string): string {
  return KIND_ICON[kind] ?? "▦"
}

export function kindLabel(kind: string): string {
  return KIND_LABEL[kind] ?? kind
}

export interface RowActions {
  canStart: boolean
  canStop: boolean
  canSuspend: boolean
  canRestart: boolean
  canSnapshot: boolean
  canLogs: boolean
}

/** Derive the button set from a resource's declared capabilities. */
export function rowActions(capabilities: string[]): RowActions {
  const has = (c: string) => capabilities.includes(c)
  return {
    canStart: has("start"),
    canStop: has("stop"),
    canSuspend: has("suspend"),
    canRestart: has("restart"),
    canSnapshot: has("snapshot_list"),
    canLogs: has("logs"),
  }
}
