import { FormEvent, useState } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import { useAuth } from '../context/AuthContext'

export default function Login() {
  const { login } = useAuth()
  const navigate = useNavigate()
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)

  const onSubmit = async (e: FormEvent) => {
    e.preventDefault()
    setError(null); setBusy(true)
    try {
      await login(email, password)
      navigate('/', { replace: true })
    } catch (err: any) {
      const msg = err?.response?.data?.detail ?? '로그인 실패'
      setError(String(msg))
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="min-h-screen bg-gray-900 text-white flex items-center justify-center p-4">
      <form onSubmit={onSubmit} className="bg-gray-800 rounded-xl p-6 w-full max-w-sm space-y-4">
        <h1 className="text-xl font-bold bg-gradient-to-r from-blue-400 via-cyan-300 to-emerald-400 bg-clip-text text-transparent">
          MK’s Algorithmic Trading System
        </h1>
        <p className="text-gray-400 text-sm">로그인</p>
        <label className="block">
          <span className="text-sm text-gray-400">이메일</span>
          <input type="email" required value={email} onChange={e => setEmail(e.target.value)}
            className="mt-1 w-full bg-gray-900 border border-gray-700 rounded px-3 py-2 text-sm focus:outline-none focus:border-blue-400" />
        </label>
        <label className="block">
          <span className="text-sm text-gray-400">비밀번호</span>
          <input type="password" required value={password} onChange={e => setPassword(e.target.value)}
            className="mt-1 w-full bg-gray-900 border border-gray-700 rounded px-3 py-2 text-sm focus:outline-none focus:border-blue-400" />
        </label>
        {error && <p className="text-sm text-red-400">{error}</p>}
        <button type="submit" disabled={busy}
          className="w-full bg-blue-600 hover:bg-blue-700 disabled:bg-gray-600 rounded py-2 text-sm font-medium">
          {busy ? '로그인 중...' : '로그인'}
        </button>
        <p className="text-xs text-gray-500 text-center">
          계정이 없으신가요? <Link to="/signup" className="text-blue-400 hover:underline">회원가입</Link>
        </p>
      </form>
    </div>
  )
}
