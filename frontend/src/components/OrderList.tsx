import { Fragment, useMemo, useState } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import {
  approvePendingSignals, fetchMorningApproval,
  fetchPendingSignals, fetchTodayOrders, updatePendingSignal,
} from '../api/client'
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
  const { data: approval } = useQuery({
    queryKey: ['morning-approval'],
    queryFn: fetchMorningApproval,
    refetchInterval: 30000,
  })
  const [approving, setApproving] = useState(false)

  const toggleExclude = async (s: PendingSignal) => {
    try {
      await updatePendingSignal(s.id, !s.is_excluded)
      qc.invalidateQueries({ queryKey: ['signals'] })
    } catch (e) {
      alert('상태 변경 실패: ' + String(e))
    }
  }

  const onApproveAll = async () => {
    const active = signals.filter(s => !s.is_excluded)
    if (active.length === 0) {
      alert('승인할 대기 신호가 없습니다')
      return
    }
    if (!confirm(`${active.length}건의 매수 주문을 지금 전송합니다. 계속할까요?\n제외된 신호는 주문되지 않습니다.`)) return
    setApproving(true)
    try {
      const r = await approvePendingSignals()
      qc.invalidateQueries({ queryKey: ['signals'] })
      qc.invalidateQueries({ queryKey: ['orders'] })
      alert(`주문 ${r.submitted}건 전송 요청 완료`)
    } catch (e: any) {
      alert('승인 실패: ' + String(e?.response?.data?.detail ?? e?.message ?? e))
    } finally {
      setApproving(false)
    }
  }

  const approvalOn = !!approval?.enabled
  const pendingCount = signals.filter(s => !s.is_excluded).length

  // 주문 이력을 KST 날짜별로 그룹핑. orders 가 created_at desc 정렬이라
  // 그룹 순서도 자연스럽게 최신부터. sv-SE locale 은 ISO 형식(YYYY-MM-DD).
  const groupedOrders = useMemo(() => {
    const fmt = new Intl.DateTimeFormat('sv-SE', { timeZone: 'Asia/Seoul' })
    const groups: { date: string; orders: OrderRecord[] }[] = []
    for (const o of orders) {
      const d = fmt.format(new Date(o.created_at))
      const last = groups[groups.length - 1]
      if (!last || last.date !== d) groups.push({ date: d, orders: [o] })
      else last.orders.push(o)
    }
    return groups
  }, [orders])

  return (
    <div className="grid grid-cols-1 md:grid-cols-2 gap-4 mb-6">
      {/* 내일 예정 매수 신호 */}
      <div className="bg-gray-800 rounded-xl p-4">
        <div className="flex items-center justify-between mb-3 gap-2">
          <h2 className="text-lg font-bold text-white">
            내일 매수 예정 <span className="text-sm text-gray-400">({signals.length}건)</span>
            {approvalOn && (
              <span className="ml-2 text-xs px-2 py-0.5 rounded bg-amber-900 text-amber-200 align-middle">
                승인 모드
              </span>
            )}
          </h2>
          {approvalOn && (
            <button
              onClick={onApproveAll}
              disabled={approving || pendingCount === 0}
              className="bg-emerald-600 hover:bg-emerald-700 disabled:bg-gray-600 text-white text-xs px-3 py-1.5 rounded font-medium whitespace-nowrap"
            >
              {approving ? '전송 중...' : `전체 승인 & 주문 (${pendingCount}건)`}
            </button>
          )}
        </div>
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
            <thead className="sticky top-0 z-10 bg-gray-800">
              <tr className="text-gray-400 border-b border-gray-700">
                {['종목', '구분', '차수', '가격', '수량', '상태'].map(h => (
                  <th key={h} className="py-1 px-2 text-right first:text-left">{h}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {groupedOrders.map(g => (
                <Fragment key={g.date}>
                  <tr className="bg-gray-900/70">
                    <td colSpan={6} className="py-1 px-2 text-xs text-gray-400 font-medium">
                      {g.date} <span className="text-gray-500">({g.orders.length}건)</span>
                    </td>
                  </tr>
                  {g.orders.map(o => (
                    <tr key={o.id} className="border-b border-gray-700">
                      <td className="py-1 px-2">
                        <span className="font-medium text-white">{o.stock_name || o.stock_code}</span>
                        <span className="ml-1 text-xs text-gray-500">{o.stock_code}</span>
                      </td>
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
                </Fragment>
              ))}
              {orders.length === 0 && (
                <tr><td colSpan={6} className="text-center py-4 text-gray-500">주문 이력 없음</td></tr>
              )}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  )
}
