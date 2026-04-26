"""
카카오톡 "나에게 보내기" 알림 모듈 — Phase 3.

플로우:
  1) 유저가 /api/settings/kakao/authorize-url → kauth 로 redirect → 동의 → /api/settings/kakao/callback
     콜백이 code 를 token 쌍(access/refresh) 으로 교환해 user_trading_config 에 저장
  2) 이벤트 훅이 notifier.send(user_id, text) 호출 → 유효 토큰으로 /v2/api/talk/memo/default/send 전송
  3) access_token 만료 시 refresh_token 으로 자동 갱신 (유저별 lock 으로 경합 방지)

원칙:
  - 전송 실패는 로그만. 호출부(주문/체결)를 절대 차단하지 않는다.
  - dedup: (user_id, dedup_key) 를 Redis (없으면 in-memory) 에 TTL 60초로 기록.
  - refresh_token 까지 만료됐으면 "재연동 필요" 상태로 간주하고 토큰 모두 삭제.
"""
from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

import httpx
import redis.asyncio as aioredis
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.db.database import AsyncSessionLocal
from app.db.models import User, UserTradingConfig

KAKAO_AUTH_BASE = "https://kauth.kakao.com"
KAKAO_API_BASE = "https://kapi.kakao.com"
_TOKEN_URL = f"{KAKAO_AUTH_BASE}/oauth/token"
_AUTHORIZE_URL = f"{KAKAO_AUTH_BASE}/oauth/authorize"
_SEND_TO_ME_URL = f"{KAKAO_API_BASE}/v2/api/talk/memo/default/send"
_TOKEN_INFO_URL = f"{KAKAO_API_BASE}/v1/user/access_token_info"
_LOGOUT_URL = f"{KAKAO_API_BASE}/v1/user/logout"

_DEDUP_TTL_SECONDS = 60
_ACCESS_TOKEN_SKEW = 60  # 만료 60초 전부터 미리 갱신
_IN_MEMORY_DEDUP: dict[str, float] = {}


@dataclass
class KakaoTokens:
    access_token: str
    refresh_token: str | None
    access_expires_at: datetime
    refresh_expires_at: datetime | None


