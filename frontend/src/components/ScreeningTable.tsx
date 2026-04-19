import { useQuery } from '@tanstack/react-query'
import { fetchScreenedStocks } from '../api/client'
import { ScreenedStock } from '../types'

export default function ScreeningTable() {
  const { data = [], isLoading } = useQuery<ScreenedStock[]>({
    queryKey: ['screened'],
    queryFn: fetchScreenedStocks,
    refetchInterval: 60000,
  })

  if (isLoading) return <p className="text-gray-400">로딩 중...</p>

  return (
    <div className="bg-gray-800 rounded-xl p-4 mb-6">
      <h2 className="text-lg font-bold text-white mb-3">
        스크리닝 종목 <span className="text-sm text-gray-400">({data.length}개)</span>
      </h2>
      <div className="overflow-x-auto">
        <table className="w-full text-sm text-gray-300">
          <thead>
            <tr className="text-gray-400 border-b border-gray-700">
              {['종목명', '현재가', '고점', '고점대비%', '저점상승배수', '순이익(억)', '영업이익(억)', '외국인%', '시가총액(억)'].map(h => (
                <th key={h} className="py-2 px-2 text-right first:text-left">{h}</th>
              ))}
            </tr>
          </thead>
          <tbody>
            {data.map(s => (
              <tr key={s.code} className="border-b border-gray-700 hover:bg-gray-700">
                <td className="py-2 px-2">
                  <span className="font-medium text-white">{s.name}</span>
                  <span className="ml-1 text-xs text-gray-500">{s.code}</span>
                </td>
                <td className="py-2 px-2 text-right">{s.current_price.toLocaleString()}</td>
                <td className="py-2 px-2 text-right">{s.high_1y.toLocaleString()}</td>
                <td className="py-2 px-2 text-right text-blue-400 font-bold">-{s.drop_from_high.toFixed(1)}%</td>
                <td className="py-2 px-2 text-right">{s.rise_from_low.toFixed(2)}x</td>
                <td className="py-2 px-2 text-right">{Math.round(s.net_income).toLocaleString()}</td>
                <td className="py-2 px-2 text-right">{Math.round(s.operating_income).toLocaleString()}</td>
                <td className="py-2 px-2 text-right">{s.foreign_ratio.toFixed(1)}%</td>
                <td className="py-2 px-2 text-right">{Math.round(s.market_cap / 100_000_000).toLocaleString()}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  )
}
