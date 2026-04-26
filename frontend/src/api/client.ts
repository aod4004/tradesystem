import axios from 'axios'
import type { WatchlistItem } from '../types'

const TOKEN_KEY = 'mk_auth_token'

const api = axios.create({ baseURL: '/api' })

api.interceptors.request.use(cfg => {
  const token = localStorage.getItem(TOKEN_KEY)
  if (token) {
    cfg.headers = cfg.headers ?? {}
    ;(cfg.headers as Record<string, string>)['Authorization'] = `Bearer ${token}`
  }
  return cfg
})

api.interceptors.response.use(
  r => r,
  err => {
    if (err?.response?.status === 401) {
      localStorage.removeItem(TOKEN_KEY)
      // 이미 로그인/회원가입 화면이 아니면 이동
      if (!['/login', '/signup'].includes(window.location.pathname)) {
        window.location.href = '/login'
      }
    }
    return Promise.reject(err)
  },
)

export const fetchDashboard = () => api.get('/dashboard').then(r => r.data)
export const fetchBalance = () => api.get('/account/balance').then(r => r.data)
export const fetchScreenedStocks = () => api.get('/dashboard/screened-stocks').then(r => r.data)
export const fetchPositions = () => api.get('/dashboard/positions').then(r => r.data)
export const reconcilePositions = () =>
  api.post('/account/reconcile-positions').then(r => r.data as {
    created: number
    updated: number
    closed: number
    total_holdings: number
  })
export const fetchTodayOrders = () => api.get('/orders/today').then(r => r.data)
export const fetchPendingSignals = () => api.get('/orders/pending-signals').then(r => r.data)
export interface ScreeningStatus {
  status: 'idle' | 'running' | 'completed' | 'error'
  started_at: string | null
  finished_at: string | null
  total: number
  processed: number
  selected: number
  signal_count: number | null
  user_count: number | null
  error: string | null
}

export const runScreening = () =>
  api.post<ScreeningStatus>('/orders/run-screening').then(r => r.data)

export const fetchScreeningStatus = () =>
  api.get<ScreeningStatus>('/orders/run-screening/status').then(r => r.data)
export const placeManualOrder = (body: {
  stock_code: string; order_type: string; quantity: number; price: number
}) => api.post('/orders/manual', body).then(r => r.data)

// 설정
export interface KiwoomKeysStatus {
  has_keys: boolean
  mock: boolean
  total_investment: number
  ws_permanently_stopped?: boolean
}

export const fetchKiwoomStatus = () =>
  api.get<KiwoomKeysStatus>('/settings/kiwoom-keys').then(r => r.data)

export const saveKiwoomKeys = (body: {
  app_key: string; secret_key: string; mock: boolean; total_investment?: number
}) => api.put<KiwoomKeysStatus>('/settings/kiwoom-keys', body).then(r => r.data)

export const deleteKiwoomKeys = () =>
  api.delete<KiwoomKeysStatus>('/settings/kiwoom-keys').then(r => r.data)

// 회원 탈퇴
export const deleteAccount = (password: string) =>
  api.delete('/auth/me', { data: { password } }).then(r => r.data)

// 런타임 리스크 가드 (Phase 4)
export interface RiskGuardsStatus {
  enabled: boolean
  daily_order_amount_limit: number | null
  daily_order_count_limit: number | null
  max_position_ratio: number | null
  default_max_position_ratio: number
}

export interface RiskGuardsPayload {
  enabled?: boolean
  daily_order_amount_limit?: number | null
  daily_order_count_limit?: number | null
  max_position_ratio?: number | null
  clear_amount?: boolean
  clear_count?: boolean
  clear_ratio?: boolean
}

export const fetchRiskGuards = () =>
  api.get<RiskGuardsStatus>('/settings/risk-guards').then(r => r.data)

export const saveRiskGuards = (body: RiskGuardsPayload) =>
  api.patch<RiskGuardsStatus>('/settings/risk-guards', body).then(r => r.data)

// 장 시작 전 사전 승인 모드 (Phase 4.2)
export interface MorningApprovalStatus { enabled: boolean }

export const fetchMorningApproval = () =>
  api.get<MorningApprovalStatus>('/settings/morning-approval').then(r => r.data)

export const setMorningApproval = (enabled: boolean) =>
  api.patch<MorningApprovalStatus>('/settings/morning-approval', { enabled }).then(r => r.data)

export const approvePendingSignals = () =>
  api.post<{ ok: boolean; submitted: number; message?: string }>('/orders/approve-pending').then(r => r.data)

// 카카오 알림 연동
export interface KakaoStatus {
  configured: boolean
  connected: boolean
  notifications_enabled: boolean
  access_expires_at: string | null
  refresh_expires_at: string | null
}

export const fetchKakaoStatus = () =>
  api.get<KakaoStatus>('/settings/kakao').then(r => r.data)

export const fetchKakaoAuthorizeUrl = () =>
  api.get<{ url: string }>('/settings/kakao/authorize-url').then(r => r.data)

export const sendKakaoTest = () =>
  api.post<{ ok: boolean }>('/settings/kakao/test').then(r => r.data)

export const setKakaoEnabled = (enabled: boolean) =>
  api.patch<KakaoStatus>('/settings/kakao/enabled', { enabled }).then(r => r.data)

export const disconnectKakao = () =>
  api.delete<KakaoStatus>('/settings/kakao').then(r => r.data)

// 관심 종목
export const fetchWatchlist = () =>
  api.get<WatchlistItem[]>('/watchlist').then(r => r.data)

export const addWatchlist = (stock_code: string) =>
  api.post<WatchlistItem>('/watchlist', { stock_code }).then(r => r.data)

export const removeWatchlist = (stock_code: string) =>
  api.delete(`/watchlist/${stock_code}`).then(r => r.data)

// 매수 예정 신호 제외/복구
export const updatePendingSignal = (id: number, is_excluded: boolean) =>
  api.patch<{ id: number; is_excluded: boolean }>(
    `/orders/pending-signals/${id}`,
    { is_excluded },
  ).then(r => r.data)
