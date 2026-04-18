import { useQuery } from '@tanstack/react-query'
import { fetchBalance } from '../api/client'
import { AccountBalance } from '../types'

export default function AccountSummary() {
  const { data } = useQuery<AccountBalance>({ queryKey: ['balance'], queryFn: fetchBalance, refetchInterval: 10000 })

  const cards = [
    { label: '총 투자금', value: data?.total_investment, color: 'text-gray-200' },
    { label: '평가 금액', value: data?.total_eval_amount, color: 'text-blue-400' },
    { label: '예수금', value: data?.deposit, color: 'text-yellow-400' },
    {
      label: '수익률',
      value: data?.profit_rate !== undefined ? `${data.profit_rate.toFixed(2)}%` : '-',
      color: (data?.profit_rate ?? 0) >= 0 ? 'text-red-400' : 'text-blue-400',
      isPercent: true,
    },
  ]

  return (
    <div className="grid grid-cols-4 gap-4 mb-6">
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
