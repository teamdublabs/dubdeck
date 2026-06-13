import { useEffect, useState } from "react"
import { api, type EgressInfo, type GroupStatus, type ResourceNode, type SnapshotInfo } from "../api"
import { egressView } from "../lib/egress"
import { kindIcon, kindLabel, rowActions } from "../lib/capabilities"
import { StatusDot } from "../shell/StatusDot"

const TRANSITIONAL = ["starting", "stopping", "suspending", "snapshotting"]
const PHASE_LABEL: Record<string, string> = {
  starting: "Starting…", stopping: "Stopping…", suspending: "Suspending…", snapshotting: "Snapshot…",
}

function useNow(intervalMs = 1000) {
  const [now, setNow] = useState(() => Date.now())
  useEffect(() => {
    const id = window.setInterval(() => setNow(Date.now()), intervalMs)
    return () => window.clearInterval(id)
  }, [intervalMs])
  return now
}

function Countdown({ expiresAt }: { expiresAt: number }) {
  const now = useNow()
  return <span className="font-mono text-lab-amber">{egressView(expiresAt, now).display}</span>
}

function DismissableError({ target, message, onDismiss }: {
  target: string; message: string; onDismiss: () => void
}) {
  return (
    <div className="flex items-baseline gap-2 rounded-md bg-lab-red/15 px-2 py-1 font-mono text-[10px] text-lab-red">
      <span className="min-w-0 flex-1 break-words">{message}</span>
      <button
        title={`Dismiss error for ${target}`}
        className="shrink-0 rounded px-1 text-lab-red/70 hover:bg-lab-red/20 hover:text-lab-red"
        onClick={() => { api.dismissError(target).catch(() => {}); onDismiss() }}
      >✕</button>
    </div>
  )
}

function SnapshotPanel({ resourceRef }: { resourceRef: string }) {
  const [snaps, setSnaps] = useState<SnapshotInfo[] | null>(null)
  const [err, setErr] = useState<string | null>(null)
  const [taking, setTaking] = useState(false)

  const load = () => api.snapshots(resourceRef).then(setSnaps).catch((e) => setErr(String(e.message ?? e)))
  useEffect(() => { load() }, [resourceRef])  // eslint-disable-line react-hooks/exhaustive-deps

  const take = async () => {
    setTaking(true); setErr(null)
    try { await api.createSnapshot(resourceRef) }
    catch (e) { setErr(e instanceof Error ? e.message : String(e)) }
    finally { setTaking(false) }
  }

  return (
    <div className="ml-6 rounded-md border border-lab-edge bg-black/20 p-2 text-[10px]">
      {err && <div className="pb-1 font-mono text-lab-red">{err}</div>}
      {snaps === null && !err && <div className="text-lab-dim">Loading snapshots…</div>}
      {snaps?.length === 0 && <div className="text-lab-dim">No snapshots.</div>}
      {snaps?.map((s) => (
        <div key={s.name + s.created} className="flex items-baseline gap-2 py-0.5 font-mono">
          <span className={s.current ? "text-lab-cyan" : "text-white/80"}>{s.current ? "▶" : "·"} {s.name}</span>
          <span className="ml-auto text-lab-dim">{s.created}</span>
        </div>
      ))}
      <button
        disabled={taking}
        onClick={take}
        className="mt-1 rounded bg-lab-cyan/12 px-2 py-0.5 text-[10px] text-lab-cyan hover:bg-lab-cyan/22 disabled:opacity-40"
      >{taking ? "…" : "+ Take snapshot"}</button>
      <span className="ml-2 text-lab-dim">runs in background — reopen to refresh</span>
    </div>
  )
}

