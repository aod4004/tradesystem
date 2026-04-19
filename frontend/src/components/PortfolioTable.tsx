import { useState, useCallback } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { fetchPositions } from '../api/client'
import { Position, WsMessage } from '../types'
import { useRealtimeWs } from '../hooks/useRealtimeWs'

export default function PortfolioTable() {
  const qc = useQueryClient()
  const { data: positions = [] } = useQuery<Position[]>({
    queryKey: ['positions'],
    queryFn: fetchPositions,
    refetchInterval: 5000,
  })

  // 실시간 가격 상태
  const [prices, setPrices] = useState<Record<string, number>>({})

  const handleWsMsg = useCallback((msg: WsMessage) => {
    if (msg.type === 'price_update') {
      const d = msg.data as { code: string; current_price: number }
      setPrices(prev => ({ ...prev, [d.code]: d.current_price }))
      return
    }
    if (
      msg.type === 'sell_signal' ||
      msg.type === 'order_event' ||
      msg.type === 'balance_event' ||
      msg.type === 'extra_buy_signal'
    ) {
      qc.invalidateQueries({ queryKey: ['positions'] })
      qc.invalidateQueries({ queryKey: ['orders'] })
      qc.invalidateQueries({ queryKey: ['balance'] })
    }
  }, [qc])

  useRealtimeWs(handleWsMsg)

  const SELL_LABELS = ['5%', '10%', '15%', '20%', 'MA20']

  return (
    <div className="bg-gray-800 rounded-xl p-4 mb-6">
      <h2 className="text-lg font-bold text-white mb-3">
        보유 종목 <span className="text-sm text-gray-400">({positions.length}개)</span>
      </h2>
      <div className="overflow-x-auto">
        <table className="w-full text-sm text-gray-300">
          <thead>
            <tr className="text-gray-400 border-b border-gray-700">
              {['종목명', '수량', '평균매입가', '현재가', '평가손익', '수익률', '매수차수', '매도차수', '다음매도조건'].map(h => (
                <th key={h} className="py-2 px-2 text-right first:text-left">{h}</th>
              ))}
            </tr>
          </thead>
          <tbody>
            {positions.map(p => {
              const cp = prices[p.stock_code] ?? 0
              const pnl = cp > 0 ? (cp - p.avg_buy_price) * p.quantity : 0
              const pnlRate = cp > 0 ? (cp - p.avg_buy_price) / p.avg_buy_price * 100 : 0
              const nextSell = p.sell_rounds_done < 5 ? SELL_LABELS[p.sell_rounds_done] : '완료'
              return (
                <tr key={p.id} className="border-b border-gray-700 hover:bg-gray-700">
                  <td className="py-2 px-2">
                    <span className="font-medium text-white">{p.stock_name || p.stock_code}</span>
                    <span className="ml-1 text-xs text-gray-500">{p.stock_code}</span>
                  </td>
                  <td className="py-2 px-2 text-right">{p.quantity.toLocaleString()}</td>
                  <td className="py-2 px-2 text-right">{Math.round(p.avg_buy_price).toLocaleString()}</td>
                  <td className="py-2 px-2 text-right">{cp > 0 ? cp.toLocaleString() : '-'}</td>
                  <td className={`py-2 px-2 text-right font-bold ${pnl >= 0 ? 'text-red-400' : 'text-blue-400'}`}>
                    {cp > 0 ? `${pnl >= 0 ? '+' : ''}${Math.round(pnl).toLocaleString()}` : '-'}
                  </td>
                  <td className={`py-2 px-2 text-right font-bold ${pnlRate >= 0 ? 'text-red-400' : 'text-blue-400'}`}>
                    {cp > 0 ? `${pnlRate >= 0 ? '+' : ''}${pnlRate.toFixed(2)}%` : '-'}
                  </td>
                  <td className="py-2 px-2 text-center">
                    <span className="bg-blue-900 text-blue-300 px-2 py-0.5 rounded text-xs">{p.buy_rounds_done}차</span>
                  </td>
                  <td className="py-2 px-2 text-center">
                    <span className="bg-green-900 text-green-300 px-2 py-0.5 rounded text-xs">{p.sell_rounds_done}차</span>
                  </td>
                  <td className="py-2 px-2 text-center text-yellow-400 text-xs font-bold">{nextSell}</td>
                </tr>
              )
            })}
            {positions.length === 0 && (
              <tr><td colSpan={9} className="text-center py-6 text-gray-500">보유 종목 없음</td></tr>
            )}
          </tbody>
        </table>
      </div>
    </div>
  )
}
