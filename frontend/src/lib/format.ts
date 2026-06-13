// Pure formatting helpers — covered by vitest.

const UNITS = ["B", "KB", "MB", "GB", "TB"]

export function formatBytes(n: number): string {
  if (n < 0) return "—"
  let value = n
  let unit = 0
  while (value >= 1000 && unit < UNITS.length - 1) {
    value /= 1024
    unit++
  }
  const digits = value >= 100 || unit === 0 ? 0 : 1
  return `${value.toFixed(digits)} ${UNITS[unit]}`
}

export function pctOf(used?: number | null, total?: number | null): number | null {
  if (!used || !total) return null
  return Math.round((used / total) * 100)
}