function ResourceRow({ resource, busy, onStart, onStop, onSuspend, onRestart, onLogs, onChange }: {
  resource: ResourceNode; busy: boolean
  onStart: () => void; onStop: () => void; onSuspend: () => void; onRestart: () => void
  onLogs: () => void; onChange: () => void
}) {
  const [showSnaps, setShowSnaps] = useState(false)
  const { ref, name, kind, state, error, capabilities } = resource
  const running = state === "running"
  const transitional = TRANSITIONAL.includes(state)
  const disabled = busy || transitional
  const act = rowActions(capabilities)
  return (
    <div className="flex flex-col gap-1 rounded-lg px-3 py-2 hover:bg-white/5">
      <div className="flex items-center gap-3">
        <StatusDot state={state} pulse />
        <span className="text-[13px] leading-none opacity-80" title={kindLabel(kind)}>{kindIcon(kind)}</span>
        <span className="font-mono text-xs text-white/90">{name}</span>
        <span className={`text-[10px] uppercase tracking-wide ${transitional ? "text-lab-cyan" : "text-lab-dim"}`}>{state}</span>
        <div className="ml-auto flex items-center gap-1.5">
          {act.canLogs && (
            <button
              title="Logs"
              onClick={onLogs}
              className="rounded-md px-1.5 py-1 text-[11px] text-lab-dim transition hover:bg-white/10 hover:text-white/90"
            >▤</button>
          )}
          {act.canSnapshot && (
            <button
              title="Snapshots"
              onClick={() => setShowSnaps(!showSnaps)}
              className={`rounded-md px-1.5 py-1 text-[11px] transition ${showSnaps ? "bg-lab-cyan/20 text-lab-cyan" : "text-lab-dim hover:bg-white/10"}`}
            >⧉</button>
          )}
          {running && act.canRestart && (
            <button
              disabled={disabled}
              onClick={onRestart}
              title="Restart"
              className="rounded-md bg-lab-cyan/12 px-2 py-1 text-[11px] font-medium text-lab-cyan transition hover:bg-lab-cyan/22 disabled:opacity-40 disabled:cursor-not-allowed"
            >↻</button>
          )}
          {running && act.canSuspend && (
            <button
              disabled={disabled}
              onClick={onSuspend}
              title="Suspend (freeze to disk — resume with Start)"
              className="rounded-md bg-lab-amber/15 px-2 py-1 text-[11px] font-medium text-lab-amber transition hover:bg-lab-amber/25 disabled:opacity-40 disabled:cursor-not-allowed"
            >⏸</button>
          )}
          {((running && act.canStop) || (!running && act.canStart)) && (
            <button
              disabled={disabled}
              onClick={running ? onStop : onStart}
              className={`rounded-md px-2.5 py-1 text-[11px] font-medium transition disabled:opacity-40 disabled:cursor-not-allowed ${
                running ? "bg-lab-red/15 text-lab-red hover:bg-lab-red/25" : "bg-lab-green/15 text-lab-green hover:bg-lab-green/25"
              }`}
            >
              {transitional ? PHASE_LABEL[state] : busy ? "…" : running ? "Stop" : "Start"}
            </button>
          )}
        </div>
      </div>
      {showSnaps && act.canSnapshot && <SnapshotPanel resourceRef={ref} />}
      {error && <div className="pl-6"><DismissableError target={ref} message={error} onDismiss={onChange} /></div>}
    </div>
  )
}

