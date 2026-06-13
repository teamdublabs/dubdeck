import type { StatusSnapshot } from "../api"

/** No hosts, providers, or groups — the fresh-install case (missing/blank
 *  config). The desktop shows the onboarding screen instead of an empty void. */
export function configEmpty(s: StatusSnapshot): boolean {
  return (
    Object.keys(s.hosts).length === 0 &&
    Object.keys(s.providers).length === 0 &&
    Object.keys(s.groups).length === 0
  )
}

export interface Diagnostic {
  name: string
  kind: "host" | "provider"
  error: string
}

/** Hosts and providers currently unreachable, with the backend's error text
 *  (SSH stderr etc.) — the actionable detail for a per-host diagnostic. */
export function unreachable(s: StatusSnapshot): Diagnostic[] {
  const out: Diagnostic[] = []
  for (const [name, h] of Object.entries(s.hosts)) {
    if (!h.reachable) out.push({ name, kind: "host", error: h.error ?? "unreachable" })
  }
  for (const [name, p] of Object.entries(s.providers)) {
    if (!p.reachable) out.push({ name, kind: "provider", error: p.error ?? "unreachable" })
  }
  return out
}

/** Config exists but every provider is down — nothing is manageable, so a
 *  prominent diagnostic is warranted (per-resource errors already surface in
 *  group windows when only some providers are down). */
export function allProvidersDown(s: StatusSnapshot): boolean {
  const providers = Object.entries(s.providers)
  return providers.length > 0 && providers.every(([, p]) => !p.reachable)
}
