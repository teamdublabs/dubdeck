import { type FormEvent, useState } from "react"
import { api } from "../api"

/** First-run setup OR returning login. One card, two modes — the desktop is
 *  hidden behind it until a session exists. */
export function AuthScreen({
  mode,
  brand,
  onAuthed,
}: {
  mode: "setup" | "login"
  brand: string
  onAuthed: () => void
}) {
  const [password, setPassword] = useState("")
  const [confirm, setConfirm] = useState("")
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)

  const setup = mode === "setup"

  async function submit(e: FormEvent) {
    e.preventDefault()
    setError(null)
    if (setup && password !== confirm) {
      setError("passwords do not match")
      return
    }
    if (setup && password.length < 8) {
      setError("password must be at least 8 characters")
      return
    }
    setBusy(true)
    try {
      await (setup ? api.setup(password) : api.login(password))
      onAuthed()
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err))
      setBusy(false)
    }
  }

  return (
    <div className="grid h-full w-full place-items-center p-6">
      <form onSubmit={submit} className="glass w-full max-w-sm rounded-2xl p-7">
        <div className="mb-1 font-mono text-lg font-semibold text-lab-cyan neon-text">◈ {brand}</div>
        <div className="mb-5 font-mono text-[11px] uppercase tracking-widest text-lab-dim">
          {setup ? "first-run setup · choose a password" : "locked · sign in to continue"}
        </div>

        <label className="mb-1 block font-mono text-[10px] uppercase tracking-widest text-lab-dim">
          Password
        </label>
        <input
          type="password"
          autoFocus
          autoComplete={setup ? "new-password" : "current-password"}
          value={password}
          onChange={(e) => setPassword(e.target.value)}
          className="mb-3 w-full rounded-lg border border-white/10 bg-black/30 px-3 py-2 font-mono text-sm text-white outline-none focus:border-lab-cyan/50"
        />

        {setup && (
          <>
            <label className="mb-1 block font-mono text-[10px] uppercase tracking-widest text-lab-dim">
              Confirm password
            </label>
            <input
              type="password"
              autoComplete="new-password"
              value={confirm}
              onChange={(e) => setConfirm(e.target.value)}
              className="mb-3 w-full rounded-lg border border-white/10 bg-black/30 px-3 py-2 font-mono text-sm text-white outline-none focus:border-lab-cyan/50"
            />
          </>
        )}

        {error && (
          <div className="mb-3 rounded-md bg-lab-red/15 px-3 py-1.5 font-mono text-[11px] text-lab-red">
            {error}
          </div>
        )}

        <button
          type="submit"
          disabled={busy || !password}
          className="w-full rounded-lg px-3 py-2 font-mono text-sm font-semibold text-lab-cyan transition neon-text hover:brightness-125 disabled:opacity-40"
          style={{
            background: "linear-gradient(160deg, rgba(56,232,255,0.22), rgba(56,232,255,0.08))",
            border: "1px solid rgba(56,232,255,0.3)",
          }}
        >
          {busy ? "…" : setup ? "Set password & enter" : "Sign in"}
        </button>

        {setup && (
          <p className="mt-3 font-mono text-[10px] leading-relaxed text-lab-dim">
            No account exists yet. This password is stored hashed (argon2id); there are no
            default credentials.
          </p>
        )}
      </form>
    </div>
  )
}