export function GroupApp({ name, group, egress, onChange, onOpenLogs }: {
  name: string; group: GroupStatus; egress: EgressInfo | undefined; onChange: () => void
  onOpenLogs?: (ref: string, name: string) => void
}) {
  const [busy, setBusy] = useState<string | null>(null)
  const [err, setErr] = useState<string | null>(null)
  const now = useNow()
  const view = egress?.expires_at ? egressView(egress.expires_at, now) : null

  const act = async (key: string, fn: () => Promise<unknown>) => {
    setBusy(key); setErr(null)
    try { await fn(); await onChange() }
    catch (e) { setErr(e instanceof Error ? e.message : String(e)) }
    finally { setBusy(null) }
  }

  const anyRunning = group.resources.some((r) => r.state === "running")
  const allRunning = group.resources.every((r) => r.state === "running")

  return (
    <div className="flex flex-col gap-3 p-4">
      <div className="flex items-baseline gap-2">
        <h2 className="font-mono text-sm font-semibold text-lab-cyan neon-text">{group.label}</h2>
      </div>

      <div className="flex items-center gap-2">
        <button
          disabled={busy === "group" || allRunning}
          onClick={() => act("group", () => api.startGroup(name))}
          className="rounded-md bg-lab-green/15 px-2.5 py-1 text-[11px] font-medium text-lab-green hover:bg-lab-green/25 disabled:opacity-40 disabled:cursor-not-allowed"
        >▶ Start all</button>
        <button
          disabled={busy === "group" || !anyRunning}
          onClick={() => act("group", () => api.stopGroup(name))}
          className="rounded-md bg-lab-red/15 px-2.5 py-1 text-[11px] font-medium text-lab-red hover:bg-lab-red/25 disabled:opacity-40 disabled:cursor-not-allowed"
        >■ Stop all</button>
        <span className="ml-auto text-[10px] text-lab-dim">infra stops last</span>
      </div>

      {group.error && (
        <DismissableError target={`group:${name}`} message={group.error} onDismiss={onChange} />
      )}

      {egress !== undefined && (
        <div className="rounded-lg border border-lab-edge bg-white/5 p-3">
          <div className="mb-2 flex items-center gap-2">
            <div className="ml-auto flex items-center gap-2 text-[11px]">
              <span className={egress.internet ? "text-lab-green" : "text-lab-dim"}>
                {egress.internet ? "● internet" : "○ dark"}
              </span>
            </div>
          </div>
          {view?.overdue ? (
            <div className="flex items-center gap-2 rounded-md bg-lab-red/20 px-2 py-1.5 text-[11px]">
              <span className="animate-pulse font-semibold text-lab-red">⚠ REVOKE OVERDUE</span>
              <span className="text-lab-red/80">server is retrying every 30s</span>
              <button
                disabled={busy === "egress"}
                onClick={() => act("egress", () => api.revokeEgress(name))}
                className="ml-auto rounded-md bg-lab-red/25 px-2.5 py-1 text-[11px] font-medium text-white hover:bg-lab-red/40 disabled:opacity-40"
              >Retry now</button>
            </div>
          ) : egress.mode === "permanent" ? (
            <div className="text-[11px] text-lab-dim">Egress: permanent (exit node)</div>
          ) : egress.expires_at ? (
            <div className="flex items-center gap-2 text-[11px]">
              <span className="text-lab-dim">Egress reverts in</span>
              <Countdown expiresAt={egress.expires_at} />
              <button
                disabled={busy === "egress"}
                onClick={() => act("egress", () => api.extendEgress(name, 15 * 60))}
                className="rounded-md bg-lab-cyan/12 px-2 py-1 text-[11px] text-lab-cyan hover:bg-lab-cyan/22 disabled:opacity-40"
                title="Extend the window by 15 minutes (4h total cap)"
              >+15m</button>
              <button
                disabled={busy === "egress"}
                onClick={() => act("egress", () => api.revokeEgress(name))}
                className="ml-auto rounded-md bg-lab-red/15 px-2.5 py-1 text-[11px] text-lab-red hover:bg-lab-red/25 disabled:opacity-40"
              >Revoke now</button>
            </div>
          ) : (
            <div className="flex items-center gap-2">
              <span className="text-[11px] text-lab-dim">Grant internet for</span>
              {[15, 30, 60].map((min) => (
                <button
                  key={min}
                  disabled={busy === "egress"}
                  onClick={() => act("egress", () => api.enableEgress(name, min * 60))}
                  className="rounded-md bg-lab-cyan/12 px-2.5 py-1 text-[11px] text-lab-cyan hover:bg-lab-cyan/22 disabled:opacity-40"
                >{min}m</button>
              ))}
            </div>
          )}
        </div>
      )}

      <div className="flex flex-col gap-0.5">
        {group.resources.map((r) => (
          <ResourceRow
            key={r.ref} resource={r} busy={busy === r.ref} onChange={onChange}
            onStart={() => act(r.ref, () => api.startResource(r.ref))}
            onStop={() => act(r.ref, () => api.stopResource(r.ref))}
            onSuspend={() => act(r.ref, () => api.suspendResource(r.ref))}
            onRestart={() => act(r.ref, () => api.restartResource(r.ref))}
            onLogs={() => onOpenLogs?.(r.ref, r.name)}
          />
        ))}
      </div>

      {err && <div className="rounded-md bg-lab-red/15 px-3 py-2 font-mono text-[11px] text-lab-red">{err}</div>}
    </div>
  )
}
