import { FormEvent, useState } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { addWatchlist, fetchWatchlist, removeWatchlist } from '../api/client'
import { WatchlistItem } from '../types'

export default function Watchlist() {
  const qc = useQueryClient()
  const { data: items = [], isLoading } = useQuery<WatchlistItem[]>({
    queryKey: ['watchlist'],
    queryFn: fetchWatchlist,
  })

  const [code, setCode] = useState('')
  const [submitting, setSubmitting] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const handleAdd = async (e: FormEvent) => {
    e.preventDefault()
    if (!code.trim()) return
    setSubmitting(true)
    setError(null)
    try {
      await addWatchlist(code.trim())
      setCode('')
      qc.invalidateQueries({ queryKey: ['watchlist'] })
    } catch (e: unknown) {
      const err = e as { response?: { data?: { detail?: string } }; message?: string }
      setError(err?.response?.data?.detail ?? err?.message ?? String(e))
    } finally {
      setSubmitting(false)
    }
  }

  const handleRemove = async (stockCode: string) => {
    if (!confirm(`${stockCode} 을(를) 관심종목에서 제거하시겠습니까?`)) return
    try {
      await removeWatchlist(stockCode)
      qc.invalidateQueries({ queryKey: ['watchlist'] })
    } catch (e) {
      alert('삭제 실패: ' + String(e))
    }
  }

  return (
    <div className="bg-gray-800 rounded-xl p-4">
      <h2 className="text-lg font-bold text-white mb-3">
        관심 종목 <span className="text-sm text-gray-400">({items.length}개)</span>
      </h2>
      <p className="text-xs text-gray-400 mb-3">
        여기에 등록한 종목은 스크리닝 후보와 함께 매수 조건(양봉 + 고점 50% ↓ / 전 차수 90% ↓)을 평가받아 자동 주문됩니다.
      </p>

      <form onSubmit={handleAdd} className="flex gap-2 mb-4">
        <input
          type="text"
          value={code}
          onChange={e => setCode(e.target.value)}
          placeholder="종목코드 (예: 005930)"
          className="flex-1 bg-gray-900 border border-gray-700 rounded px-3 py-2 text-sm text-white focus:outline-none focus:border-blue-500"
          disabled={submitting}
        />
        <button
          type="submit"
          disabled={submitting || !code.trim()}
          className="bg-blue-600 hover:bg-blue-700 disabled:bg-gray-600 text-white px-4 py-2 rounded text-sm font-medium whitespace-nowrap"
        >
          {submitting ? '추가 중...' : '추가'}
        </button>
      </form>
      {error && <p className="text-xs text-red-400 mb-3">{error}</p>}

      {isLoading ? (
        <p className="text-gray-500 text-sm">로딩 중…</p>
      ) : items.length === 0 ? (
        <p className="text-gray-500 text-sm">등록된 관심종목이 없습니다.</p>
      ) : (
        <table className="w-full text-sm text-gray-300">
          <thead>
            <tr className="text-gray-400 border-b border-gray-700">
              <th className="py-1 px-2 text-left">종목명</th>
              <th className="py-1 px-2 text-left">코드</th>
              <th className="py-1 px-2 text-left hidden md:table-cell">등록일</th>
              <th className="py-1 px-2 text-right">관리</th>
            </tr>
          </thead>
          <tbody>
            {items.map(w => (
              <tr key={w.stock_code} className="border-b border-gray-700">
                <td className="py-1 px-2 font-medium text-white">{w.stock_name}</td>
                <td className="py-1 px-2 text-gray-400">{w.stock_code}</td>
                <td className="py-1 px-2 text-gray-500 text-xs hidden md:table-cell">
                  {new Date(w.added_at).toLocaleDateString()}
                </td>
                <td className="py-1 px-2 text-right">
                  <button
                    onClick={() => handleRemove(w.stock_code)}
                    className="text-xs px-2 py-0.5 rounded bg-red-900 hover:bg-red-800 text-red-200"
                  >
                    제거
                  </button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </div>
  )
}
