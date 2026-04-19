import { useState, ReactNode } from 'react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { BrowserRouter, Navigate, Route, Routes } from 'react-router-dom'
import AccountSummary from './components/AccountSummary'
import ScreeningTable from './components/ScreeningTable'
import PortfolioTable from './components/PortfolioTable'
import OrderList from './components/OrderList'
import Login from './pages/Login'
import Signup from './pages/Signup'
import { AuthProvider, useAuth } from './context/AuthContext'
import { runScreening } from './api/client'

const qc = new QueryClient()

function Dashboard() {
  const { user, logout } = useAuth()
  const [tab, setTab] = useState<'overview' | 'screening'>('overview')
  const [screening, setScreening] = useState(false)

  const handleRunScreening = async () => {
    setScreening(true)
    try {
      const r = await runScreening()
      alert(`스크리닝 완료: ${r.screened_count}개 종목, 매수신호 ${r.signal_count}건`)
      qc.invalidateQueries()
    } catch (e) {
      alert('스크리닝 오류: ' + String(e))
    } finally {
      setScreening(false)
    }
  }

  return (
    <div className="min-h-screen bg-gray-900 text-white p-4 md:p-6">
      {/* 헤더 */}
      <div className="flex items-start md:items-center justify-between mb-6 gap-3">
        <div>
          <h1 className="text-xl md:text-3xl font-bold bg-gradient-to-r from-blue-400 via-cyan-300 to-emerald-400 bg-clip-text text-transparent tracking-tight">
            MK’s Algorithmic Trading System
          </h1>
          <p className="text-gray-400 text-sm mt-1">Powered by Kiwoom REST API</p>
        </div>
        <div className="flex flex-col md:flex-row gap-2 md:gap-3 items-end">
          <button
            onClick={handleRunScreening}
            disabled={screening}
            className="bg-blue-600 hover:bg-blue-700 disabled:bg-gray-600 text-white px-4 py-2 rounded-lg text-sm font-medium whitespace-nowrap"
          >
            {screening ? '스크리닝 중...' : '수동 스크리닝'}
          </button>
          <div className="flex items-center gap-2 text-xs text-gray-400">
            <span>{user?.email}</span>
            <button onClick={logout} className="text-gray-300 hover:text-white underline">로그아웃</button>
          </div>
        </div>
      </div>

      {/* 탭 */}
      <div className="flex gap-1 mb-6 bg-gray-800 rounded-lg p-1 w-fit">
        {(['overview', 'screening'] as const).map(t => (
          <button
            key={t}
            onClick={() => setTab(t)}
            className={`px-4 py-2 rounded-md text-sm font-medium transition-colors ${
              tab === t ? 'bg-blue-600 text-white' : 'text-gray-400 hover:text-white'
            }`}
          >
            {t === 'overview' ? '포트폴리오' : '스크리닝 종목'}
          </button>
        ))}
      </div>

      {tab === 'overview' ? (
        <>
          <AccountSummary />
          <PortfolioTable />
          <OrderList />
        </>
      ) : (
        <ScreeningTable />
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
            <Route path="/" element={<Protected><Dashboard /></Protected>} />
            <Route path="*" element={<Navigate to="/" replace />} />
          </Routes>
        </AuthProvider>
      </BrowserRouter>
    </QueryClientProvider>
  )
}
