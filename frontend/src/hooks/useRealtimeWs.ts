import { useEffect, useRef, useCallback } from 'react'
import { WsMessage } from '../types'

export function useRealtimeWs(onMessage: (msg: WsMessage) => void) {
  const wsRef = useRef<WebSocket | null>(null)
  const reconnectTimer = useRef<ReturnType<typeof setTimeout>>()

  const connect = useCallback(() => {
    const protocol = window.location.protocol === 'https:' ? 'wss' : 'ws'
    const token = localStorage.getItem('mk_auth_token') ?? ''
    const q = token ? `?token=${encodeURIComponent(token)}` : ''
    const ws = new WebSocket(`${protocol}://${window.location.host}/ws/realtime${q}`)
    wsRef.current = ws

    ws.onmessage = e => {
      try {
        const msg: WsMessage = JSON.parse(e.data)
        onMessage(msg)
      } catch {}
    }

    ws.onclose = () => {
      reconnectTimer.current = setTimeout(connect, 3000)
    }
  }, [onMessage])

  useEffect(() => {
    connect()
    return () => {
      clearTimeout(reconnectTimer.current)
      wsRef.current?.close()
    }
  }, [connect])
}
