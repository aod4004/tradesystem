import { FormEvent, useEffect, useState } from 'react'
import { Link, useNavigate, useSearchParams } from 'react-router-dom'
import { useAuth } from '../context/AuthContext'
import {
  deleteAccount,
  deleteKiwoomKeys,
  disconnectKakao,
  fetchKakaoAuthorizeUrl,
  fetchKakaoStatus,
  fetchKiwoomStatus,
  fetchMorningApproval,
  fetchRiskGuards,
  KakaoStatus,
  KiwoomKeysStatus,
  RiskGuardsStatus,
  saveKiwoomKeys,
  saveRiskGuards,
  sendKakaoTest,
  setKakaoEnabled,
  setMorningApproval,
} from '../api/client'

export default function Settings() {
  const { user, logout } = useAuth()
  const navigate = useNavigate()
  const [searchParams, setSearchParams] = useSearchParams()

  const [status, setStatus] = useState<KiwoomKeysStatus | null>(null)
  const [appKey, setAppKey] = useState('')
  const [secretKey, setSecretKey] = useState('')
  const [mock, setMock] = useState(true)
  const [totalInvest, setTotalInvest] = useState<string>('')
  const [busy, setBusy] = useState(false)
  const [msg, setMsg] = useState<string | null>(null)
  const [err, setErr] = useState<string | null>(null)

  // ── 카카오 알림 상태 ────────────────────────────────────────
  const [kakao, setKakao] = useState<KakaoStatus | null>(null)
  const [kakaoBusy, setKakaoBusy] = useState(false)
  const [kakaoMsg, setKakaoMsg] = useState<string | null>(null)
  const [kakaoErr, setKakaoErr] = useState<string | null>(null)

  // ── 사전 승인 모드 상태 (Phase 4.2) ─────────────────────────
  const [approvalEnabled, setApprovalEnabled] = useState<boolean | null>(null)
  const [approvalBusy, setApprovalBusy] = useState(false)

  // ── 리스크 가드 상태 ────────────────────────────────────────
  const [risk, setRisk] = useState<RiskGuardsStatus | null>(null)
  const [riskAmount, setRiskAmount] = useState<string>('')
  const [riskCount, setRiskCount] = useState<string>('')
  const [riskRatioPct, setRiskRatioPct] = useState<string>('')
  const [riskBusy, setRiskBusy] = useState(false)
  const [riskMsg, setRiskMsg] = useState<string | null>(null)
  const [riskErr, setRiskErr] = useState<string | null>(null)

  const refreshKakao = () => fetchKakaoStatus().then(setKakao).catch(() => {})
  const refreshRisk = () => fetchRiskGuards().then(r => {
    setRisk(r)
    setRiskAmount(r.daily_order_amount_limit != null ? String(r.daily_order_amount_limit) : '')
    setRiskCount(r.daily_order_count_limit != null ? String(r.daily_order_count_limit) : '')
    setRiskRatioPct(
      r.max_position_ratio != null ? String(Math.round(r.max_position_ratio * 1000) / 10) : '',
    )
  }).catch(() => {})

  useEffect(() => {
    fetchKiwoomStatus().then(s => {
      setStatus(s)
      setMock(s.mock)
      if (s.total_investment > 0) setTotalInvest(String(s.total_investment))
    }).catch(() => {})
    refreshKakao()
    refreshRisk()
    fetchMorningApproval().then(s => setApprovalEnabled(s.enabled)).catch(() => {})
  }, [])

  const onToggleApproval = async (enabled: boolean) => {
    setApprovalBusy(true)
    try {
      const s = await setMorningApproval(enabled)
      setApprovalEnabled(s.enabled)
    } catch {
      /* 토글 실패 시 상태 유지 */
    } finally {
      setApprovalBusy(false)
    }
  }

  // 카카오 OAuth 콜백 복귀 처리
  useEffect(() => {
    const result = searchParams.get('kakao')
    if (!result) return
    if (result === 'connected') {
      setKakaoMsg('카카오톡 알림이 연동되었습니다.')
      refreshKakao()
    } else if (result === 'error') {
      setKakaoErr(searchParams.get('reason') || '연동 실패')
    }
    // URL 정리
    searchParams.delete('kakao')
    searchParams.delete('reason')
    setSearchParams(searchParams, { replace: true })
  }, [searchParams, setSearchParams])

  const onKakaoConnect = async () => {
    setKakaoBusy(true); setKakaoMsg(null); setKakaoErr(null)
    try {
      const { url } = await fetchKakaoAuthorizeUrl()
      window.location.href = url
    } catch (e: any) {
      setKakaoErr(String(e?.response?.data?.detail ?? e?.message ?? e))
      setKakaoBusy(false)
    }
  }

  const onKakaoTest = async () => {
    setKakaoBusy(true); setKakaoMsg(null); setKakaoErr(null)
    try {
      await sendKakaoTest()
      setKakaoMsg('카카오톡으로 테스트 메시지를 보냈습니다.')
    } catch (e: any) {
      setKakaoErr(String(e?.response?.data?.detail ?? e?.message ?? e))
    } finally {
      setKakaoBusy(false)
    }
  }

  const onKakaoDisconnect = async () => {
    if (!confirm('카카오 알림 연동을 해제할까요?')) return
    setKakaoBusy(true); setKakaoMsg(null); setKakaoErr(null)
    try {
      const s = await disconnectKakao()
      setKakao(s)
      setKakaoMsg('연동이 해제되었습니다.')
    } catch (e: any) {
      setKakaoErr(String(e?.response?.data?.detail ?? e?.message ?? e))
    } finally {
      setKakaoBusy(false)
    }
  }

  const onToggleNotifications = async (enabled: boolean) => {
    setKakaoBusy(true); setKakaoErr(null)
    try {
      const s = await setKakaoEnabled(enabled)
      setKakao(s)
    } catch (e: any) {
      setKakaoErr(String(e?.response?.data?.detail ?? e?.message ?? e))
    } finally {
      setKakaoBusy(false)
    }
  }

  const onSave = async (e: FormEvent) => {
    e.preventDefault()
    setBusy(true); setMsg(null); setErr(null)
    try {
      const body: any = { app_key: appKey, secret_key: secretKey, mock }
      if (totalInvest.trim()) body.total_investment = Number(totalInvest)
      const s = await saveKiwoomKeys(body)
      setStatus(s)
      setAppKey(''); setSecretKey('')
      setMsg('저장되었습니다. 대시보드에서 잔고를 확인하세요.')
    } catch (e: any) {
      setErr(String(e?.response?.data?.detail ?? e?.message ?? e))
    } finally {
      setBusy(false)
    }
  }

  // ── 리스크 가드 핸들러 ───────────────────────────────────────
  const onToggleRisk = async (enabled: boolean) => {
    setRiskBusy(true); setRiskMsg(null); setRiskErr(null)
    try {
      const r = await saveRiskGuards({ enabled })
      setRisk(r)
    } catch (e: any) {
      setRiskErr(String(e?.response?.data?.detail ?? e?.message ?? e))
    } finally {
      setRiskBusy(false)
    }
  }

  const onSaveRisk = async (e: FormEvent) => {
    e.preventDefault()
    setRiskBusy(true); setRiskMsg(null); setRiskErr(null)
    try {
      const body: any = {}
      // 빈 문자열 → clear_* 플래그로 보내 한도 해제
      if (riskAmount.trim() === '') body.clear_amount = true
      else body.daily_order_amount_limit = Number(riskAmount)
      if (riskCount.trim() === '') body.clear_count = true
      else body.daily_order_count_limit = Number(riskCount)
      if (riskRatioPct.trim() === '') body.clear_ratio = true
      else body.max_position_ratio = Number(riskRatioPct) / 100
      const r = await saveRiskGuards(body)
      setRisk(r)
      setRiskMsg('저장되었습니다.')
    } catch (e: any) {
      setRiskErr(String(e?.response?.data?.detail ?? e?.message ?? e))
    } finally {
      setRiskBusy(false)
    }
  }

  const onDeleteKeys = async () => {
    if (!confirm('등록된 키움 키를 삭제할까요?')) return
    setBusy(true); setMsg(null); setErr(null)
    try {
      const s = await deleteKiwoomKeys()
      setStatus(s)
      setMsg('키가 삭제되었습니다.')
    } catch (e: any) {
      setErr(String(e?.response?.data?.detail ?? e?.message ?? e))
    } finally {
      setBusy(false)
    }
  }

  // ── 회원 탈퇴 ───────────────────────────────────────────────
  const [confirmPw, setConfirmPw] = useState('')
  const [showConfirm, setShowConfirm] = useState(false)

  const onDeleteAccount = async () => {
    if (!confirmPw) return
    if (!confirm('정말로 회원 탈퇴하시겠습니까? 되돌릴 수 없습니다.')) return
    setBusy(true); setErr(null)
    try {
      await deleteAccount(confirmPw)
      logout()
      navigate('/login', { replace: true })
    } catch (e: any) {
      setErr(String(e?.response?.data?.detail ?? e?.message ?? e))
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="min-h-screen bg-gray-900 text-white p-4 md:p-6">
      <div className="max-w-2xl mx-auto">
        <div className="flex items-center justify-between mb-6">
          <Link to="/" className="text-sm text-blue-400 hover:underline">← 대시보드</Link>
          <span className="text-sm text-gray-400">{user?.email}</span>
        </div>

        <h1 className="text-2xl font-bold mb-6">설정</h1>

        {/* 키움 키 ------------------------------------------------- */}
        <section className="bg-gray-800 rounded-xl p-5 mb-6">
          <div className="flex items-center justify-between mb-3">
            <h2 className="text-lg font-semibold">키움 API 키</h2>
            <span className={`text-xs px-2 py-0.5 rounded ${status?.has_keys ? 'bg-emerald-700 text-emerald-100' : 'bg-gray-700 text-gray-300'}`}>
              {status?.has_keys ? '등록됨' : '미등록'}
            </span>
          </div>
          <p className="text-sm text-gray-400 mb-4">
            키움 OpenAPI+ 에서 발급받은 App Key / Secret Key 를 입력하세요.
            모의투자와 실전투자 계정의 키는 서로 다릅니다.
          </p>

          {status?.ws_permanently_stopped && (
            <div className="mb-4 rounded-lg border border-red-500/40 bg-red-900/30 px-4 py-3 text-sm text-red-200">
              <div className="font-semibold mb-1">🛑 키움 실시간 연결이 자동 재시도를 중단했습니다</div>
              <div className="text-red-300/90">
                연속된 인증 실패로 WebSocket 루프가 영구 정지된 상태입니다.
                아래에서 키를 다시 저장하면 자동으로 재시작됩니다.
              </div>
            </div>
          )}

          <form onSubmit={onSave} className="space-y-3">
            <label className="block">
              <span className="text-sm text-gray-400">App Key</span>
              <input type="text" value={appKey} onChange={e => setAppKey(e.target.value)}
                autoComplete="off" required
                className="mt-1 w-full bg-gray-900 border border-gray-700 rounded px-3 py-2 text-sm focus:outline-none focus:border-blue-400" />
            </label>
            <label className="block">
              <span className="text-sm text-gray-400">Secret Key</span>
              <input type="password" value={secretKey} onChange={e => setSecretKey(e.target.value)}
                autoComplete="off" required
                className="mt-1 w-full bg-gray-900 border border-gray-700 rounded px-3 py-2 text-sm focus:outline-none focus:border-blue-400" />
            </label>
            <label className="flex items-center gap-2 text-sm text-gray-300">
              <input type="checkbox" checked={mock} onChange={e => setMock(e.target.checked)} />
              모의투자 계정으로 연결 (해제 시 실전 계정)
            </label>
            <label className="block">
              <span className="text-sm text-gray-400">총 투자금 (원, 선택)</span>
              <input type="number" min="0" value={totalInvest} onChange={e => setTotalInvest(e.target.value)}
                placeholder="예: 10000000"
                className="mt-1 w-full bg-gray-900 border border-gray-700 rounded px-3 py-2 text-sm focus:outline-none focus:border-blue-400" />
            </label>

            {msg && <p className="text-sm text-emerald-400">{msg}</p>}
            {err && <p className="text-sm text-red-400">{err}</p>}

            <div className="flex gap-2 pt-1">
              <button type="submit" disabled={busy}
                className="bg-blue-600 hover:bg-blue-700 disabled:bg-gray-600 rounded px-4 py-2 text-sm font-medium">
                {busy ? '저장 중...' : '저장 & 검증'}
              </button>
              {status?.has_keys && (
                <button type="button" onClick={onDeleteKeys} disabled={busy}
                  className="bg-gray-700 hover:bg-gray-600 rounded px-4 py-2 text-sm">
                  키 삭제
                </button>
              )}
            </div>
          </form>
        </section>

        {/* 카카오톡 알림 -------------------------------------------- */}
        <section className="bg-gray-800 rounded-xl p-5 mb-6">
          <div className="flex items-center justify-between mb-3">
            <h2 className="text-lg font-semibold">카카오톡 알림 (나에게 보내기)</h2>
            <span className={`text-xs px-2 py-0.5 rounded ${kakao?.connected ? 'bg-emerald-700 text-emerald-100' : 'bg-gray-700 text-gray-300'}`}>
              {kakao?.connected ? '연동됨' : '미연동'}
            </span>
          </div>
          <p className="text-sm text-gray-400 mb-4">
            매수 신호·주문 접수·체결·취소·매도 조건 도달 알림이 본인 카카오톡 "나에게 보내기" 로 전송됩니다.
            연동 시 카카오 로그인 및 <span className="text-gray-200">나에게 메시지 전송</span> 권한 동의가 필요합니다.
          </p>

          {kakao && !kakao.configured && (
            <p className="text-sm text-amber-400 mb-3">
              서버에 카카오 앱이 설정돼 있지 않습니다 (KAKAO_REST_API_KEY / KAKAO_REDIRECT_URI).
            </p>
          )}

          {kakaoMsg && <p className="text-sm text-emerald-400 mb-2">{kakaoMsg}</p>}
          {kakaoErr && <p className="text-sm text-red-400 mb-2">{kakaoErr}</p>}

          <div className="flex flex-wrap gap-2">
            {!kakao?.connected ? (
              <button onClick={onKakaoConnect} disabled={kakaoBusy || !kakao?.configured}
                className="bg-yellow-500 hover:bg-yellow-400 text-gray-900 disabled:bg-gray-600 disabled:text-gray-300 rounded px-4 py-2 text-sm font-medium">
                {kakaoBusy ? '이동 중...' : '카카오톡 연동'}
              </button>
            ) : (
              <>
                <button onClick={onKakaoTest} disabled={kakaoBusy}
                  className="bg-blue-600 hover:bg-blue-700 disabled:bg-gray-600 rounded px-4 py-2 text-sm font-medium">
                  테스트 전송
                </button>
                <button onClick={onKakaoDisconnect} disabled={kakaoBusy}
                  className="bg-gray-700 hover:bg-gray-600 rounded px-4 py-2 text-sm">
                  연동 해제
                </button>
                <label className="inline-flex items-center gap-2 px-3 py-2 text-sm text-gray-300">
                  <input type="checkbox" checked={kakao.notifications_enabled}
                    onChange={e => onToggleNotifications(e.target.checked)} disabled={kakaoBusy} />
                  알림 켜기
                </label>
              </>
            )}
          </div>

          {kakao?.connected && kakao.access_expires_at && (
            <p className="text-xs text-gray-500 mt-3">
              액세스 토큰 만료: {new Date(kakao.access_expires_at).toLocaleString()}
              {kakao.refresh_expires_at && ` · 리프레시 만료: ${new Date(kakao.refresh_expires_at).toLocaleString()}`}
            </p>
          )}
        </section>

        {/* 장 시작 전 사전 승인 모드 (Phase 4.2) -------------------- */}
        <section className="bg-gray-800 rounded-xl p-5 mb-6">
          <div className="flex items-center justify-between mb-3">
            <h2 className="text-lg font-semibold">장 시작 전 사전 승인 모드</h2>
            <label className="inline-flex items-center gap-2 text-sm text-gray-300">
              <input type="checkbox" checked={!!approvalEnabled}
                onChange={e => onToggleApproval(e.target.checked)}
                disabled={approvalBusy || approvalEnabled === null} />
              사용
            </label>
          </div>
          <p className="text-sm text-gray-400">
            켜 두면 08:50 자동 매수가 즉시 나가지 않고 <span className="text-gray-200">승인 대기</span> 상태로 둡니다.
            카카오톡으로 오늘 신호 요약이 전송되며, 대시보드의 "승인 대기" 카드에서
            <span className="text-gray-200"> "전체 승인 &amp; 주문"</span> 을 눌러야 실제 주문이 전송됩니다.
          </p>
        </section>

        {/* 런타임 리스크 가드 --------------------------------------- */}
        <section className="bg-gray-800 rounded-xl p-5 mb-6">
          <div className="flex items-center justify-between mb-3">
            <h2 className="text-lg font-semibold">주문 리스크 가드</h2>
            <label className="inline-flex items-center gap-2 text-sm text-gray-300">
              <input type="checkbox" checked={!!risk?.enabled}
                onChange={e => onToggleRisk(e.target.checked)} disabled={riskBusy || !risk} />
              가드 사용
            </label>
          </div>
          <p className="text-sm text-gray-400 mb-4">
            매수 주문 전 자동 체크로 사고를 방지합니다. 위반 시 주문이 차단되고 카카오톡으로 사유가 전송됩니다.
            <br />빈 값으로 저장하면 해당 한도 없음. 매도 주문에는 적용되지 않습니다.
          </p>

          <form onSubmit={onSaveRisk} className="space-y-3">
            <label className="block">
              <span className="text-sm text-gray-400">일일 매수 금액 한도 (원)</span>
              <input type="number" min="0" step="100000" value={riskAmount}
                onChange={e => setRiskAmount(e.target.value)}
                placeholder="예: 2000000 (비우면 제한 없음)"
                className="mt-1 w-full bg-gray-900 border border-gray-700 rounded px-3 py-2 text-sm focus:outline-none focus:border-blue-400" />
            </label>
            <label className="block">
              <span className="text-sm text-gray-400">일일 매수 건수 한도</span>
              <input type="number" min="0" step="1" value={riskCount}
                onChange={e => setRiskCount(e.target.value)}
                placeholder="예: 10 (비우면 제한 없음)"
                className="mt-1 w-full bg-gray-900 border border-gray-700 rounded px-3 py-2 text-sm focus:outline-none focus:border-blue-400" />
            </label>
            <label className="block">
              <span className="text-sm text-gray-400">
                종목당 투자한도 비율 (%)
                {risk && (
                  <span className="text-gray-500"> · 기본 {(risk.default_max_position_ratio * 100).toFixed(1)}%</span>
                )}
              </span>
              <input type="number" min="0" max="100" step="0.1" value={riskRatioPct}
                onChange={e => setRiskRatioPct(e.target.value)}
                placeholder={risk ? `예: ${(risk.default_max_position_ratio * 100).toFixed(1)} (비우면 기본값 사용)` : ''}
                className="mt-1 w-full bg-gray-900 border border-gray-700 rounded px-3 py-2 text-sm focus:outline-none focus:border-blue-400" />
            </label>

            {riskMsg && <p className="text-sm text-emerald-400">{riskMsg}</p>}
            {riskErr && <p className="text-sm text-red-400">{riskErr}</p>}

            <button type="submit" disabled={riskBusy}
              className="bg-blue-600 hover:bg-blue-700 disabled:bg-gray-600 rounded px-4 py-2 text-sm font-medium">
              {riskBusy ? '저장 중...' : '가드 설정 저장'}
            </button>
          </form>
        </section>

        {/* 회원 탈퇴 ------------------------------------------------ */}
        <section className="bg-gray-800 rounded-xl p-5 border border-red-900/40">
          <h2 className="text-lg font-semibold mb-2 text-red-400">회원 탈퇴</h2>
          <p className="text-sm text-gray-400 mb-4">
            탈퇴 시 로그인 정보와 보유 포지션/주문/신호 기록이 모두 삭제됩니다.
            키움 계좌의 실제 자산은 영향받지 않습니다.
          </p>

          {!showConfirm ? (
            <button onClick={() => setShowConfirm(true)}
              className="bg-red-700 hover:bg-red-600 rounded px-4 py-2 text-sm font-medium">
              탈퇴하기
            </button>
          ) : (
            <div className="space-y-3">
              <label className="block">
                <span className="text-sm text-gray-400">확인을 위해 비밀번호를 입력하세요</span>
                <input type="password" value={confirmPw} onChange={e => setConfirmPw(e.target.value)}
                  autoComplete="current-password"
                  className="mt-1 w-full bg-gray-900 border border-gray-700 rounded px-3 py-2 text-sm focus:outline-none focus:border-red-400" />
              </label>
              <div className="flex gap-2">
                <button onClick={onDeleteAccount} disabled={busy || !confirmPw}
                  className="bg-red-700 hover:bg-red-600 disabled:bg-gray-600 rounded px-4 py-2 text-sm font-medium">
                  {busy ? '탈퇴 중...' : '탈퇴 확정'}
                </button>
                <button onClick={() => { setShowConfirm(false); setConfirmPw('') }}
                  className="bg-gray-700 hover:bg-gray-600 rounded px-4 py-2 text-sm">
                  취소
                </button>
              </div>
            </div>
          )}
        </section>
      </div>
    </div>
  )
}
