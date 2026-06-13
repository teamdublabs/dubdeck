import { type ReactNode, useCallback, useEffect, useState } from "react"
import { AUTH_REQUIRED_EVENT, api, type AuthStatus, authEvents } from "./api"
import { AuthScreen } from "./auth/AuthScreen"

/** Wraps the desktop. Until a session exists (or auth is disabled), the desktop
 *  never renders — only the setup/login screen does. A mid-session 401 drops
 *  straight back here via AUTH_REQUIRED_EVENT. */
export function AuthGate({ children }: { children: ReactNode }) {
  const [status, setStatus] = useState<AuthStatus | null>(null)

  const check = useCallback(async () => {
    try {
      setStatus(await api.authStatus())
    } catch {
      // Backend unreachable — show a locked screen, never a blank desktop.
      setStatus({ enabled: true, configured: true, authenticated: false, brand: "Dubdeck" })
    }
  }, [])

  useEffect(() => {
    // Initial auth probe — async, so this isn't a synchronous setState-in-effect.
    // eslint-disable-next-line react-hooks/set-state-in-effect
    check()
    const onExpired = () => setStatus((s) => (s ? { ...s, authenticated: false } : s))
    authEvents.addEventListener(AUTH_REQUIRED_EVENT, onExpired)
    return () => authEvents.removeEventListener(AUTH_REQUIRED_EVENT, onExpired)
  }, [check])

  if (status === null) {
    return <div className="grid h-full w-full place-items-center font-mono text-lab-dim">…</div>
  }
  if (!status.enabled || status.authenticated) return <>{children}</>
  return (
    <AuthScreen mode={status.configured ? "login" : "setup"} brand={status.brand} onAuthed={check} />
  )
}
