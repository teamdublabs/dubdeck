import { useCallback, useEffect, useRef, useState } from "react"
import { api, type StatusSnapshot } from "./api"

// SSE first (prompt pushes after every action), polling as the fallback when
// the stream errors — and the stream auto-reconnects with a short backoff.
export function useStatus(pollMs = 5000) {
  const [snapshot, setSnapshot] = useState<StatusSnapshot | null>(null)
  const [error, setError] = useState<string | null>(null)
  const pollTimer = useRef<number>(0)

  const refresh = useCallback(async () => {
    try {
      setSnapshot(await api.status())
      setError(null)
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    }
  }, [])

  useEffect(() => {
    let source: EventSource | null = null
    let reconnect = 0
    let disposed = false

    const stopPolling = () => {
      if (pollTimer.current) {
        window.clearInterval(pollTimer.current)
        pollTimer.current = 0
      }
    }
    const startPolling = () => {
      if (!pollTimer.current) {
        refresh()
        pollTimer.current = window.setInterval(refresh, pollMs)
      }
    }

    const connect = () => {
      if (disposed) return
      source = new EventSource("/api/events")
      source.onmessage = (ev) => {
        stopPolling()
        setSnapshot(JSON.parse(ev.data))
        setError(null)
      }
      source.onerror = () => {
        source?.close()
        startPolling() // keep data flowing while the stream is down
        if (!disposed) reconnect = window.setTimeout(connect, 3000)
      }
    }

    connect()
    return () => {
      disposed = true
      source?.close()
      stopPolling()
      window.clearTimeout(reconnect)
    }
  }, [refresh, pollMs])

  return { snapshot, error, refresh }
}
