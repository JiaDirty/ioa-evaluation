import { useEffect, useRef, useState } from 'react'
import type { TaskEvent } from '../types'

export function useTaskStream(taskId: string | null) {
  const [events, setEvents] = useState<TaskEvent[]>([])
  const [status, setStatus] = useState('idle')
  const [connected, setConnected] = useState(false)
  const lastSequence = useRef(0)
  const terminal = useRef(false)

  useEffect(() => {
    setEvents([])
    setStatus(taskId ? 'connecting' : 'idle')
    lastSequence.current = 0
    terminal.current = false
    if (!taskId) return
    let closed = false
    let retryTimer: number | undefined
    let socket: WebSocket | null = null

    const connect = () => {
      if (closed) return
      const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:'
      socket = new WebSocket(
        `${protocol}//${window.location.host}/api/tasks/${encodeURIComponent(taskId)}/stream?after_sequence=${lastSequence.current}`,
      )
      socket.onopen = () => setConnected(true)
      socket.onmessage = message => {
        const data = JSON.parse(message.data) as {
          type: string
          event?: TaskEvent
          status?: string
          last_sequence?: number
        }
        if (data.type === 'event' && data.event) {
          lastSequence.current = Math.max(lastSequence.current, data.event.sequence || 0)
          setEvents(previous => previous.some(item => item.event_id === data.event!.event_id)
            ? previous
            : [...previous, data.event!])
        }
        if (data.status) {
          setStatus(data.status)
          terminal.current = ['completed', 'failed', 'cancelled'].includes(data.status)
        }
        if (data.last_sequence) lastSequence.current = Math.max(lastSequence.current, data.last_sequence)
      }
      socket.onclose = () => {
        setConnected(false)
        if (!closed && !terminal.current) {
          retryTimer = window.setTimeout(connect, 700)
        }
      }
      socket.onerror = () => socket?.close()
    }

    connect()
    return () => {
      closed = true
      if (retryTimer) window.clearTimeout(retryTimer)
      socket?.close()
    }
  }, [taskId])

  return { events, status, connected, lastSequence: lastSequence.current }
}
