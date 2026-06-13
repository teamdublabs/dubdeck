import { useEffect, useRef, useState } from "react"
import { THEMES } from "./backgrounds/glsl"

export function BackgroundPicker({ theme, setTheme }: {
  theme: string
  setTheme: (id: string) => void
}) {
  const [open, setOpen] = useState(false)
  const ref = useRef<HTMLDivElement>(null)
  const current = THEMES.find((t) => t.id === theme) ?? THEMES[0]

  useEffect(() => {
    const onDoc = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false)
    }
    document.addEventListener("mousedown", onDoc)
    return () => document.removeEventListener("mousedown", onDoc)
  }, [])

  return (
    <div ref={ref} className="relative">
      <button
        onClick={() => setOpen((o) => !o)}
        className="glass flex items-center gap-2 rounded-lg px-2.5 py-1.5 font-mono text-[11px] text-white/85 transition hover:text-lab-cyan"
      >
        <span className="h-2 w-2 rounded-full bg-lab-cyan shadow-[0_0_8px_#38e8ff]" />
        {current.label}
        <span className={`text-[8px] transition ${open ? "rotate-180" : ""}`}>▾</span>
      </button>
      {open && (
        <div className="glass absolute right-0 top-9 z-[9999] w-44 rounded-lg p-1">
          <div className="px-2 py-1 font-mono text-[9px] uppercase tracking-widest text-lab-dim">
            Scene
          </div>
          {THEMES.map((t) => (
            <button
              key={t.id}
              onClick={() => { setTheme(t.id); setOpen(false) }}
              className={`flex w-full items-center gap-2 rounded-md px-2 py-1.5 text-left font-mono text-[11px] transition hover:bg-lab-cyan/15 ${
                t.id === theme ? "text-lab-cyan" : "text-white/80"
              }`}
            >
              <span className={`h-1.5 w-1.5 rounded-full ${t.id === theme ? "bg-lab-cyan shadow-[0_0_6px_#38e8ff]" : "bg-white/25"}`} />
              {t.label}
            </button>
          ))}
        </div>
      )}
    </div>
  )
}
