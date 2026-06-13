import type { VMState } from "../api"

const COLORS: Record<string, string> = {
  running: "bg-lab-green shadow-[0_0_8px_#00ff95]",
  stopped: "bg-lab-dim",
  suspended: "bg-lab-amber shadow-[0_0_8px_#ffb01f]",
  paused: "bg-lab-amber",
  unknown: "bg-lab-red/70",
  starting: "bg-lab-cyan shadow-[0_0_8px_#38e8ff]",
  stopping: "bg-lab-amber shadow-[0_0_8px_#ffb01f]",
}

export function StatusDot({ state, pulse }: { state: VMState | string; pulse?: boolean }) {
  const transitional = state === "starting" || state === "stopping"
  const animate = transitional || (pulse && state === "running")
  return <span className={`inline-block h-2.5 w-2.5 rounded-full ${COLORS[state] ?? COLORS.unknown} ${animate ? "pulse" : ""}`} />
}
