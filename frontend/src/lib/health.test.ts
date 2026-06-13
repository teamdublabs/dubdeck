import { describe, expect, it } from "vitest"
import type { StatusSnapshot } from "../api"
import { allProvidersDown, configEmpty, unreachable } from "./health"

function snap(partial: Partial<StatusSnapshot>): StatusSnapshot {
  return { generated_at: 0, hosts: {}, providers: {}, groups: {}, modules: {}, ...partial }
}

const host = (reachable: boolean, error: string | null = null) => ({ reachable, error, stats: null })
const provider = (reachable: boolean, error: string | null = null) => ({ reachable, error })

describe("configEmpty", () => {
  it("is true when nothing is configured", () => {
    expect(configEmpty(snap({}))).toBe(true)
  })
  it("is false once any host/provider/group exists", () => {
    expect(configEmpty(snap({ hosts: { a: host(true) } }))).toBe(false)
    expect(configEmpty(snap({ groups: { g: { label: "G", resources: [] } } }))).toBe(false)
  })
})

describe("unreachable", () => {
  it("collects down hosts and providers with their error text", () => {
    const s = snap({
      hosts: { up: host(true), down: host(false, "ssh: timeout") },
      providers: { p1: provider(true), p2: provider(false, "connection refused") },
    })
    expect(unreachable(s)).toEqual([
      { name: "down", kind: "host", error: "ssh: timeout" },
      { name: "p2", kind: "provider", error: "connection refused" },
    ])
  })
  it("falls back to a generic message when error is null", () => {
    expect(unreachable(snap({ hosts: { h: host(false) } }))[0].error).toBe("unreachable")
  })
})

describe("allProvidersDown", () => {
  it("is false with no providers (that's the empty/onboarding case)", () => {
    expect(allProvidersDown(snap({}))).toBe(false)
  })
  it("is true only when every provider is unreachable", () => {
    expect(allProvidersDown(snap({ providers: { a: provider(false), b: provider(false) } }))).toBe(true)
    expect(allProvidersDown(snap({ providers: { a: provider(false), b: provider(true) } }))).toBe(false)
  })
})
