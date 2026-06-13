import { type FormEvent, useEffect, useState } from "react"
import { api, type Settings } from "../api"

function Toggle({ on, onClick }: { on: boolean; onClick: () => void }) {
  return (
    <button
      type="button"
      onClick={onClick}
      className={`relative h-5 w-9 rounded-full transition ${on ? "bg-lab-cyan/70" : "bg-white/15"}`}
    >
      <span
        className={`absolute top-0.5 h-4 w-4 rounded-full bg-white transition-all ${on ? "left-[18px]" : "left-0.5"}`}
      />
    </button>
  )
}

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <div className="border-b border-white/10 px-4 py-3.5">
      <div className="mb-2.5 font-mono text-[10px] uppercase tracking-widest text-lab-dim">{title}</div>
      {children}
    </div>
  )
}

export function SettingsApp({ onBrandChange }: { onBrandChange?: (name: string) => void }) {
  const [settings, setSettings] = useState<Settings | null>(null)
  const [modules, setModules] = useState<string[]>([])
  const [brand, setBrand] = useState("")

  useEffect(() => {
    api.settings().then((s) => {
      setSettings(s)
      setBrand(typeof s["ui.branding.name"] === "string" ? (s["ui.branding.name"] as string) : "")
    }).catch(() => {})
    api.config().then((c) => setModules(Object.keys(c.modules))).catch(() => {})
  }, [])

  async function patch(updates: Settings) {
    const next = await api.patchSettings(updates)
    setSettings(next)
    return next
  }

  async function toggleModule(name: string, on: boolean) {
    await patch({ [`modules.${name}.enabled`]: on })
  }

  async function saveBrand(e: FormEvent) {
    e.preventDefault()
    const next = await patch({ "ui.branding.name": brand })
    const n = next["ui.branding.name"]
    if (typeof n === "string") onBrandChange?.(n)
  }

  if (!settings) return <div className="p-4 font-mono text-[11px] text-lab-dim">Loading…</div>

  const moduleOn = (name: string) => settings[`modules.${name}.enabled`] === true

  return (
    <div className="flex h-full flex-col overflow-auto">
      <Section title="Modules">
        {modules.length === 0 ? (
          <div className="font-mono text-[11px] text-lab-dim">No optional modules configured.</div>
        ) : (
          modules.map((name) => (
            <div key={name} className="flex items-center justify-between py-1.5">
              <span className="font-mono text-sm text-white/90">{name}</span>
              <Toggle on={moduleOn(name)} onClick={() => toggleModule(name, !moduleOn(name))} />
            </div>
          ))
        )}
        <p className="mt-1.5 font-mono text-[10px] text-lab-dim">
          Module changes take effect when the backend restarts.
        </p>
      </Section>

      <Section title="Branding">
        <form onSubmit={saveBrand} className="flex items-center gap-2">
          <input
            value={brand}
            onChange={(e) => setBrand(e.target.value)}
            placeholder="Dubdeck"
            className="min-w-0 flex-1 rounded-lg border border-white/10 bg-black/30 px-3 py-1.5 font-mono text-sm text-white outline-none focus:border-lab-cyan/50"
          />
          <button
            type="submit"
            className="rounded-lg border border-lab-cyan/30 bg-lab-cyan/10 px-3 py-1.5 font-mono text-[11px] text-lab-cyan hover:bg-lab-cyan/20"
          >
            Save
          </button>
        </form>
      </Section>

      <Section title="Change password">
        <ChangePassword />
      </Section>
    </div>
  )
}

function ChangePassword() {
  const [current, setCurrent] = useState("")
  const [next, setNext] = useState("")
  const [confirm, setConfirm] = useState("")
  const [msg, setMsg] = useState<{ ok: boolean; text: string } | null>(null)
  const [busy, setBusy] = useState(false)

  async function submit(e: FormEvent) {
    e.preventDefault()
    setMsg(null)
    if (next !== confirm) {
      setMsg({ ok: false, text: "new passwords do not match" })
      return
    }
    if (next.length < 8) {
      setMsg({ ok: false, text: "new password must be at least 8 characters" })
      return
    }
    setBusy(true)
    try {
      await api.changePassword(current, next)
      setMsg({ ok: true, text: "password changed" })
      setCurrent(""); setNext(""); setConfirm("")
    } catch (err) {
      setMsg({ ok: false, text: err instanceof Error ? err.message : String(err) })
    }
    setBusy(false)
  }

  const field = (placeholder: string, value: string, set: (v: string) => void) => (
    <input
      type="password"
      autoComplete="off"
      placeholder={placeholder}
      value={value}
      onChange={(e) => set(e.target.value)}
      className="w-full rounded-lg border border-white/10 bg-black/30 px-3 py-1.5 font-mono text-sm text-white outline-none focus:border-lab-cyan/50"
    />
  )

  return (
    <form onSubmit={submit} className="flex flex-col gap-2">
      {field("current password", current, setCurrent)}
      {field("new password", next, setNext)}
      {field("confirm new password", confirm, setConfirm)}
      {msg && (
        <div className={`font-mono text-[11px] ${msg.ok ? "text-lab-green" : "text-lab-red"}`}>{msg.text}</div>
      )}
      <button
        type="submit"
        disabled={busy || !current || !next}
        className="self-start rounded-lg border border-lab-cyan/30 bg-lab-cyan/10 px-3 py-1.5 font-mono text-[11px] text-lab-cyan hover:bg-lab-cyan/20 disabled:opacity-40"
      >
        {busy ? "…" : "Change password"}
      </button>
    </form>
  )
}
