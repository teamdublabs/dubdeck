import { useEffect, useRef } from "react"
import { DEFAULT_THEME, SHADERS, VERT } from "./glsl"

function compile(gl: WebGLRenderingContext, type: number, src: string): WebGLShader {
  const s = gl.createShader(type)!
  gl.shaderSource(s, src)
  gl.compileShader(s)
  if (!gl.getShaderParameter(s, gl.COMPILE_STATUS)) {
    console.error("shader compile:", gl.getShaderInfoLog(s))
  }
  return s
}

interface Prog {
  prog: WebGLProgram
  pos: number
  time: WebGLUniformLocation | null
  res: WebGLUniformLocation | null
  mouse: WebGLUniformLocation | null
}

// One WebGL canvas; swaps the active fragment shader when `theme` changes.
export function Backgrounds({ theme }: { theme: string }) {
  const canvasRef = useRef<HTMLCanvasElement>(null)
  const themeRef = useRef(theme)
  const kickRef = useRef<() => void>(() => {})
  useEffect(() => {
    themeRef.current = theme
  }, [theme])

  useEffect(() => {
    const canvas = canvasRef.current!
    const gl = canvas.getContext("webgl", { antialias: false, alpha: false, premultipliedAlpha: false })
    if (!gl) return
    const scale = Math.min(window.devicePixelRatio || 1, 1.5)

    const buf = gl.createBuffer()
    gl.bindBuffer(gl.ARRAY_BUFFER, buf)
    gl.bufferData(gl.ARRAY_BUFFER, new Float32Array([-1, -1, 3, -1, -1, 3]), gl.STATIC_DRAW)

    const cache: Record<string, Prog> = {}
    const getProg = (id: string): Prog => {
      if (cache[id]) return cache[id]
      const vs = compile(gl, gl.VERTEX_SHADER, VERT)
      const fs = compile(gl, gl.FRAGMENT_SHADER, SHADERS[id].frag)
      const prog = gl.createProgram()!
      gl.attachShader(prog, vs)
      gl.attachShader(prog, fs)
      gl.linkProgram(prog)
      if (!gl.getProgramParameter(prog, gl.LINK_STATUS)) {
        console.error("link:", gl.getProgramInfoLog(prog))
      }
      cache[id] = {
        prog,
        pos: gl.getAttribLocation(prog, "a_pos"),
        time: gl.getUniformLocation(prog, "u_time"),
        res: gl.getUniformLocation(prog, "u_res"),
        mouse: gl.getUniformLocation(prog, "u_mouse"),
      }
      return cache[id]
    }

    const mouse = { x: 0, y: 0, tx: 0, ty: 0 }
    const onMove = (e: MouseEvent) => {
      mouse.tx = (e.clientX / window.innerWidth - 0.5) * 2
      mouse.ty = -(e.clientY / window.innerHeight - 0.5) * 2
    }
    window.addEventListener("mousemove", onMove)

    let raf = 0
    const start = performance.now()
    const frame = (now: number) => {
      mouse.x += (mouse.tx - mouse.x) * 0.04
      mouse.y += (mouse.ty - mouse.y) * 0.04
      const id = themeRef.current in SHADERS ? themeRef.current : DEFAULT_THEME
      const p = getProg(id)
      gl.useProgram(p.prog)
      gl.bindBuffer(gl.ARRAY_BUFFER, buf)
      gl.enableVertexAttribArray(p.pos)
      gl.vertexAttribPointer(p.pos, 2, gl.FLOAT, false, 0, 0)
      gl.uniform1f(p.time, (now - start) / 1000)
      gl.uniform2f(p.res, canvas.width, canvas.height)
      gl.uniform2f(p.mouse, mouse.x, mouse.y)
      gl.drawArrays(gl.TRIANGLES, 0, 3)
      // static themes draw once; kick() restarts the loop on theme change/resize
      if (!SHADERS[id].static) raf = requestAnimationFrame(frame)
    }
    const kick = () => {
      cancelAnimationFrame(raf)
      raf = requestAnimationFrame(frame)
    }
    kickRef.current = kick

    const resize = () => {
      const w = window.innerWidth
      const h = window.innerHeight
      canvas.width = Math.floor(w * scale)
      canvas.height = Math.floor(h * scale)
      canvas.style.width = `${w}px`
      canvas.style.height = `${h}px`
      gl.viewport(0, 0, canvas.width, canvas.height)
      kick()
    }
    resize()
    window.addEventListener("resize", resize)

    return () => {
      // NB: do NOT loseContext() here — React StrictMode reuses the same canvas
      // on remount, and a lost context makes every subsequent compile fail
      // silently. Just stop the loop; the GPU resources free with the canvas.
      cancelAnimationFrame(raf)
      window.removeEventListener("resize", resize)
      window.removeEventListener("mousemove", onMove)
    }
  }, [])

  // restart the loop on theme change — a static theme may have stopped it
  useEffect(() => { kickRef.current() }, [theme])

  return <canvas ref={canvasRef} className="fixed inset-0" style={{ width: "100vw", height: "100vh" }} />
}
