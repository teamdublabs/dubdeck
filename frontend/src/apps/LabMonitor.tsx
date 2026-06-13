import { useEffect, useRef, useState } from "react"
import { Area, AreaChart, ResponsiveContainer, YAxis } from "recharts"
import { api, type ResourceStats, type StatusSnapshot } from "../api"
import { formatBytes, pctOf } from "../lib/format"

// One memory-percent series per host, keyed by host name from the snapshot.
type Point = { t: number } & Record<string, number>

function pct(used?: number, total?: number) {
  return used && total ? Math.round((used / total) * 100) : 0
}

// Per-resource disk is deliberately slow-moving data — poll at the backend's cache TTL.
const VMSTATS_POLL_MS = 120_000

export function LabMonitor({ snapshot }: { snapshot: StatusSnapshot | null }) {
  const [history, setHistory] = useState<Point[]>([])
  const [vmstats, setVmstats] = useState<ResourceStats | null>(null)
  const tick = useRef(0)

  useEffect(() => {
    if (!snapshot) return
    const point: Point = { t: tick.current++ }
    for (const [name, host] of Object.entries(snapshot.hosts)) {
      point[name] = pct(host.stats?.mem_used, host.stats?.mem_total)
    }
    setHistory((h) => [...h.slice(-39), point])
  }, [snapshot])

  useEffect(() => {
    const load = () => api.resourcestats().then(setVmstats).catch(() => {})
    load()
    const id = setInterval(load, VMSTATS_POLL_MS)
    return () => clearInterval(id)
  }, [])

  const hosts = snapshot ? Object.entries(snapshot.hosts) : []
  const vmCounts = snapshot
    ? Object.values(snapshot.groups).flatMap((g) => g.resources.map((r) => r.state))
    : []
  const running = vmCounts.filter((s) => s === "running").length

  const egressGroups = snapshot?.modules.egress ? Object.entries(snapshot.modules.egress) : []
  const groupsOnline = egressGroups.filter(([, e]) => e.internet).length

  const vmDisks = vmstats
    ? Object.entries(vmstats.resources).sort(([, a], [, b]) => b.disk_bytes - a.disk_bytes)
    : []
  const maxDisk = vmDisks[0]?.[1].disk_bytes ?? 1

  return (
    <div className="flex flex-col gap-4 p-4">
      <div className="grid grid-cols-3 gap-3">
        <Stat label="VMs running" value={`${running}/${vmCounts.length}`} />
        <Stat label="Groups online" value={snapshot ? `${groupsOnline}/${egressGroups.length}` : "—"} />
        <Stat label="Hosts up" value={snapshot ? `${hosts.filter(([, h]) => h.reachable).length}/${hosts.length}` : "—"} />
      </div>

      {hosts.map(([name, host]) => {
        const diskPct = pctOf(host.stats?.disk_used, host.stats?.disk_total)
        return (
          <div key={name} className="rounded-lg border border-lab-edge bg-white/5 p-3">
            <div className="mb-1 flex items-center gap-2">
              <span className={`h-2 w-2 rounded-full ${host.reachable ? "bg-lab-green" : "bg-lab-red"}`} />
              <span className="font-mono text-xs text-white">{name}</span>
              {host.stats && (
                <span className="ml-auto font-mono text-[11px] text-lab-dim">
                  load {host.stats.load_1m.toFixed(2)} · mem {pct(host.stats.mem_used, host.stats.mem_total)}%
                </span>
              )}
              {!host.reachable && <span className="ml-auto text-[11px] text-lab-red">{host.error ?? "unreachable"}</span>}
            </div>
            {diskPct !== null && host.stats?.disk_total && (
              <div className="mb-1 flex items-center gap-2" title="Host disk (data volume)">
                <div className="h-1.5 flex-1 overflow-hidden rounded-full bg-white/10">
                  <div
                    className={`h-full rounded-full ${diskPct > 90 ? "bg-lab-red" : diskPct > 75 ? "bg-lab-amber" : "bg-lab-cyan"}`}
                    style={{ width: `${diskPct}%` }}
                  />
                </div>
                <span className="font-mono text-[10px] text-lab-dim">
                  disk {diskPct}% · {formatBytes(host.stats.disk_used ?? 0)} / {formatBytes(host.stats.disk_total)}
                </span>
              </div>
            )}
            <ResponsiveContainer width="100%" height={48}>
              <AreaChart data={history}>
                <defs>
                  <linearGradient id={`g-${name}`} x1="0" y1="0" x2="0" y2="1">
                    <stop offset="0%" stopColor="#38e8ff" stopOpacity={0.5} />
                    <stop offset="100%" stopColor="#38e8ff" stopOpacity={0} />
                  </linearGradient>
                </defs>
                <YAxis domain={[0, 100]} hide />
                <Area type="monotone" dataKey={name} stroke="#38e8ff" strokeWidth={1.5} fill={`url(#g-${name})`} isAnimationActive={false} />
              </AreaChart>
            </ResponsiveContainer>
          </div>
        )
      })}

      {vmDisks.length > 0 && (
        <div className="rounded-lg border border-lab-edge bg-white/5 p-3">
          <div className="mb-2 text-[10px] uppercase tracking-wide text-lab-dim">VM disk footprint (host)</div>
          <div className="flex flex-col gap-1">
            {vmDisks.map(([ref, { disk_bytes }]) => {
              const slash = ref.lastIndexOf("/")
              const label = slash >= 0 ? ref.slice(slash + 1) : ref
              return (
                <div key={ref} className="flex items-center gap-2">
                  <span className="w-28 truncate font-mono text-[11px] text-white/85" title={ref}>{label}</span>
                  <div className="h-1.5 flex-1 overflow-hidden rounded-full bg-white/10">
                    <div className="h-full rounded-full bg-lab-cyan/70" style={{ width: `${Math.max(2, (disk_bytes / maxDisk) * 100)}%` }} />
                  </div>
                  <span className="w-16 text-right font-mono text-[10px] text-lab-dim">{formatBytes(disk_bytes)}</span>
                </div>
              )
            })}
          </div>
        </div>
      )}
    </div>
  )
}

function Stat({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded-lg border border-lab-edge bg-white/5 p-3">
      <div className="font-mono text-lg font-semibold text-lab-cyan neon-text">{value}</div>
      <div className="text-[10px] uppercase tracking-wide text-lab-dim">{label}</div>
    </div>
  )
}
