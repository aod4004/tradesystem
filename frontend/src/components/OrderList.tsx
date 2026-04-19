import { useQuery, useQueryClient } from '@tanstack/react-query'
import { fetchTodayOrders, fetchPendingSignals, updatePendingSignal } from '../api/client'
import { OrderRecord, PendingSignal } from '../types'

const STATUS_LABELS: Record<string, string> = {
  pending: '예정', submitted: '접수', filled: '체결', cancelled: '취소',
}
const STATUS_COLORS: Record<string, string> = {
  pending: 'text-yellow-400', submitted: 'text-blue-400',
  filled: 'text-green-400', cancelled: 'text-gray-500',
}

export default function OrderList() {
  const qc = useQueryClient()
  const { data: orders = [] } = useQuery<OrderRecord[]>({
    queryKey: ['orders'],
    queryFn: fetchTodayOrders,
    refetchInterval: 5000,
  })
  const { data: signals = [] } = useQuery<PendingSignal[]>({
    queryKey: ['signals'],
    queryFn: fetchPendingSignals,
    refetchInterval: 10000,
  })

  const toggleExclude = async (s: PendingSignal) => {
    try {
      await updatePendingSignal(s.id, !s.is_excluded)
      qc.invalidateQueries({ queryKey: ['signals'] })
    } catch (e) {
      alert('상태 변경 실패: ' + String(e))
    }
  }

  return (
    <div className="grid grid-cols-1 md:grid-cols-2 gap-4 mb-6">
      {/* 내일 예정 매수 신호 */}
      <div className="bg-gray-800 rounded-xl p-4">
        <h2 className="text-lg font-bold text-white mb-3">
          내일 매수 예정 <span className="text-sm text-gray-400">({signals.length}건)</span>
        </h2>
        <table className="w-full text-sm text-gray-300">
          <thead>
            <tr className="text-gray-400 border-b border-gray-700">
              <th className="py-1 px-2 text-left">종목명</th>
              <th className="py-1 px-2 text-right">차수</th>
              <th className="py-1 px-2 text-right">주문가격</th>
              <th className="py-1 px-2 text-right hidden md:table-cell">수량</th>
              <th className="py-1 px-2 text-right hidden md:table-cell">금액</th>
              <th className="py-1 px-2 text-right hidden md:table-cell">비율</th>
              <th className="py-1 px-2 text-right">제외</th>
            </tr>
          </thead>
          <tbody>
            {signals.map(s => (
              <tr
                key={s.id}
                className={`border-b border-gray-700 ${s.is_excluded ? 'opacity-40 line-through' : ''}`}
              >
                <td className="py-1 px-2">
                  <span className="font-medium text-white">{s.stock_name || s.stock_code}</span>
                  <span className="ml-1 text-xs text-gray-500">{s.stock_code}</span>
                  {s.source === 'watchlist' && (
                    <span className="ml-1 text-[10px] px-1 py-0.5 rounded bg-purple-900 text-purple-200">관심</span>
                  )}
                  <div className="md:hidden text-xs text-gray-500 mt-0.5">
                    {s.quantity.toLocaleString()}주 · {s.amount.toLocaleString()}원 · <span className="text-yellow-400">{s.investment_ratio.toFixed(2)}%</span>
                  </div>
                </td>
                <td className="py-1 px-2 text-right text-blue-400">{s.trigger_round}차</td>
                <td className="py-1 px-2 text-right">{s.target_order_price.toLocaleString()}</td>
                <td className="py-1 px-2 text-right hidden md:table-cell">{s.quantity.toLocaleString()}</td>
                <td className="py-1 px-2 text-right hidden md:table-cell">{s.amount.toLocaleString()}</td>
                <td className="py-1 px-2 text-right hidden md:table-cell text-yellow-400">{s.investment_ratio.toFixed(2)}%</td>
                <td className="py-1 px-2 text-right">
                  <button
                    onClick={() => toggleExclude(s)}
                    className={`text-xs px-2 py-0.5 rounded ${
                      s.is_excluded
                        ? 'bg-gray-700 hover:bg-gray-600 text-gray-300'
                        : 'bg-red-900 hover:bg-red-800 text-red-200'
                    }`}
                  >
                    {s.is_excluded ? '복구' : '제외'}
                  </button>
                </td>
              </tr>
            ))}
            {signals.length === 0 && (
              <tr><td colSpan={7} className="text-center py-4 text-gray-500">예정 주문 없음</td></tr>
            )}
          </tbody>
        </table>
      </div>

      {/* 주문 이력 */}
      <div className="bg-gray-800 rounded-xl p-4">
        <h2 className="text-lg font-bold text-white mb-3">주문 이력</h2>
        <div className="overflow-y-auto max-h-64">
          <table className="w-full text-sm text-gray-300">
            <thead>
              <tr className="text-gray-400 border-b border-gray-700">
                {['종목', '구분', '차수', '가격', '수량', '상태'].map(h => (
                  <th key={h} className="py-1 px-2 text-right first:text-left">{h}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {orders.map(o => (
                <tr key={o.id} className="border-b border-gray-700">
                  <td className="py-1 px-2 text-white">{o.stock_code}</td>
                  <td className={`py-1 px-2 text-right font-bold ${o.order_type === 'buy' ? 'text-red-400' : 'text-blue-400'}`}>
                    {o.order_type === 'buy' ? '매수' : '매도'}
                  </td>
                  <td className="py-1 px-2 text-right">{o.order_round}차</td>
                  <td className="py-1 px-2 text-right">{o.order_price.toLocaleString()}</td>
                  <td className="py-1 px-2 text-right">{o.order_qty}</td>
                  <td className={`py-1 px-2 text-right ${STATUS_COLORS[o.status] ?? ''}`}>
                    {STATUS_LABELS[o.status] ?? o.status}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  )
}
