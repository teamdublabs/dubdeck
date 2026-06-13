import { useCallback, useEffect, useRef, useState } from "react"
import { api } from "../api"

// Log tail viewer for LOGS-capable resources. The backend endpoint lands in
// Phase 5 (Docker); until then a fetch here surfaces the backend's error in the
// body, so wiring Phase 5 in is a no-op on this side.
const LINE_CHOICES = [200, 500, 1000]

export function LogsApp({ resourceRef, name }: { resourceRef: string; name: string }) {
  const [text, setText] = useState<string | null>(null)
  const [err, setErr] = useState<string | null>(null)
  const [n, setN] = useState(200)
  const [loading, setLoading] = useState(true)
  const [follow, setFollow] = useState(true)
  const [reloads, setReloads] = useState(0)
  const bodyRef = useRef<HTMLPreElement>(null)

  const reload = useCallback(() => { setLoading(true); setReloads((r) => r + 1) }, [])

  useEffect(() => {
    // Fetch on mount and whenever the ref / line-count / reload key changes. All
    // state updates happen AFTER the await, never synchronously in the effect.
    let alive = true
    api.resourceLogs(resourceRef, n)
      .then((out) => { if (alive) { setText(out); setErr(null) } })
      .catch((e) => { if (alive) setErr(e instanceof Error ? e.message : String(e)) })
      .finally(() => { if (alive) setLoading(false) })
    return () => { alive = false }
  }, [resourceRef, n, reloads])

  useEffect(() => {
    // Tail behaviour: pin to the bottom on new output unless the user opts out.
    if (follow && bodyRef.current) bodyRef.current.scrollTop = bodyRef.current.scrollHeight
  }, [text, follow])

  return (
    <div className="flex h-full flex-col">
      <div className="flex items-center gap-2 border-b border-white/10 px-3 py-2">
        <span className="font-mono text-xs text-white/90">{name}</span>
        <span className="font-mono text-[10px] uppercase tracking-wide text-lab-dim">logs</span>
        <div className="ml-auto flex items-center gap-2">
          <select
            value={n}
            onChange={(e) => { setLoading(true); setN(Number(e.target.value)) }}
            className="rounded border border-white/10 bg-black/30 px-1.5 py-0.5 font-mono text-[11px] text-white/80 outline-none"
          >
            {LINE_CHOICES.map((c) => <option key={c} value={c}>{c} lines</option>)}
          </select>
          <label className="flex items-center gap-1 font-mono text-[10px] text-lab-dim" title="Pin to newest output">
            <input type="checkbox" checked={follow} onChange={(e) => setFollow(e.target.checked)} /> follow
          </label>
          <button
            onClick={reload}
            disabled={loading}
            title="Refresh"
            className="rounded-md bg-lab-cyan/12 px-2 py-0.5 font-mono text-[11px] text-lab-cyan hover:bg-lab-cyan/22 disabled:opacity-40"
          >
            {loading ? "…" : "↻"}
          </button>
        </div>
      </div>
      <pre
        ref={bodyRef}
        className="min-h-0 flex-1 overflow-auto bg-black/40 p-3 font-mono text-[11px] leading-relaxed text-white/85"
      >
        {err ? (
          <span className="text-lab-red">{err}</span>
        ) : text === null ? (
          <span className="text-lab-dim">Loading…</span>
        ) : text.trim() === "" ? (
          <span className="text-lab-dim">No log output.</span>
        ) : (
          text
        )}
      </pre>
    </div>
  )
}
