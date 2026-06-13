import { useEffect, useState } from "react"
import type { StatusSnapshot } from "../api"
import { egressView, soonestExpiry } from "../lib/egress"

export interface AppDef { id: string; title: string; icon: string }

function Clock() {
  const [now, setNow] = useState(new Date())
  useEffect(() => {
    const id = setInterval(() => setNow(new Date()), 1000)
    return () => clearInterval(id)
  }, [])
  return (
    <div className="text-right font-mono leading-tight">
      <div className="text-xs text-white">{now.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" })}</div>
      <div className="text-[10px] text-lab-dim">{now.toLocaleDateString([], { weekday: "short", month: "short", day: "numeric" })}</div>
    </div>
  )
}

function Tray({ snapshot }: { snapshot: StatusSnapshot | null }) {
  const [now, setNow] = useState(() => Date.now())
  useEffect(() => {
    const id = setInterval(() => setNow(Date.now()), 1000)
    return () => clearInterval(id)
  }, [])

  const egressEntries = snapshot?.modules.egress ? Object.entries(snapshot.modules.egress) : []
  const expiries = egressEntries
    .filter(([, e]) => e.mode !== "permanent")
    .map(([, e]) => e.expires_at)
  const soonest = soonestExpiry(expiries)
  const view = soonest !== null ? egressView(soonest, now) : null
  const overdue = expiries.some((e) => e !== null && egressView(e, now).overdue)

  return (
    <div className="flex items-center gap-3">
      <div className="flex items-center gap-1.5" title="Enclave internet status">
        {egressEntries.map(([label, e]) => (
          <span key={label} title={`${label}: ${e.internet ? "online" : "dark"}`}
            className={`h-2 w-2 rounded-full ${e.internet ? "bg-lab-green shadow-[0_0_6px_#00ff95]" : "bg-lab-dim"}`} />
        ))}
      </div>
      {overdue ? (
        <span className="animate-pulse rounded bg-lab-red/25 px-1.5 py-0.5 font-mono text-[10px] font-semibold text-lab-red"
          title="An egress revoke is past its window — the server is retrying">
          ⚠ REVOKE OVERDUE
        </span>
      ) : view ? (
        <span className="rounded bg-lab-amber/15 px-1.5 py-0.5 font-mono text-[10px] text-lab-amber"
          title="Soonest egress window expiry">
          ⏱ {view.display}
        </span>
      ) : null}
    </div>
  )
}

export function Taskbar({ apps, open, minimized, snapshot, onLaunch, startOpen, setStartOpen, brand, onLogout, titleOf, iconOf }: {
  apps: AppDef[]
  open: string[]
  minimized: string[]
  snapshot: StatusSnapshot | null
  onLaunch: (id: string) => void
  startOpen: boolean
  setStartOpen: (v: boolean) => void
  brand: string
  onLogout: () => void
  // Resolve title/icon for any open window id, including transient ones (logs)
  // that aren't in the app launcher list.
  titleOf: (id: string) => string
  iconOf: (id: string) => string
}) {
  return (
    <>
      {startOpen && (
        // stop mousedown here: the desktop root closes the menu on mousedown,
        // which would unmount these buttons before their click can fire
        <div className="glass absolute bottom-14 left-3 z-[9999] w-64 rounded-xl p-2" onMouseDown={(e) => e.stopPropagation()}>
          <div className="px-2 py-1 font-mono text-[10px] uppercase tracking-widest text-lab-dim">{brand} · Apps</div>
          {apps.map((a) => (
            <button key={a.id} onClick={() => { onLaunch(a.id); setStartOpen(false) }}
              className="flex w-full items-center gap-2 rounded-lg px-2 py-2 text-left text-sm text-white/90 hover:bg-lab-cyan/15">
              <span>{a.icon}</span> {a.title}
            </button>
          ))}
          <div className="my-1 border-t border-white/10" />
          <button onClick={() => { setStartOpen(false); onLogout() }}
            className="flex w-full items-center gap-2 rounded-lg px-2 py-2 text-left text-sm text-lab-red/90 hover:bg-lab-red/15">
            <span>⏻</span> Log out
          </button>
        </div>
      )}
      <div className="glass absolute inset-x-0 bottom-0 z-[9998] flex h-12 items-center gap-2 rounded-none border-x-0 border-b-0 px-3">
        <button onClick={() => setStartOpen(!startOpen)} onMouseDown={(e) => e.stopPropagation()}
          className="flex items-center gap-1.5 rounded-lg px-3 py-1.5 font-mono text-xs font-semibold text-lab-cyan transition neon-text hover:brightness-125"
          style={{
            background: "linear-gradient(160deg, rgba(56,232,255,0.22), rgba(56,232,255,0.08))",
            border: "1px solid rgba(56,232,255,0.3)",
            boxShadow: "0 0 16px -4px rgba(56,232,255,0.5), inset 0 1px 0 rgba(255,255,255,0.1)",
          }}>
          ◈ {brand}
        </button>
        <div className="flex items-center gap-1">
          {open.map((id) => (
            <button key={id} onClick={() => onLaunch(id)}
              className={`flex items-center gap-1.5 rounded-lg px-2.5 py-1.5 text-xs transition ${
                minimized.includes(id) ? "bg-white/5 text-lab-dim" : "bg-lab-cyan/10 text-white"
              } hover:bg-lab-cyan/20`}>
              <span>{iconOf(id)}</span>
              <span className="font-mono">{titleOf(id)}</span>
            </button>
          ))}
        </div>
        <div className="ml-auto flex items-center gap-4">
          <Tray snapshot={snapshot} />
          <span className="font-mono text-[10px] text-lab-dim select-none" title="Dubdeck version">
            v{__APP_VERSION__}
          </span>
          <Clock />
        </div>
      </div>
    </>
  )
}
