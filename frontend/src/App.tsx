import { AnimatePresence } from "framer-motion"
import { useCallback, useEffect, useMemo, useState } from "react"
import { AUTH_REQUIRED_EVENT, api, authEvents } from "./api"
import { GroupApp } from "./apps/GroupApp"
import { LabMonitor } from "./apps/LabMonitor"
import { LogsApp } from "./apps/LogsApp"
import { OpsLogApp } from "./apps/OpsLogApp"
import { SettingsApp } from "./apps/SettingsApp"
import { Taskbar, type AppDef } from "./shell/Taskbar"
import { Backgrounds } from "./shell/backgrounds/Backgrounds"
import { DEFAULT_THEME, SHADERS } from "./shell/backgrounds/glsl"
import { BackgroundPicker } from "./shell/BackgroundPicker"
import { Window } from "./shell/Window"
import { StatusDot } from "./shell/StatusDot"
import { Onboarding, HostDiagnostics } from "./shell/Onboarding"
import { allProvidersDown, configEmpty } from "./lib/health"
import { useStatus } from "./useStatus"

interface WinState {
  id: string
  z: number
  minimized: boolean
  maximized: boolean
  position: { x: number; y: number }
  size: { w: number; h: number }
  prev?: { position: { x: number; y: number }; size: { w: number; h: number } }
}

const SIZES: Record<string, { w: number; h: number }> = {
  monitor: { w: 420, h: 440 },
  log: { w: 520, h: 360 },
  settings: { w: 360, h: 470 },
  _group: { w: 380, h: 440 },
  _logs: { w: 560, h: 420 },
}

function sizeFor(id: string): { w: number; h: number } {
  if (id.startsWith("group:")) return SIZES._group
  if (id.startsWith("logs:")) return SIZES._logs
  return SIZES[id]
}

interface Session {
  windows: Record<string, WinState>
  order: string[]
  topZ: number
}

function loadSession(): Session {
  try {
    const raw = localStorage.getItem("dubdeck.session")
    if (raw) {
      const s = JSON.parse(raw) as Session
      if (s && typeof s === "object" && Array.isArray(s.order)) return s
    }
  } catch { /* corrupted session — start fresh */ }
  return { windows: {}, order: [], topZ: 10 }
}

