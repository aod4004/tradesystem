import { createContext, useCallback, useContext, useEffect, useMemo, useState, ReactNode } from 'react'
import axios from 'axios'

export interface AuthUser {
  id: number
  email: string
  is_admin: boolean
}

interface AuthContextShape {
  user: AuthUser | null
  token: string | null
  loading: boolean
  login: (email: string, password: string) => Promise<void>
  signup: (email: string, password: string) => Promise<void>
  logout: () => void
}

const AuthContext = createContext<AuthContextShape | null>(null)

const TOKEN_KEY = 'mk_auth_token'

export function AuthProvider({ children }: { children: ReactNode }) {
  const [token, setToken] = useState<string | null>(() => localStorage.getItem(TOKEN_KEY))
  const [user, setUser] = useState<AuthUser | null>(null)
  const [loading, setLoading] = useState<boolean>(!!token)

  // 토큰을 axios 기본 헤더에 반영
  useEffect(() => {
    if (token) {
      axios.defaults.headers.common['Authorization'] = `Bearer ${token}`
      localStorage.setItem(TOKEN_KEY, token)
    } else {
      delete axios.defaults.headers.common['Authorization']
      localStorage.removeItem(TOKEN_KEY)
    }
  }, [token])

  // 401 → 로그아웃
  useEffect(() => {
    const id = axios.interceptors.response.use(
      r => r,
      err => {
        if (err?.response?.status === 401) {
          setToken(null)
          setUser(null)
        }
        return Promise.reject(err)
      },
    )
    return () => axios.interceptors.response.eject(id)
  }, [])

  // 토큰이 있으면 /me 로 사용자 복원
  useEffect(() => {
    if (!token) { setLoading(false); return }
    axios.get('/api/auth/me')
      .then(r => setUser(r.data as AuthUser))
      .catch(() => { setToken(null); setUser(null) })
      .finally(() => setLoading(false))
  }, [token])

  const login = useCallback(async (email: string, password: string) => {
    const r = await axios.post('/api/auth/login', { email, password })
    setToken(r.data.access_token)
    setUser(r.data.user)
  }, [])

  const signup = useCallback(async (email: string, password: string) => {
    const r = await axios.post('/api/auth/signup', { email, password })
    setToken(r.data.access_token)
    setUser(r.data.user)
  }, [])

  const logout = useCallback(() => {
    setToken(null)
    setUser(null)
  }, [])

  const value = useMemo(
    () => ({ user, token, loading, login, signup, logout }),
    [user, token, loading, login, signup, logout],
  )

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>
}

export function useAuth(): AuthContextShape {
  const ctx = useContext(AuthContext)
  if (!ctx) throw new Error('useAuth must be used inside AuthProvider')
  return ctx
}
