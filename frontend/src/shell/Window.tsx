import { motion } from "framer-motion"
import type { ReactNode } from "react"
import { useRef } from "react"

export interface WindowProps {
  id: string
  title: string
  icon: ReactNode
  z: number
  minimized: boolean
  focused: boolean
  maximized: boolean
  position: { x: number; y: number }
  size: { w: number; h: number }
  onFocus: () => void
  onClose: () => void
  onMinimize: () => void
  onMaximize: () => void
  onMove: (pos: { x: number; y: number }) => void
  onResize: (size: { w: number; h: number }) => void
  children: ReactNode
}

const MIN_W = 300
const MIN_H = 220

function Light({ color, glyph, title, onClick }: {
  color: string; glyph: string; title: string; onClick: () => void
}) {
  return (
    <button
      onClick={onClick}
      title={title}
      style={{ background: color, boxShadow: `0 0 8px ${color}99` }}
      className="group/light flex h-3.5 w-3.5 items-center justify-center rounded-full text-[8px] font-bold text-black/70 transition hover:brightness-125"
    >
      <span className="opacity-0 transition group-hover/light:opacity-100">{glyph}</span>
    </button>
  )
}

export function Window(props: WindowProps) {
  const dragControls = useRef<{ x: number; y: number } | null>(null)
  if (props.minimized) return null

  return (
    <motion.div
      className={`glass absolute flex flex-col overflow-hidden rounded-2xl ${props.focused ? "win-focused" : ""}`}
      style={{
        left: props.position.x,
        top: props.position.y,
        width: props.size.w,
        height: props.size.h,
        zIndex: props.z,
      }}
      initial={{ opacity: 0, scale: 0.9, y: 22, filter: "blur(10px)" }}
      animate={{ opacity: 1, scale: 1, y: 0, filter: "blur(0px)" }}
      exit={{ opacity: 0, scale: 0.92, filter: "blur(8px)" }}
      transition={{ type: "spring", stiffness: 320, damping: 28 }}
      onMouseDown={props.onFocus}
    >
      {props.focused && <div className="win-accent" />}
      <div
        className="titlebar flex h-9 shrink-0 cursor-grab items-center gap-2 px-3 active:cursor-grabbing"
        onDoubleClick={props.onMaximize}
        onMouseDown={(e) => {
          props.onFocus()
          if (props.maximized) return
          dragControls.current = { x: e.clientX - props.position.x, y: e.clientY - props.position.y }
          const move = (ev: MouseEvent) => {
            if (!dragControls.current) return
            props.onMove({
              x: ev.clientX - dragControls.current.x,
              y: Math.max(0, ev.clientY - dragControls.current.y),
            })
          }
          const up = () => {
            dragControls.current = null
            window.removeEventListener("mousemove", move)
            window.removeEventListener("mouseup", up)
          }
          window.addEventListener("mousemove", move)
          window.addEventListener("mouseup", up)
        }}
      >
        <span
          className="grid h-5 w-5 place-items-center rounded-md text-[13px]"
          style={{ background: "rgba(56,232,255,0.12)", boxShadow: "inset 0 0 0 1px rgba(56,232,255,0.25)" }}
        >
          {props.icon}
        </span>
        <span className={`font-mono text-xs font-medium tracking-wide ${props.focused ? "text-lab-cyan neon-text" : "text-white/55"}`}>
          {props.title}
        </span>
        <div className="ml-auto flex items-center gap-2">
          <Light color="#ffb01f" glyph="—" title="Minimize" onClick={props.onMinimize} />
          <Light color="#00d97e" glyph="⤢" title="Maximize" onClick={props.onMaximize} />
          <Light color="#ff4d6d" glyph="✕" title="Close" onClick={props.onClose} />
        </div>
      </div>
      <div className="min-h-0 flex-1 overflow-auto">{props.children}</div>
      {!props.maximized && (
        <div
          title="Resize"
          className="absolute bottom-0 right-0 h-4 w-4 cursor-nwse-resize"
          style={{
            background:
              "linear-gradient(135deg, transparent 50%, rgba(120,200,255,0.35) 50%)",
            borderBottomRightRadius: "1rem",
          }}
          onMouseDown={(e) => {
            e.stopPropagation()
            props.onFocus()
            const from = { x: e.clientX, y: e.clientY, w: props.size.w, h: props.size.h }
            const move = (ev: MouseEvent) => {
              props.onResize({
                w: Math.max(MIN_W, from.w + ev.clientX - from.x),
                h: Math.max(MIN_H, from.h + ev.clientY - from.y),
              })
            }
            const up = () => {
              window.removeEventListener("mousemove", move)
              window.removeEventListener("mouseup", up)
            }
            window.addEventListener("mousemove", move)
            window.addEventListener("mouseup", up)
          }}
        />
      )}
    </motion.div>
  )
}
