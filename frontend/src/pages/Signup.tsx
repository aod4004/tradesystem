import { FormEvent, useState } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import { useAuth } from '../context/AuthContext'

export default function Signup() {
  const { signup } = useAuth()
  const navigate = useNavigate()
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [confirm, setConfirm] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)

  const onSubmit = async (e: FormEvent) => {
    e.preventDefault()
    setError(null)
    if (password.length < 8) {
      setError('비밀번호는 8자 이상이어야 합니다')
      return
    }
    if (password !== confirm) {
      setError('비밀번호 확인이 일치하지 않습니다')
      return
    }
    setBusy(true)
    try {
      await signup(email, password)
      navigate('/', { replace: true })
    } catch (err: any) {
      const msg = err?.response?.data?.detail ?? '회원가입 실패'
      setError(String(msg))
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="min-h-screen bg-gray-900 text-white flex items-center justify-center p-4">
      <form onSubmit={onSubmit} className="bg-gray-800 rounded-xl p-6 w-full max-w-sm space-y-4">
        <h1
          className="text-xl font-bold bg-gradient-to-r from-blue-400 via-cyan-300 to-emerald-400 bg-clip-text text-transparent"
          style={{ paddingBottom: '5pt' }}
        >
          5P’s Algorithmic Trading System
        </h1>
        <p className="text-gray-400 text-sm">회원가입</p>
        <label className="block">
          <span className="text-sm text-gray-400">이메일</span>
          <input type="email" required value={email} onChange={e => setEmail(e.target.value)}
            className="mt-1 w-full bg-gray-900 border border-gray-700 rounded px-3 py-2 text-sm focus:outline-none focus:border-blue-400" />
        </label>
        <label className="block">
          <span className="text-sm text-gray-400">비밀번호 (8자 이상)</span>
          <input type="password" required minLength={8} value={password} onChange={e => setPassword(e.target.value)}
            className="mt-1 w-full bg-gray-900 border border-gray-700 rounded px-3 py-2 text-sm focus:outline-none focus:border-blue-400" />
        </label>
        <label className="block">
          <span className="text-sm text-gray-400">비밀번호 확인</span>
          <input type="password" required value={confirm} onChange={e => setConfirm(e.target.value)}
            className="mt-1 w-full bg-gray-900 border border-gray-700 rounded px-3 py-2 text-sm focus:outline-none focus:border-blue-400" />
        </label>
        {error && <p className="text-sm text-red-400">{error}</p>}
        <button type="submit" disabled={busy}
          className="w-full bg-blue-600 hover:bg-blue-700 disabled:bg-gray-600 rounded py-2 text-sm font-medium">
          {busy ? '가입 중...' : '회원가입'}
        </button>
        <p className="text-xs text-gray-500 text-center">
          이미 계정이 있으신가요? <Link to="/login" className="text-blue-400 hover:underline">로그인</Link>
        </p>
        <p className="text-[10px] text-gray-500 text-center leading-relaxed">
          본 시스템은 개인 사용용으로 제공되며, 모든 매매 결과에 대한 책임은 사용자 본인에게 있습니다.
        </p>
      </form>
    </div>
  )
}
