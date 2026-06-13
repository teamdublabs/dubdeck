import { describe, expect, it } from "vitest"
import { formatBytes, pctOf } from "./format"

describe("formatBytes", () => {
  it("keeps small values in bytes", () => {
    expect(formatBytes(512)).toBe("512 B")
  })

  it("scales to GB with one decimal", () => {
    expect(formatBytes(93914044 * 1024)).toBe("89.6 GB")
  })

  it("drops decimals at three digits", () => {
    expect(formatBytes(120 * 1024 ** 3)).toBe("120 GB")
  })

  it("handles TB-scale host disks", () => {
    expect(formatBytes(1948404040 * 1024)).toBe("1.8 TB")
  })
})

describe("pctOf", () => {
  it("computes a rounded percentage", () => {
    expect(pctOf(91, 100)).toBe(91)
  })

  it("is null when either side is missing", () => {
    expect(pctOf(null, 100)).toBeNull()
    expect(pctOf(50, null)).toBeNull()
    expect(pctOf(0, 100)).toBeNull()
  })
})
