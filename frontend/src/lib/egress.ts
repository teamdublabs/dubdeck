// Pure egress-window math — kept out of components so vitest can cover it.

export interface EgressView {
  /** "m:ss" remaining, or "0:00" once past expiry */
  display: string
  remainingMs: number
  /** Past expiry but still present = the server-side revoke is being retried */
  overdue: boolean
}

export function egressView(expiresAtS: number, nowMs: number): EgressView {
  const remainingMs = expiresAtS * 1000 - nowMs
  const clamped = Math.max(0, remainingMs)
  const m = Math.floor(clamped / 60000)
  const s = Math.floor((clamped % 60000) / 1000)
  return {
    display: `${m}:${s.toString().padStart(2, "0")}`,
    remainingMs,
    // Small grace so a normal revoke in flight doesn't flash the alarm.
    overdue: remainingMs < -5000,
  }
}

/** The soonest-expiring active window, for the tray countdown. */
export function soonestExpiry(expiries: (number | null)[]): number | null {
  const active = expiries.filter((e): e is number => e !== null)
  return active.length ? Math.min(...active) : null
}
