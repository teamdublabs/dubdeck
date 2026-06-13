import { afterEach, describe, expect, it, vi } from "vitest"
import { AUTH_REQUIRED_EVENT, api, authEvents, logParams } from "./api"

describe("logParams", () => {
  it("is empty for no filters", () => {
    expect(logParams({})).toBe("")
  })

  it("builds the query string from set filters only", () => {
    expect(logParams({ limit: 50, action: "egress.", failures: true })).toBe(
      "?limit=50&action=egress.&failures=true",
    )
  })

  it("carries the pagination cursor", () => {
    expect(logParams({ before_id: 42 })).toBe("?before_id=42")
  })
})

describe("api error handling", () => {
  afterEach(() => vi.unstubAllGlobals())

  it("surfaces the backend detail message", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => new Response(JSON.stringify({ detail: "unknown group 'ghost'" }), { status: 404 })),
    )
    await expect(api.startGroup("ghost")).rejects.toThrow("unknown group 'ghost'")
  })

  it("falls back to the status text on non-JSON errors", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => new Response("<html>nope</html>", { status: 502, statusText: "Bad Gateway" })),
    )
    await expect(api.status()).rejects.toThrow("Bad Gateway")
  })

  it("URL-encodes VM names", async () => {
    const fetchMock = vi.fn(async (...args: Parameters<typeof fetch>) => {
      void args
      return new Response("{}", { status: 200 })
    })
    vi.stubGlobal("fetch", fetchMock)
    await api.startResource("host02-parallels/Windows 11 (ARM)")
    expect(fetchMock.mock.calls[0][0]).toBe("/api/resources/host02-parallels/Windows%2011%20(ARM)/start")
  })

  it("fetches resource logs as text with an encoded ref + line count", async () => {
    const fetchMock = vi.fn(async (...args: Parameters<typeof fetch>) => {
      void args
      return new Response("line1\nline2", { status: 200 })
    })
    vi.stubGlobal("fetch", fetchMock)
    const out = await api.resourceLogs("host01-docker/web app", 500)
    expect(fetchMock.mock.calls[0][0]).toBe("/api/resources/host01-docker/web%20app/logs?n=500")
    expect(out).toBe("line1\nline2")
  })

  it("surfaces the backend error text when logs fail", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => new Response("logs not supported", { status: 404 })))
    await expect(api.resourceLogs("p/r")).rejects.toThrow("logs not supported")
  })
})

describe("auth + settings client", () => {
  afterEach(() => vi.unstubAllGlobals())

  const okFetch = () => {
    const m = vi.fn(async (...args: Parameters<typeof fetch>) => {
      void args
      return new Response("{}", { status: 200 })
    })
    vi.stubGlobal("fetch", m)
    return m
  }

  it("posts login / setup / logout / change-password to the right routes", async () => {
    const m = okFetch()
    await api.login("pw")
    await api.setup("pw-12345678")
    await api.logout()
    await api.changePassword("old", "new-pw-123")
    const calls = m.mock.calls.map((c) => [c[0], (c[1] as RequestInit | undefined)?.method])
    expect(calls).toEqual([
      ["/api/login", "POST"],
      ["/api/setup", "POST"],
      ["/api/logout", "POST"],
      ["/api/auth/password", "POST"],
    ])
    // change-password sends current + new (renamed from the JS arg)
    expect(JSON.parse((m.mock.calls[3][1] as RequestInit).body as string)).toEqual({
      current: "old",
      new: "new-pw-123",
    })
  })

  it("PATCHes settings", async () => {
    const m = okFetch()
    await api.patchSettings({ "ui.branding.name": "Homelab" })
    expect(m.mock.calls[0][0]).toBe("/api/settings")
    expect((m.mock.calls[0][1] as RequestInit).method).toBe("PATCH")
  })

  it("dispatches an auth-required event on 401 from a gated call", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => new Response(JSON.stringify({ detail: "nope" }), { status: 401 })))
    const onAuth = vi.fn()
    authEvents.addEventListener(AUTH_REQUIRED_EVENT, onAuth)
    await expect(api.status()).rejects.toThrow("nope")
    expect(onAuth).toHaveBeenCalledOnce()
    authEvents.removeEventListener(AUTH_REQUIRED_EVENT, onAuth)
  })

  it("does NOT dispatch auth-required when login itself 401s", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => new Response(JSON.stringify({ detail: "invalid password" }), { status: 401 })))
    const onAuth = vi.fn()
    authEvents.addEventListener(AUTH_REQUIRED_EVENT, onAuth)
    await expect(api.login("wrong")).rejects.toThrow("invalid password")
    expect(onAuth).not.toHaveBeenCalled()
    authEvents.removeEventListener(AUTH_REQUIRED_EVENT, onAuth)
  })
})
