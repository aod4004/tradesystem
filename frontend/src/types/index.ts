export interface ScreenedStock {
  code: string
  name: string
  market: string
  current_price: number
  high_1y: number
  low_1y: number
  drop_from_high: number      // 고점 대비 하락률 (%)
  rise_from_low: number       // 저점 대비 상승 배수
  market_cap: number
  net_income: number          // 순이익 (억원)
  operating_income: number    // 영업이익 (억원)
  foreign_ratio: number       // 외국인 비율 (%)
  screened_at: string
}

export interface Position {
  id: number
  stock_code: string
  stock_name: string
  quantity: number
  avg_buy_price: number
  buy_rounds_done: number
  sell_rounds_done: number
  status: string
  current_price?: number      // 실시간 주입
  eval_profit_loss?: number   // 평가 손익
  profit_rate?: number        // 수익률 %
}

export interface PendingSignal {
  id: number
  stock_code: string
  stock_name: string
  source: 'screening' | 'watchlist'
  trigger_round: number
  target_order_price: number
  quantity: number
  amount: number
  investment_ratio: number   // 총 투자금 대비 %
  signal_date: string
  is_executed?: boolean
  is_excluded: boolean
}

export interface WatchlistItem {
  stock_code: string
  stock_name: string
  added_at: string
}

export interface OrderRecord {
  id: number
  stock_code: string
  stock_name: string
  order_type: 'buy' | 'sell'
  order_round: number
  order_price: number
  order_qty: number
  filled_price: number | null
  filled_qty: number
  status: string
  created_at: string
}

export interface AccountBalance {
  total_investment: number
  total_asset: number               // 총자산 (평가금+예수금+대용금)
  total_eval_amount: number         // 보유 종목 평가금액만
  total_purchase_amount: number
  total_profit_loss: number
  total_profit_rate: number
  deposit: number
  order_available: number
  profit_rate: number               // 총자산 기준
  holdings: Holding[]
}

export interface Holding {
  code: string
  name: string
  quantity: number
  avg_price: number
  current_price: number
  eval_profit_loss: number
  profit_rate: number
}

export type WsMessageType =
  | 'price_update'
  | 'sell_signal'
  | 'order_event'
  | 'balance_event'
  | 'extra_buy_signal'

export interface WsMessage {
  type: WsMessageType
  data: Record<string, unknown>
}