class KakaoNotifier:
    def __init__(self) -> None:
        self._http: httpx.AsyncClient | None = None
        self._redis: aioredis.Redis | None = None
        self._client_lock = asyncio.Lock()
        # 유저별 토큰 갱신 동시성 방지
        self._refresh_locks: dict[int, asyncio.Lock] = {}

    # ------------------------------------------------------------------ #
    #  공통 — HTTP / Redis
    # ------------------------------------------------------------------ #
    async def _client(self) -> httpx.AsyncClient:
        async with self._client_lock:
            if self._http is None:
                self._http = httpx.AsyncClient(timeout=10)
            return self._http

    async def _get_redis(self) -> aioredis.Redis | None:
        if self._redis is not None:
            return self._redis
        try:
            self._redis = await aioredis.from_url(settings.REDIS_URL)
            await self._redis.ping()
        except Exception:
            self._redis = None
        return self._redis

    async def _should_send(
        self,
        user_id: int,
        dedup_key: str | None,
        ttl: int | None = None,
    ) -> bool:
        if not dedup_key:
            return True
        key = f"notify:dedup:u{user_id}:{dedup_key}"
        eff_ttl = ttl if (ttl is not None and ttl > 0) else _DEDUP_TTL_SECONDS
        redis = await self._get_redis()
        if redis is not None:
            try:
                was_set = await redis.set(key, "1", ex=eff_ttl, nx=True)
                return bool(was_set)
            except Exception:
                pass
        now = time.monotonic()
        _prune_in_memory(now)
        if key in _IN_MEMORY_DEDUP:
            return False
        _IN_MEMORY_DEDUP[key] = now + eff_ttl
        return True

    # ------------------------------------------------------------------ #
    #  OAuth: authorize URL / code → token / refresh
    # ------------------------------------------------------------------ #
    def authorize_url(self, state: str) -> str | None:
        if not settings.KAKAO_REST_API_KEY or not settings.KAKAO_REDIRECT_URI:
            return None
        from urllib.parse import urlencode
        qs = urlencode({
            "client_id": settings.KAKAO_REST_API_KEY,
            "redirect_uri": settings.KAKAO_REDIRECT_URI,
            "response_type": "code",
            "scope": "talk_message",
            "state": state,
        })
        return f"{_AUTHORIZE_URL}?{qs}"

    async def exchange_code(self, code: str) -> KakaoTokens:
        body = {
            "grant_type": "authorization_code",
            "client_id": settings.KAKAO_REST_API_KEY,
            "redirect_uri": settings.KAKAO_REDIRECT_URI,
            "code": code,
        }
        if settings.KAKAO_CLIENT_SECRET:
            body["client_secret"] = settings.KAKAO_CLIENT_SECRET
        return await self._token_request(body)

    async def _refresh(self, refresh_token: str) -> KakaoTokens:
        body = {
            "grant_type": "refresh_token",
            "client_id": settings.KAKAO_REST_API_KEY,
            "refresh_token": refresh_token,
        }
        if settings.KAKAO_CLIENT_SECRET:
            body["client_secret"] = settings.KAKAO_CLIENT_SECRET
        return await self._token_request(body)

    async def _token_request(self, body: dict[str, str]) -> KakaoTokens:
        client = await self._client()
        resp = await client.post(
            _TOKEN_URL,
            data=body,
            headers={"Content-Type": "application/x-www-form-urlencoded;charset=utf-8"},
        )
        if resp.status_code >= 400:
            raise RuntimeError(f"kakao token error {resp.status_code}: {resp.text[:200]}")
        data = resp.json()
        now = datetime.utcnow()
        access = data.get("access_token")
        if not access:
            raise RuntimeError(f"kakao token 응답에 access_token 없음: {data}")
        access_expires = now + timedelta(seconds=int(data.get("expires_in", 21600)))
        refresh = data.get("refresh_token")  # 있을 때만 돌아옴
        if "refresh_token_expires_in" in data:
            refresh_expires = now + timedelta(seconds=int(data["refresh_token_expires_in"]))
        else:
            refresh_expires = None
        return KakaoTokens(
            access_token=access,
            refresh_token=refresh,
            access_expires_at=access_expires,
            refresh_expires_at=refresh_expires,
        )

    # ------------------------------------------------------------------ #
    #  토큰 저장/조회
    # ------------------------------------------------------------------ #
    async def save_tokens(self, user_id: int, tokens: KakaoTokens) -> None:
        async with AsyncSessionLocal() as db:  # type: AsyncSession
            cfg = await _get_or_create_cfg(db, user_id)
            cfg.kakao_access_token = tokens.access_token
            if tokens.refresh_token:
                cfg.kakao_refresh_token = tokens.refresh_token
            cfg.kakao_access_expires_at = tokens.access_expires_at
            if tokens.refresh_expires_at is not None:
                cfg.kakao_refresh_expires_at = tokens.refresh_expires_at
            await db.commit()

    async def clear_tokens(self, user_id: int) -> None:
        async with AsyncSessionLocal() as db:
            cfg = (
                await db.execute(
                    select(UserTradingConfig).where(UserTradingConfig.user_id == user_id)
                )
            ).scalar_one_or_none()
            if cfg is None:
                return
            cfg.kakao_access_token = None
            cfg.kakao_refresh_token = None
            cfg.kakao_access_expires_at = None
            cfg.kakao_refresh_expires_at = None
            await db.commit()

    async def _get_valid_access_token(self, user_id: int) -> str | None:
        """현재 유효한 access_token 을 반환. 만료 임박이면 refresh. 재연동이 필요하면 None."""
        lock = self._refresh_locks.setdefault(user_id, asyncio.Lock())
        async with lock:
            async with AsyncSessionLocal() as db:
                cfg = (
                    await db.execute(
                        select(UserTradingConfig).where(UserTradingConfig.user_id == user_id)
                    )
                ).scalar_one_or_none()
                if cfg is None or not cfg.notifications_enabled:
                    return None
                if not cfg.kakao_access_token:
                    return None
                now = datetime.utcnow()
                valid = (
                    cfg.kakao_access_expires_at is not None
                    and cfg.kakao_access_expires_at > now + timedelta(seconds=_ACCESS_TOKEN_SKEW)
                )
                if valid:
                    return cfg.kakao_access_token
                # refresh 필요
                if not cfg.kakao_refresh_token:
                    return None
                if (
                    cfg.kakao_refresh_expires_at is not None
                    and cfg.kakao_refresh_expires_at <= now
                ):
                    # refresh_token 자체가 만료됐으면 재연동 요구
                    cfg.kakao_access_token = None
                    cfg.kakao_refresh_token = None
                    cfg.kakao_access_expires_at = None
                    cfg.kakao_refresh_expires_at = None
                    await db.commit()
                    return None
                try:
                    tokens = await self._refresh(cfg.kakao_refresh_token)
                except Exception as e:
                    print(f"[notifier] user={user_id} refresh 실패: {e}")
                    return None
                cfg.kakao_access_token = tokens.access_token
                cfg.kakao_access_expires_at = tokens.access_expires_at
                if tokens.refresh_token:
                    cfg.kakao_refresh_token = tokens.refresh_token
                if tokens.refresh_expires_at is not None:
                    cfg.kakao_refresh_expires_at = tokens.refresh_expires_at
                await db.commit()
                return tokens.access_token

    # ------------------------------------------------------------------ #
    #  전송
    # ------------------------------------------------------------------ #
    async def _send_raw(self, access_token: str, text: str, link_url: str | None) -> tuple[bool, str]:
        import json as _json
        template: dict[str, Any] = {
            "object_type": "text",
            "text": text[:4000],  # 카카오 max ~200자 권장이나 4000자까지 허용
            "link": {"web_url": link_url or "", "mobile_web_url": link_url or ""},
            "button_title": "열기" if link_url else "",
        }
        if not link_url:
            template.pop("button_title", None)
        client = await self._client()
        try:
            resp = await client.post(
                _SEND_TO_ME_URL,
                headers={
                    "Authorization": f"Bearer {access_token}",
                    "Content-Type": "application/x-www-form-urlencoded;charset=utf-8",
                },
                data={"template_object": _json.dumps(template, ensure_ascii=False)},
            )
        except Exception as e:
            return False, f"예외: {e}"
        if resp.status_code >= 400:
            return False, f"{resp.status_code} {resp.text[:200]}"
        return True, "ok"

    async def send(
        self,
        user_id: int,
        text: str,
        *,
        dedup_key: str | None = None,
        dedup_ttl: int | None = None,
        link_url: str | None = None,
    ) -> None:
        """유저에게 알림 전송 (조건부).

        dedup_ttl: dedup 키 TTL(초). None 이면 기본 60초. WS 알림처럼 24시간 1회로
        제한해야 하는 케이스에서 86400 등 명시.
        """
        if not settings.KAKAO_REST_API_KEY:
            return
        if not await self._should_send(user_id, dedup_key, ttl=dedup_ttl):
            return
        access = await self._get_valid_access_token(user_id)
        if not access:
            return
        ok, reason = await self._send_raw(access, text, link_url)
        if not ok:
            print(f"[notifier] user={user_id} 카카오 전송 실패: {reason}")

    async def send_to_admins(
        self, text: str, *, dedup_key: str | None = None, dedup_ttl: int | None = None,
    ) -> None:
        """관리자 전체에게 전송 (스케줄러·WS 오류 등)."""
        if not settings.KAKAO_REST_API_KEY:
            return
        async with AsyncSessionLocal() as db:
            rows = (
                await db.execute(
                    select(User.id)
                    .join(UserTradingConfig, UserTradingConfig.user_id == User.id)
                    .where(
                        User.is_admin == True,  # noqa: E712
                        UserTradingConfig.notifications_enabled == True,  # noqa: E712
                        UserTradingConfig.kakao_access_token.isnot(None),
                    )
                )
            ).all()
        for (uid,) in rows:
            await self.send(uid, text, dedup_key=dedup_key, dedup_ttl=dedup_ttl)

    async def send_test(self, user_id: int) -> tuple[bool, str]:
        """연결 테스트 — 저장된 토큰으로 실제 전송 시도."""
        if not settings.KAKAO_REST_API_KEY:
            return False, "서버에 KAKAO_REST_API_KEY 가 설정되지 않았습니다"
        access = await self._get_valid_access_token(user_id)
        if not access:
            return False, "연동된 카카오 토큰이 없거나 만료되었습니다 — 다시 연동하세요"
        return await self._send_raw(
            access,
            "✅ 5P Trading 알림 테스트 — 이 메시지가 보이면 연동이 정상입니다.",
            None,
        )

    async def close(self) -> None:
        if self._http is not None:
            try:
                await self._http.aclose()
            except Exception:
                pass
            self._http = None
        if self._redis is not None:
            try:
                await self._redis.aclose()
            except Exception:
                pass
            self._redis = None


