import axios from 'axios'

const api = axios.create({ baseURL: '/api' })

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
