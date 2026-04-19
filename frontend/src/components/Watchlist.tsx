import { FormEvent, useState } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { addWatchlist, fetchWatchlist, removeWatchlist } from '../api/client'
import { WatchlistItem } from '../types'

export default function Watchlist() {
  const qc = useQueryClient()
  const { data: items = [], isLoading, isFetching } = useQuery<WatchlistItem[]>({
    queryKey: ['watchlist'],
    queryFn: fetchWatchlist,
    // 탭 전환으로 컴포넌트가 다시 마운트될 때마다 새로 조회
    refetchOnMount: 'always',
    staleTime: 0,
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
      <div className="flex items-center justify-between mb-3">
        <h2 className="text-lg font-bold text-white">
          관심 종목 <span className="text-sm text-gray-400">({items.length}개)</span>
          {isFetching && <span className="text-xs text-gray-500 ml-2">갱신 중…</span>}
        </h2>
        <button
          onClick={() => qc.invalidateQueries({ queryKey: ['watchlist'] })}
          disabled={isFetching}
          className="text-xs px-2 py-1 rounded bg-gray-700 hover:bg-gray-600 text-gray-200 disabled:opacity-50"
        >
          새로고침
        </button>
      </div>
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
        <div className="overflow-x-auto">
          <table className="w-full text-sm text-gray-300">
            <thead>
              <tr className="text-gray-400 border-b border-gray-700">
                {['종목명', '현재가', '고점', '고점대비%', '저점상승배수', '순이익(억)', '영업이익(억)', '외국인%', '시가총액(억)', '등록일', ''].map(h => (
                  <th key={h} className="py-2 px-2 text-right first:text-left">{h}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {items.map(w => (
                <tr key={w.stock_code} className="border-b border-gray-700 hover:bg-gray-700">
                  <td className="py-2 px-2">
                    <span className="font-medium text-white">{w.stock_name}</span>
                    <span className="ml-1 text-xs text-gray-500">{w.stock_code}</span>
                    {w.error && (
                      <div className="text-[10px] text-red-400 mt-0.5" title={w.error}>
                        조회 실패
                      </div>
                    )}
                  </td>
                  <td className="py-2 px-2 text-right">
                    {w.current_price > 0 ? w.current_price.toLocaleString() : '—'}
                  </td>
                  <td className="py-2 px-2 text-right">
                    {w.high_1y > 0 ? w.high_1y.toLocaleString() : '—'}
                  </td>
                  <td className="py-2 px-2 text-right text-blue-400 font-bold">
                    {w.drop_from_high > 0 ? `-${w.drop_from_high.toFixed(1)}%` : '—'}
                  </td>
                  <td className="py-2 px-2 text-right">
                    {w.rise_from_low > 0 ? `${w.rise_from_low.toFixed(2)}x` : '—'}
                  </td>
                  <td className="py-2 px-2 text-right">{w.net_income.toFixed(0)}</td>
                  <td className="py-2 px-2 text-right">{w.operating_income.toFixed(0)}</td>
                  <td className="py-2 px-2 text-right">{w.foreign_ratio.toFixed(1)}%</td>
                  <td className="py-2 px-2 text-right">
                    {w.market_cap > 0 ? (w.market_cap / 100_000_000).toFixed(0) : '—'}
                  </td>
                  <td className="py-2 px-2 text-right text-gray-500 text-xs">
                    {new Date(w.added_at).toLocaleDateString()}
                  </td>
                  <td className="py-2 px-2 text-right">
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
        </div>
      )}
    </div>
  )
}