export default function App() {
  const { snapshot, error, refresh } = useStatus()
  const [session] = useState(loadSession)
  const [windows, setWindows] = useState<Record<string, WinState>>(session.windows)
  const [order, setOrder] = useState<string[]>(session.order)
  const [topZ, setTopZ] = useState(session.topZ)
  const [startOpen, setStartOpen] = useState(false)
  const [diagDismissed, setDiagDismissed] = useState(false)
  const [brand, setBrand] = useState("Dubdeck")
  // Friendly names for open logs windows (id "logs:<ref>" -> resource name).
  const [logNames, setLogNames] = useState<Record<string, string>>({})
  const [theme, setThemeState] = useState(() => {
    const stored = localStorage.getItem("dubdeck.theme")
    return stored && stored in SHADERS ? stored : DEFAULT_THEME  // ignore stale/removed ids
  })
  const setTheme = (id: string) => { setThemeState(id); localStorage.setItem("dubdeck.theme", id) }

  useEffect(() => {
    // The desktop survives reloads — same windows, same spots.
    localStorage.setItem("dubdeck.session", JSON.stringify({ windows, order, topZ }))
  }, [windows, order, topZ])

  // Once a snapshot is in hand, drop windows for groups that no longer exist
  // (config edited, an enclave retired) — otherwise a stale persisted session
  // leaves a ghost window stuck on "Loading…". This adjusts state during render
  // (React's "store info from previous renders" pattern) rather than in an
  // effect, so there's no extra commit/flash; it self-stabilizes once trimmed.
  if (snapshot) {
    const dead = order.filter((id) => id.startsWith("group:") && !(id.slice(6) in snapshot.groups))
    if (dead.length) {
      setOrder((o) => o.filter((id) => !dead.includes(id)))
      setWindows((w) => {
        const next = { ...w }
        for (const id of dead) delete next[id]
        return next
      })
    }
  }

  useEffect(() => {
    // Brand name powers the start button / menu; live-updated from Settings.
    api.settings().then((s) => {
      const n = s["ui.branding.name"]
      if (typeof n === "string" && n) setBrand(n)
    }).catch(() => {})
  }, [])

  useEffect(() => {
    // Brand drives the browser tab / favicon label too, live as Settings change.
    document.title = brand
  }, [brand])

  const logout = useCallback(async () => {
    await api.logout().catch(() => {})
    authEvents.dispatchEvent(new Event(AUTH_REQUIRED_EVENT))
  }, [])

  const apps: AppDef[] = useMemo(() => {
    const groupApps = snapshot
      ? Object.entries(snapshot.groups).map(([id, g]) => ({ id: `group:${id}`, title: g.label, icon: "▣" }))
      : []
    return [
      ...groupApps,
      { id: "monitor", title: "Lab Monitor", icon: "📊" },
      { id: "log", title: "Ops Log", icon: "📜" },
      { id: "settings", title: "Settings", icon: "⚙" },
    ]
  }, [snapshot])

  const launch = useCallback((id: string) => {
    setTopZ((z) => z + 1)
    setWindows((w) => {
      const existing = w[id]
      const z = topZ + 1
      if (existing) return { ...w, [id]: { ...existing, minimized: false, z } }
      const size = sizeFor(id)
      const n = order.length
      return {
        ...w,
        [id]: { id, z, minimized: false, maximized: false, position: { x: 120 + n * 36, y: 50 + n * 28 }, size },
      }
    })
    setOrder((o) => (o.includes(id) ? o : [...o, id]))
  }, [order.length, topZ])

  const openLogs = useCallback((ref: string, name: string) => {
    setLogNames((m) => (m[ref] === name ? m : { ...m, [ref]: name }))
    launch(`logs:${ref}`)
  }, [launch])

  const focus = (id: string) => { setTopZ((z) => z + 1); setWindows((w) => ({ ...w, [id]: { ...w[id], z: topZ + 1, minimized: false } })) }
  const close = (id: string) => { setWindows((w) => { const n = { ...w }; delete n[id]; return n }); setOrder((o) => o.filter((x) => x !== id)) }
  const minimize = (id: string) => setWindows((w) => ({ ...w, [id]: { ...w[id], minimized: true } }))
  const move = (id: string, position: { x: number; y: number }) => setWindows((w) => ({ ...w, [id]: { ...w[id], position } }))
  const resize = (id: string, size: { w: number; h: number }) => setWindows((w) => ({ ...w, [id]: { ...w[id], size } }))
  const maximize = (id: string) =>
    setWindows((w) => {
      const win = w[id]
      if (win.maximized && win.prev) {
        return { ...w, [id]: { ...win, maximized: false, ...win.prev, prev: undefined } }
      }
      return {
        ...w,
        [id]: {
          ...win,
          maximized: true,
          prev: { position: win.position, size: win.size },
          position: { x: 8, y: 8 },
          size: { w: window.innerWidth - 16, h: window.innerHeight - 64 },
        },
      }
    })

  const focusedId = useMemo(() => {
    let top: string | null = null
    let max = -1
    for (const id of order) {
      const w = windows[id]
      if (w && !w.minimized && w.z > max) { max = w.z; top = id }
    }
    return top
  }, [order, windows])

  const renderApp = (id: string) => {
    if (id === "monitor") return <LabMonitor snapshot={snapshot} />
    if (id === "log") return <OpsLogApp />
    if (id === "settings") return <SettingsApp onBrandChange={setBrand} />
    if (id.startsWith("group:")) {
      const key = id.slice("group:".length)
      const group = snapshot?.groups[key]
      const egress = snapshot?.modules.egress?.[key]
      if (group) return <GroupApp name={key} group={group} egress={egress} onChange={refresh} onOpenLogs={openLogs} />
      return <div className="p-4 text-lab-dim">{snapshot ? "This group is no longer in the config." : "Loading…"}</div>
    }
    if (id.startsWith("logs:")) {
      const ref = id.slice("logs:".length)
      return <LogsApp resourceRef={ref} name={logNames[ref] ?? ref} />
    }
    return null
  }
  const titleOf = (id: string) => {
    if (id.startsWith("logs:")) return `Logs · ${logNames[id.slice(5)] ?? id.slice(5)}`
    return apps.find((a) => a.id === id)?.title ?? id
  }
  const iconOf = (id: string) => {
    if (id.startsWith("logs:")) return "▤"
    return apps.find((a) => a.id === id)?.icon ?? "▣"
  }

  return (
    <div className="relative h-full w-full select-none" onMouseDown={() => startOpen && setStartOpen(false)}>
      <Backgrounds theme={theme} />

      {/* Configurable brand wordmark on the wallpaper — faint, behind everything. */}
      <div className="pointer-events-none absolute inset-0 z-0 flex items-center justify-center overflow-hidden">
        <span className="select-none whitespace-nowrap font-mono text-[14vw] font-bold uppercase tracking-[0.2em] text-white/[0.03]">
          {brand}
        </span>
      </div>

      <div className="absolute left-5 top-5 flex flex-col gap-4">
        {apps.map((a) => (
          <button key={a.id} onDoubleClick={() => launch(a.id)} onClick={() => launch(a.id)}
            className="group flex w-20 flex-col items-center gap-1.5">
            <span
              className="grid h-12 w-12 place-items-center rounded-xl text-2xl transition-all duration-200 group-hover:-translate-y-0.5"
              style={{
                background: "linear-gradient(160deg, rgba(56,232,255,0.14), rgba(11,18,30,0.5))",
                border: "1px solid rgba(120,200,255,0.18)",
                boxShadow: "inset 0 1px 0 rgba(255,255,255,0.08), 0 8px 20px -10px rgba(0,0,0,0.7)",
              }}
            >
              <span className="drop-shadow-[0_0_10px_rgba(56,232,255,0.5)] transition group-hover:drop-shadow-[0_0_16px_rgba(56,232,255,0.85)]">{a.icon}</span>
            </span>
            <span className="text-center font-mono text-[10px] leading-tight text-white/75 transition group-hover:text-lab-cyan">{a.title}</span>
          </button>
        ))}
      </div>

      {snapshot && (configEmpty(snapshot) || (allProvidersDown(snapshot) && !diagDismissed)) && (
        <div className="pointer-events-none absolute inset-0 z-[40] flex items-center justify-center p-6">
          {configEmpty(snapshot)
            ? <Onboarding brand={brand} />
            : <HostDiagnostics snapshot={snapshot} onDismiss={() => setDiagDismissed(true)} />}
        </div>
      )}

      <div className="absolute right-4 top-4 z-[9000] flex items-center gap-3">
        {snapshot && (
          <div className="flex items-center gap-2 font-mono text-[11px] text-lab-dim">
            {Object.entries(snapshot.hosts).map(([n, h]) => (
              <span key={n} className="flex items-center gap-1"><StatusDot state={h.reachable ? "running" : "unknown"} /> {n}</span>
            ))}
          </div>
        )}
        {error && (
          <div className="glass rounded-lg px-3 py-1.5 font-mono text-[11px] text-lab-red">backend: {error}</div>
        )}
        <BackgroundPicker theme={theme} setTheme={setTheme} />
      </div>

      <AnimatePresence>
        {order.map((id) => {
          const win = windows[id]
          if (!win) return null
          return (
            <Window key={id} id={id} title={titleOf(id)} icon={iconOf(id)} z={win.z}
              minimized={win.minimized} maximized={win.maximized} focused={id === focusedId}
              position={win.position} size={win.size}
              onFocus={() => focus(id)} onClose={() => close(id)} onMinimize={() => minimize(id)}
              onMaximize={() => maximize(id)} onMove={(p) => move(id, p)} onResize={(s) => resize(id, s)}>
              {renderApp(id)}
            </Window>
          )
        })}
      </AnimatePresence>

      <Taskbar apps={apps} open={order} minimized={order.filter((id) => windows[id]?.minimized)}
        snapshot={snapshot} onLaunch={launch} startOpen={startOpen} setStartOpen={setStartOpen}
        brand={brand} onLogout={logout} titleOf={titleOf} iconOf={iconOf} />
    </div>
  )
}
