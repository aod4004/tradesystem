import { useEffect, useRef, useCallback } from 'react'
import { WsMessage } from '../types'

/**
 * 실시간 WebSocket 훅.
 *
 * - token null/empty 면 연결 시도 안 함 (만료 JWT 로 403 루프 방지)
 * - 연속 실패 시 지수 백오프: 3s → 6s → 12s → 24s → 30s(max)
 * - 핸드셰이크 직후 즉시 close 가 3회 연속이면 "인증 실패 추정" 으로
 *   onAuthFailure 콜백 호출 후 재시도 중단. 소비자에서 logout 처리.
 *
 * 주의: 브라우저 WebSocket 은 HTTP 403 을 code=1006 close 로만 노출해
 *       토큰 만료와 네트워크 오류를 구분 못 한다. "연결 직후 즉시 종료"
 *       패턴으로 간접 판정.
 */
export function useRealtimeWs(
  onMessage: (msg: WsMessage) => void,
  token: string | null,
  onAuthFailure?: () => void,
) {
  const wsRef = useRef<WebSocket | null>(null)
  const reconnectTimer = useRef<ReturnType<typeof setTimeout>>()
  const attemptRef = useRef(0)        // 연속 실패 횟수 (성공 수신 시 0 리셋)
  const immediateCloseRef = useRef(0) // 핸드셰이크 직후 즉시 close 횟수
  const stoppedRef = useRef(false)    // 인증 실패로 영구 중단
  const connectedAtRef = useRef(0)

  const connect = useCallback(() => {
    if (stoppedRef.current) return
    if (!token) return   // 토큰 없으면 연결 안 함

    const protocol = window.location.protocol === 'https:' ? 'wss' : 'ws'
    const url = `${protocol}://${window.location.host}/ws/realtime?token=${encodeURIComponent(token)}`
    const ws = new WebSocket(url)
    wsRef.current = ws
    connectedAtRef.current = Date.now()

    ws.onopen = () => {
      immediateCloseRef.current = 0
    }

    ws.onmessage = e => {
      try {
        const msg: WsMessage = JSON.parse(e.data)
        attemptRef.current = 0
        onMessage(msg)
      } catch {}
    }

    ws.onclose = () => {
      if (stoppedRef.current) return
      const aliveMs = Date.now() - connectedAtRef.current
      if (aliveMs < 500) {
        // 연결 성공 or 핸드셰이크 전에 바로 끊김 — 인증 실패 추정
        immediateCloseRef.current += 1
      } else {
        immediateCloseRef.current = 0
      }

      if (immediateCloseRef.current >= 3) {
        // 3회 연속 즉시 close → 토큰 무효 판정, 재시도 중단
        stoppedRef.current = true
        if (onAuthFailure) onAuthFailure()
        return
      }

      attemptRef.current += 1
      const delays = [3000, 6000, 12000, 24000, 30000]
      const delay = delays[Math.min(attemptRef.current - 1, delays.length - 1)]
      reconnectTimer.current = setTimeout(connect, delay)
    }

    ws.onerror = () => {
      // close 가 뒤따라오므로 별도 처리 없이 onclose 에 위임
    }
  }, [onMessage, token, onAuthFailure])

  useEffect(() => {
    // token 이 바뀌면 이전 소켓 정리 후 새로 연결 (stopped 도 리셋)
    stoppedRef.current = false
    attemptRef.current = 0
    immediateCloseRef.current = 0
    connect()
    return () => {
      clearTimeout(reconnectTimer.current)
      wsRef.current?.close()
      wsRef.current = null
    }
  }, [connect])
}
