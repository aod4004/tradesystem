import { useEffect, useRef, useState, ReactNode } from 'react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { BrowserRouter, Link, Navigate, Route, Routes } from 'react-router-dom'
import AccountSummary from './components/AccountSummary'
import ScreeningTable from './components/ScreeningTable'
import PortfolioTable from './components/PortfolioTable'
import OrderList from './components/OrderList'
import Watchlist from './components/Watchlist'
import Login from './pages/Login'
import Signup from './pages/Signup'
import Settings from './pages/Settings'
import { AuthProvider, useAuth } from './context/AuthContext'
import { runScreening, fetchScreeningStatus, ScreeningStatus } from './api/client'

const qc = new QueryClient()

function Dashboard() {
  const { user, logout } = useAuth()
  const [tab, setTab] = useState<'overview' | 'screening' | 'watchlist'>('overview')
  const [status, setStatus] = useState<ScreeningStatus | null>(null)
  const pollRef = useRef<number | null>(null)
  const lastSeenStatus = useRef<ScreeningStatus['status'] | null>(null)

  const stopPolling = () => {
    if (pollRef.current !== null) {
      window.clearInterval(pollRef.current)
      pollRef.current = null
    }
  }

  const startPolling = () => {
    stopPolling()
    pollRef.current = window.setInterval(async () => {
      try {
        const s = await fetchScreeningStatus()
        setStatus(s)
        if (s.status !== 'running') {
          stopPolling()
          if (lastSeenStatus.current === 'running') {
            // 방금 끝났을 때만 알림/리프레시
            if (s.status === 'completed') {
              qc.invalidateQueries()
              alert(`스크리닝 완료: ${s.selected}개 종목, 매수신호 ${s.signal_count ?? 0}건`)
            } else if (s.status === 'error') {
              alert('스크리닝 오류: ' + (s.error ?? 'unknown'))
            }
          }
        }
        lastSeenStatus.current = s.status
      } catch (e) {
        console.error('스크리닝 상태 조회 실패', e)
      }
    }, 2000)
  }

  // 최초 진입 시 이미 돌고 있으면 이어받기
  useEffect(() => {
    fetchScreeningStatus()
      .then(s => {
        setStatus(s)
        lastSeenStatus.current = s.status
        if (s.status === 'running') startPolling()
      })
      .catch(() => { /* admin 아니면 403 — 무시 */ })
    return stopPolling
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  const handleRunScreening = async () => {
    try {
      const s = await runScreening()
      setStatus(s)
      lastSeenStatus.current = s.status
      startPolling()
    } catch (e) {
      alert('스크리닝 시작 오류: ' + String(e))
    }
  }

  const running = status?.status === 'running'
  const progressPct =
    running && status && status.total > 0
      ? Math.min(100, Math.floor((status.processed / status.total) * 100))
      : 0

  return (
    <div className="min-h-screen bg-gray-900 text-white p-4 md:p-6">
      {/* 헤더 */}
      <div className="flex items-start md:items-center justify-between mb-6 gap-3">
        <div>
          <h1
            className="text-xl md:text-3xl font-bold bg-gradient-to-r from-blue-400 via-cyan-300 to-emerald-400 bg-clip-text text-transparent tracking-tight"
            style={{ paddingBottom: '5pt' }}
          >
            5P’s Algorithmic Trading System
          </h1>
          <p className="text-gray-400 text-sm mt-1">Powered by Kiwoom REST API</p>
        </div>
        <div className="flex flex-col md:flex-row gap-2 md:gap-3 items-end">
          <div className="flex flex-col items-end gap-1">
            <button
              onClick={handleRunScreening}
              disabled={running}
              className="bg-blue-600 hover:bg-blue-700 disabled:bg-gray-600 text-white px-4 py-2 rounded-lg text-sm font-medium whitespace-nowrap"
            >
              {running
                ? `스크리닝 중 ${status!.processed}/${status!.total || '?'} (${progressPct}%)`
                : '수동 스크리닝'}
            </button>
            {running && (
              <div className="w-full h-1 bg-gray-700 rounded overflow-hidden">
                <div
                  className="h-full bg-blue-500 transition-all"
                  style={{ width: `${progressPct}%` }}
                />
              </div>
            )}
          </div>
          <div className="flex items-center gap-2 text-xs text-gray-400">
            <span>{user?.email}</span>
            <Link to="/settings" className="text-gray-300 hover:text-white underline">설정</Link>
            <button onClick={logout} className="text-gray-300 hover:text-white underline">로그아웃</button>
          </div>
        </div>
      </div>

      {/* 탭 */}
      <div className="flex gap-1 mb-6 bg-gray-800 rounded-lg p-1 w-fit">
        {(['overview', 'screening', 'watchlist'] as const).map(t => (
          <button
            key={t}
            onClick={() => setTab(t)}
            className={`px-4 py-2 rounded-md text-sm font-medium transition-colors ${
              tab === t ? 'bg-blue-600 text-white' : 'text-gray-400 hover:text-white'
            }`}
          >
            {t === 'overview' ? '포트폴리오' : t === 'screening' ? '스크리닝 종목' : '관심 종목'}
          </button>
        ))}
      </div>

      {tab === 'overview' ? (
        <>
          <AccountSummary />
          <PortfolioTable />
          <OrderList />
        </>
      ) : tab === 'screening' ? (
        <ScreeningTable />
      ) : (
        <Watchlist />
      )}
    </div>
  )
}

function Protected({ children }: { children: ReactNode }) {
  const { user, token, loading } = useAuth()
  if (loading) {
    return <div className="min-h-screen bg-gray-900 text-gray-500 flex items-center justify-center">Loading…</div>
  }
  if (!token || !user) {
    return <Navigate to="/login" replace />
  }
  return <>{children}</>
}

export default function App() {
  return (
    <QueryClientProvider client={qc}>
      <BrowserRouter>
        <AuthProvider>
          <Routes>
            <Route path="/login" element={<Login />} />
            <Route path="/signup" element={<Signup />} />
            <Route path="/settings" element={<Protected><Settings /></Protected>} />
            <Route path="/" element={<Protected><Dashboard /></Protected>} />
            <Route path="*" element={<Navigate to="/" replace />} />
          </Routes>
        </AuthProvider>
      </BrowserRouter>
    </QueryClientProvider>
  )
}