def _prune_in_memory(now: float) -> None:
    stale = [k for k, exp in _IN_MEMORY_DEDUP.items() if exp <= now]
    for k in stale:
        _IN_MEMORY_DEDUP.pop(k, None)


async def _get_or_create_cfg(db: AsyncSession, user_id: int) -> UserTradingConfig:
    cfg = (
        await db.execute(
            select(UserTradingConfig).where(UserTradingConfig.user_id == user_id)
        )
    ).scalar_one_or_none()
    if cfg is None:
        cfg = UserTradingConfig(user_id=user_id)
        db.add(cfg)
        await db.flush()
    return cfg


notifier = KakaoNotifier()


# ---------------------------------------------------------------------- #
#  fire-and-forget 래퍼 — 이벤트 훅에서 await 부담 없이 사용
# ---------------------------------------------------------------------- #
def _fire_and_forget(coro) -> None:
    try:
        loop = asyncio.get_event_loop()
    except RuntimeError:
        return
    if loop.is_running():
        loop.create_task(coro)


def notify_user_fire(
    user_id: int, text: str, *, dedup_key: str | None = None, dedup_ttl: int | None = None,
) -> None:
    _fire_and_forget(notifier.send(user_id, text, dedup_key=dedup_key, dedup_ttl=dedup_ttl))


def notify_admins_fire(
    text: str, *, dedup_key: str | None = None, dedup_ttl: int | None = None,
) -> None:
    _fire_and_forget(notifier.send_to_admins(text, dedup_key=dedup_key, dedup_ttl=dedup_ttl))
