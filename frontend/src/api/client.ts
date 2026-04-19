import axios from 'axios'

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
export const fetchTodayOrders = () => api.get('/orders/today').then(r => r.data)
export const fetchPendingSignals = () => api.get('/orders/pending-signals').then(r => r.data)
export const runScreening = () => api.post('/orders/run-screening').then(r => r.data)
export const placeManualOrder = (body: {
  stock_code: string; order_type: string; quantity: number; price: number
}) => api.post('/orders/manual', body).then(r => r.data)
