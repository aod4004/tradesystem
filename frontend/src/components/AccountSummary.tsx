import { useQuery } from '@tanstack/react-query'
import { Link } from 'react-router-dom'
import { fetchBalance } from '../api/client'
import { AccountBalance } from '../types'

export default function AccountSummary() {
  const { data, error, isLoading } = useQuery<AccountBalance, any>({
    queryKey: ['balance'],
    queryFn: fetchBalance,
    refetchInterval: 10000,
    retry: (count, err) => {
      // 키 미등록(409) 은 재시도 의미 없음
      if (err?.response?.status === 409) return false
      return count < 2
    },
  })

  const keysMissing = error?.response?.status === 409

  if (keysMissing) {
    return (
      <div className="bg-gray-800 border border-amber-700/50 rounded-xl p-5 mb-6">
        <h3 className="text-amber-400 font-semibold mb-1">키움 API 키가 등록되지 않았습니다</h3>
        <p className="text-sm text-gray-400 mb-3">
          계좌 잔고와 보유 자산을 확인하려면 먼저 키움 App Key / Secret Key 를 등록해주세요.
        </p>
        <Link to="/settings" className="inline-block bg-blue-600 hover:bg-blue-700 rounded px-4 py-2 text-sm font-medium">
          키 등록하러 가기
        </Link>
      </div>
    )
  }

  if (isLoading && !data) {
    return <div className="text-gray-500 text-sm mb-6">잔고 로딩 중…</div>
  }

  const cards = [
    { label: '총 투자금', value: data?.total_investment, color: 'text-gray-200' },
    { label: '총 자산', value: data?.total_asset, color: 'text-blue-400' },
    { label: '예수금', value: data?.deposit, color: 'text-yellow-400' },
    {
      label: '수익률',
      value: data?.profit_rate !== undefined ? `${data.profit_rate.toFixed(2)}%` : '-',
      color: (data?.profit_rate ?? 0) >= 0 ? 'text-red-400' : 'text-blue-400',
      isPercent: true,
    },
  ]

  return (
    <div className="grid grid-cols-2 md:grid-cols-4 gap-4 mb-6">
      {cards.map(c => (
        <div key={c.label} className="bg-gray-800 rounded-xl p-4">
          <p className="text-gray-400 text-sm mb-1">{c.label}</p>
          <p className={`text-xl font-bold ${c.color}`}>
            {c.isPercent
              ? c.value
              : typeof c.value === 'number'
              ? `${c.value.toLocaleString()}원`
              : '-'}
          </p>
        </div>
      ))}
    </div>
  )
}
