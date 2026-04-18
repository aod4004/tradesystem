import { useState } from 'react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import AccountSummary from './components/AccountSummary'
import ScreeningTable from './components/ScreeningTable'
import PortfolioTable from './components/PortfolioTable'
import OrderList from './components/OrderList'
import { runScreening } from './api/client'

const qc = new QueryClient()

function Dashboard() {
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
    <div className="min-h-screen bg-gray-900 text-white p-6">
      {/* 헤더 */}
      <div className="flex items-center justify-between mb-6">
        <div>
          <h1 className="text-2xl font-bold text-white">주식 자동매매 시스템</h1>
          <p className="text-gray-400 text-sm mt-1">키움증권 REST API · 낙폭 과대 반등 전략</p>
        </div>
        <div className="flex gap-3">
          <button
            onClick={handleRunScreening}
            disabled={screening}
            className="bg-blue-600 hover:bg-blue-700 disabled:bg-gray-600 text-white px-4 py-2 rounded-lg text-sm font-medium"
          >
            {screening ? '스크리닝 중...' : '수동 스크리닝'}
          </button>
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

export default function App() {
  return (
    <QueryClientProvider client={qc}>
      <Dashboard />
    </QueryClientProvider>
  )
}
