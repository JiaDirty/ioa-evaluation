import { useState, useEffect, useRef, useCallback } from 'react'
import type { WSMessage } from '../types'

export function useWebSocket(expId: string | null) {
  const [messages, setMessages] = useState<WSMessage[]>([])
  const [connected, setConnected] = useState(false)
  const wsRef = useRef<WebSocket | null>(null)

  const connect = useCallback(() => {
    if (!expId) return

    const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:'
    const ws = new WebSocket(`${protocol}//${window.location.host}/api/experiments/ws/${expId}/progress`)
    wsRef.current = ws

    ws.onopen = () => setConnected(true)
    ws.onclose = () => setConnected(false)
    ws.onerror = () => setConnected(false)
    ws.onmessage = (event) => {
      try {
        const msg = JSON.parse(event.data) as WSMessage
        setMessages(prev => [...prev, msg])
      } catch { /* ignore */ }
    }
  }, [expId])

  useEffect(() => {
    connect()
    return () => {
      wsRef.current?.close()
    }
  }, [connect])

  const reset = useCallback(() => setMessages([]), [])

  return { messages, connected, reset }
}
