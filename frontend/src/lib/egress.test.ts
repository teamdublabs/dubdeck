import { describe, expect, it } from "vitest"
import { egressView, soonestExpiry } from "./egress"

const NOW = 1_750_000_000_000 // ms

describe("egressView", () => {
  it("formats remaining time as m:ss", () => {
    const view = egressView(NOW / 1000 + 754, NOW) // 12m34s left
    expect(view.display).toBe("12:34")
    expect(view.overdue).toBe(false)
  })

  it("pads seconds", () => {
    expect(egressView(NOW / 1000 + 65, NOW).display).toBe("1:05")
  })

  it("clamps the display at 0:00 once expired", () => {
    expect(egressView(NOW / 1000 - 30, NOW).display).toBe("0:00")
  })

  it("is not overdue within the 5s revoke grace", () => {
    expect(egressView(NOW / 1000 - 3, NOW).overdue).toBe(false)
  })

  it("flags overdue past the grace — failed revoke being retried", () => {
    expect(egressView(NOW / 1000 - 30, NOW).overdue).toBe(true)
  })
})

describe("soonestExpiry", () => {
  it("picks the earliest active window", () => {
    expect(soonestExpiry([null, 200, 100, null])).toBe(100)
  })

  it("returns null when nothing is active", () => {
    expect(soonestExpiry([null, null])).toBeNull()
    expect(soonestExpiry([])).toBeNull()
  })
})
