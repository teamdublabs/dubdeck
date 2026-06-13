import { useCallback, useEffect, useState } from "react"
import { api, type LogEntry } from "../api"

const PAGE = 100

const ACTION_FILTERS = [
  { key: "", label: "all" },
  { key: "vm.", label: "vm ops" },
  { key: "egress.", label: "egress" },
] as const

function dayOf(ts: number): string {
  return new Date(ts * 1000).toLocaleDateString([], { weekday: "short", month: "short", day: "numeric" })
}

export function OpsLogApp() {
  const [entries, setEntries] = useState<LogEntry[]>([])
  const [action, setAction] = useState("")
  const [failuresOnly, setFailuresOnly] = useState(false)
  const [exhausted, setExhausted] = useState(false)

  const query = useCallback(
    (before_id?: number) =>
      api.log({ limit: PAGE, before_id, action: action || undefined, failures: failuresOnly || undefined }),
    [action, failuresOnly],
  )

  useEffect(() => {
    let live = true
    const load = () => query().then((es) => { if (live) { setEntries(es); setExhausted(es.length < PAGE) } }).catch(() => {})
    load()
    const id = setInterval(load, 4000)
    return () => { live = false; clearInterval(id) }
  }, [query])

  const loadOlder = async () => {
    const oldest = entries[entries.length - 1]
    if (!oldest) return
    const older = await query(oldest.id).catch(() => [])
    setEntries((cur) => [...cur, ...older.filter((o) => !cur.some((c) => c.id === o.id))])
    if (older.length < PAGE) setExhausted(true)
  }

  // Precompute day dividers — first entry of each day gets one.
  const dividers = entries.map(
    (e, i) => i === 0 || dayOf(e.ts) !== dayOf(entries[i - 1].ts),
  )
  return (
    <div className="flex h-full flex-col font-mono text-[11px]">
      <div className="flex items-center gap-1.5 border-b border-white/10 px-2 py-1.5">
        {ACTION_FILTERS.map((f) => (
          <button key={f.key} onClick={() => setAction(f.key)}
            className={`rounded px-2 py-0.5 text-[10px] transition ${
              action === f.key ? "bg-lab-cyan/20 text-lab-cyan" : "text-lab-dim hover:bg-white/5"
            }`}>{f.label}</button>
        ))}
        <button onClick={() => setFailuresOnly(!failuresOnly)}
          className={`ml-auto rounded px-2 py-0.5 text-[10px] transition ${
            failuresOnly ? "bg-lab-red/20 text-lab-red" : "text-lab-dim hover:bg-white/5"
          }`}>✗ failures only</button>
      </div>
      <div className="min-h-0 flex-1 overflow-auto p-2">
        {entries.length === 0 && <div className="p-4 text-lab-dim">Nothing matches.</div>}
        {entries.map((e, i) => {
          return (
            <div key={e.id}>
              {dividers[i] && (
                <div className="px-2 pb-1 pt-2 text-[9px] uppercase tracking-widest text-lab-dim/70">{dayOf(e.ts)}</div>
              )}
              <div className="flex items-baseline gap-2 border-b border-white/5 px-2 py-1.5">
                <span className="text-lab-dim">{new Date(e.ts * 1000).toLocaleTimeString()}</span>
                <span className={e.ok ? "text-lab-green" : "text-lab-red"}>{e.ok ? "✓" : "✗"}</span>
                <span className="text-lab-cyan">{e.action}</span>
                <span className="text-white/90">{e.target}</span>
                {e.detail && <span className="truncate text-lab-dim">{e.detail}</span>}
              </div>
            </div>
          )
        })}
        {!exhausted && entries.length > 0 && (
          <button onClick={loadOlder}
            className="mt-2 w-full rounded-md bg-white/5 py-1.5 text-[10px] text-lab-dim hover:bg-white/10">
            Load older entries
          </button>
        )}
      </div>
    </div>
  )
}
