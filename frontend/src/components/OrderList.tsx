import { useQuery } from '@tanstack/react-query'
import { fetchTodayOrders, fetchPendingSignals } from '../api/client'
import { OrderRecord, PendingSignal } from '../types'

const STATUS_LABELS: Record<string, string> = {
  pending: '예정', submitted: '접수', filled: '체결', cancelled: '취소',
}
const STATUS_COLORS: Record<string, string> = {
  pending: 'text-yellow-400', submitted: 'text-blue-400',
  filled: 'text-green-400', cancelled: 'text-gray-500',
}

export default function OrderList() {
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

  return (
    <div className="grid grid-cols-2 gap-4 mb-6">
      {/* 내일 예정 매수 신호 */}
      <div className="bg-gray-800 rounded-xl p-4">
        <h2 className="text-lg font-bold text-white mb-3">
          내일 매수 예정 <span className="text-sm text-gray-400">({signals.length}건)</span>
        </h2>
        <table className="w-full text-sm text-gray-300">
          <thead>
            <tr className="text-gray-400 border-b border-gray-700">
              {['종목코드', '차수', '주문가격'].map(h => (
                <th key={h} className="py-1 px-2 text-right first:text-left">{h}</th>
              ))}
            </tr>
          </thead>
          <tbody>
            {signals.map((s, i) => (
              <tr key={i} className="border-b border-gray-700">
                <td className="py-1 px-2 text-white">{s.stock_code}</td>
                <td className="py-1 px-2 text-right text-blue-400">{s.trigger_round}차</td>
                <td className="py-1 px-2 text-right">{s.target_order_price.toLocaleString()}원</td>
              </tr>
            ))}
            {signals.length === 0 && (
              <tr><td colSpan={3} className="text-center py-4 text-gray-500">예정 주문 없음</td></tr>
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
