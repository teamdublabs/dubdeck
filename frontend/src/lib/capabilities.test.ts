import { describe, expect, it } from "vitest"
import { kindIcon, kindLabel, rowActions, type RowActions } from "./capabilities"

describe("rowActions", () => {
  // capability set -> which buttons the row exposes
  const cases: Array<[string, string[], Partial<RowActions>]> = [
    [
      "VM provider (parallels/libvirt)",
      ["start", "stop", "force_stop", "suspend", "snapshot_list", "snapshot_create", "disk_stats"],
      { canStart: true, canStop: true, canSuspend: true, canSnapshot: true, canRestart: false, canLogs: false },
    ],
    [
      "container provider (docker)",
      ["start", "stop", "restart", "logs"],
      { canStart: true, canStop: true, canRestart: true, canLogs: true, canSuspend: false, canSnapshot: false },
    ],
    [
      "stack provider (compose)",
      ["start", "stop", "restart"],
      { canStart: true, canStop: true, canRestart: true, canSuspend: false, canSnapshot: false, canLogs: false },
    ],
    ["no capabilities", [], { canStart: false, canStop: false, canSuspend: false, canRestart: false, canSnapshot: false, canLogs: false }],
  ]

  it.each(cases)("%s", (_name, caps, expected) => {
    expect(rowActions(caps)).toMatchObject(expected)
  })
})

describe("kind metadata", () => {
  it("maps known kinds to icon + label", () => {
    expect(kindIcon("vm")).toBe("🖥")
    expect(kindIcon("container")).toBe("📦")
    expect(kindIcon("stack")).toBe("🗄")
    expect(kindLabel("container")).toBe("Container")
  })

  it("falls back gracefully for unknown kinds", () => {
    expect(kindIcon("widget")).toBe("▦")
    expect(kindLabel("widget")).toBe("widget")
  })
})
