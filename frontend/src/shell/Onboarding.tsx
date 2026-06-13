import { useState } from "react"
import type { StatusSnapshot } from "../api"
import { unreachable } from "../lib/health"

// A minimal, valid v2 config users can paste verbatim and then edit. Kept in
// sync with config.example.yaml's schema (hosts / providers / groups).
const EXAMPLE = `hosts:
  linux01:
    transport: ssh
    address: 192.0.2.10   # an IP/host the backend can SSH to
    user: labuser

providers:
  - id: lab-kvm
    type: libvirt         # or: parallels
    host: linux01

groups:
  lab:
    label: "My Lab"
    members:
      - lab-kvm/example-vm
`

function CopyButton({ text }: { text: string }) {
  const [copied, setCopied] = useState(false)
  return (
    <button
      onClick={() => {
        navigator.clipboard?.writeText(text).then(
          () => { setCopied(true); window.setTimeout(() => setCopied(false), 1500) },
          () => {},
        )
      }}
      className="rounded-md border border-lab-cyan/30 bg-lab-cyan/10 px-2.5 py-1 font-mono text-[11px] text-lab-cyan hover:bg-lab-cyan/20"
    >
      {copied ? "copied ✓" : "copy"}
    </button>
  )
}

/** Shown when no config exists — guides the user to write config.yaml. */
export function Onboarding({ brand }: { brand: string }) {
  return (
    <div className="pointer-events-auto glass w-[min(560px,90vw)] rounded-2xl p-6 text-white/90 shadow-2xl">
      <h1 className="mb-1 font-mono text-lg font-semibold text-lab-cyan neon-text">Welcome to {brand}</h1>
      <p className="mb-4 text-sm text-white/70">
        No infrastructure is configured yet. Point {brand} at a{" "}
        <code className="rounded bg-black/40 px-1 font-mono text-lab-cyan">config.yaml</code> describing the
        hosts, providers, and groups it should manage, then restart the backend.
      </p>

      <div className="mb-2 flex items-center justify-between">
        <span className="font-mono text-[10px] uppercase tracking-widest text-lab-dim">Minimal config.yaml</span>
        <CopyButton text={EXAMPLE} />
      </div>
      <pre className="max-h-64 overflow-auto rounded-lg border border-lab-edge bg-black/40 p-3 font-mono text-[11px] leading-relaxed text-white/85">
        {EXAMPLE}
      </pre>

      <p className="mt-4 text-[12px] text-white/60">
        Mount it at the path in{" "}
        <code className="rounded bg-black/40 px-1 font-mono text-lab-cyan">DUBDECK_CONFIG</code> (see{" "}
        <code className="rounded bg-black/40 px-1 font-mono">config.example.yaml</code> for every field), then{" "}
        <code className="rounded bg-black/40 px-1 font-mono">docker compose up -d</code>.
      </p>
    </div>
  )
}

/** Shown when config exists but every provider is unreachable — surfaces the
 *  backend's per-host/provider error text so the user can fix connectivity. */
export function HostDiagnostics({ snapshot, onDismiss }: {
  snapshot: StatusSnapshot; onDismiss: () => void
}) {
  const problems = unreachable(snapshot)
  return (
    <div className="pointer-events-auto glass w-[min(560px,90vw)] rounded-2xl p-6 text-white/90 shadow-2xl">
      <div className="mb-1 flex items-baseline justify-between">
        <h1 className="font-mono text-lg font-semibold text-lab-red neon-text">Nothing reachable</h1>
        <button onClick={onDismiss} title="Dismiss" className="rounded px-1.5 text-lab-dim hover:bg-white/10 hover:text-white/90">✕</button>
      </div>
      <p className="mb-4 text-sm text-white/70">
        {brandJoin(problems.length)} configured but unreachable. The backend reported:
      </p>
      <div className="flex flex-col gap-2">
        {problems.map((p) => (
          <div key={`${p.kind}:${p.name}`} className="rounded-lg border border-lab-edge bg-black/30 p-2.5">
            <div className="flex items-baseline gap-2">
              <span className="font-mono text-xs text-white/90">{p.name}</span>
              <span className="font-mono text-[10px] uppercase tracking-wide text-lab-dim">{p.kind}</span>
            </div>
            <div className="mt-1 break-words font-mono text-[11px] text-lab-red/90">{p.error}</div>
          </div>
        ))}
      </div>
      <p className="mt-4 text-[12px] text-white/60">
        Check the address, SSH user/key, and that the host is up. Dubdeck keeps retrying — this clears on its own when a host comes back.
      </p>
    </div>
  )
}

function brandJoin(n: number): string {
  return n === 1 ? "1 host/provider is" : `${n} hosts/providers are`
}
