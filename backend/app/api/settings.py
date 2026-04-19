"""유저 설정 API — 키움 키 등록/조회/삭제, 투자금 설정, 카카오 알림 연동."""
from datetime import datetime, timedelta, timezone

import jwt
from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import RedirectResponse
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import get_current_user
from app.config import settings
from app.core.kiwoom_client import (
    KiwoomClient, get_or_create_user_client, invalidate_user_client,
)
from app.core.notifier import notifier
from app.db.database import AsyncSessionLocal, get_db
from app.db.models import User, UserTradingConfig
from app.ws.kiwoom_ws import kiwoom_pool


router = APIRouter(prefix="/api/settings", tags=["settings"])

# 카카오 OAuth 의 state 파라미터 — 유저 식별 + CSRF 방지용 JWT
_KAKAO_STATE_AUD = "kakao_oauth_state"
_KAKAO_STATE_TTL_MIN = 10


class KiwoomKeysStatus(BaseModel):
    has_keys: bool
    mock: bool
    total_investment: float


class KiwoomKeysPayload(BaseModel):
    app_key: str = Field(min_length=4, max_length=200)
    secret_key: str = Field(min_length=4, max_length=200)
    mock: bool = True
    total_investment: float | None = None


async def _get_or_create_config(db: AsyncSession, user_id: int) -> UserTradingConfig:
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


@router.get("/kiwoom-keys", response_model=KiwoomKeysStatus)
async def get_status(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    cfg = (
        await db.execute(
            select(UserTradingConfig).where(UserTradingConfig.user_id == user.id)
        )
    ).scalar_one_or_none()
    if cfg is None:
        return KiwoomKeysStatus(has_keys=False, mock=True, total_investment=0.0)
    has = bool(cfg.kiwoom_app_key and cfg.kiwoom_secret_key)
    return KiwoomKeysStatus(
        has_keys=has, mock=cfg.kiwoom_mock, total_investment=cfg.total_investment,
    )


@router.put("/kiwoom-keys", response_model=KiwoomKeysStatus)
async def update_keys(
    payload: KiwoomKeysPayload,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    # 저장 전에 키가 실제로 유효한지 토큰 발급 한 번으로 검증
    probe = KiwoomClient(
        app_key=payload.app_key, secret_key=payload.secret_key, mock=payload.mock,
    )
    try:
        await probe.get_token()
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"키움 토큰 발급 실패: {e}",
        )
    finally:
        await probe.close()

    cfg = await _get_or_create_config(db, user.id)
    cfg.kiwoom_app_key = payload.app_key
    cfg.kiwoom_secret_key = payload.secret_key
    cfg.kiwoom_mock = payload.mock
    if payload.total_investment is not None and payload.total_investment > 0:
        cfg.total_investment = payload.total_investment
    await db.commit()
    await db.refresh(cfg)

    # 레지스트리/WS 풀에 새 키 반영 — 기존 연결이 있으면 종료 후 재연결
    await invalidate_user_client(user.id)
    client = get_or_create_user_client(user.id, cfg)
    try:
        await kiwoom_pool.connect_user(user.id, client)
    except Exception as e:
        print(f"[settings] user={user.id} WS 연결 실패: {e}")

    return KiwoomKeysStatus(
        has_keys=True, mock=cfg.kiwoom_mock, total_investment=cfg.total_investment,
    )


@router.delete("/kiwoom-keys", response_model=KiwoomKeysStatus)
async def delete_keys(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    cfg = (
        await db.execute(
            select(UserTradingConfig).where(UserTradingConfig.user_id == user.id)
        )
    ).scalar_one_or_none()
    if cfg is not None:
        cfg.kiwoom_app_key = None
        cfg.kiwoom_secret_key = None
        await db.commit()

    # WS 연결 및 캐시된 클라이언트 정리
    await kiwoom_pool.disconnect_user(user.id)
    await invalidate_user_client(user.id)

    return KiwoomKeysStatus(
        has_keys=False,
        mock=cfg.kiwoom_mock if cfg else True,
        total_investment=cfg.total_investment if cfg else 0.0,
    )


# ====================================================================== #
#  카카오톡 "나에게 보내기" 알림 연동 (Phase 3)
# ====================================================================== #

class KakaoStatus(BaseModel):
    configured: bool                       # 서버에 REST API 키/Redirect URI 가 설정돼 있는지
    connected: bool                        # 이 유저의 토큰이 유효 범위에 있는지
    notifications_enabled: bool
    access_expires_at: datetime | None = None
    refresh_expires_at: datetime | None = None


class KakaoEnabledPayload(BaseModel):
    enabled: bool


def _kakao_configured() -> bool:
    return bool(settings.KAKAO_REST_API_KEY and settings.KAKAO_REDIRECT_URI)


def _encode_state(user_id: int) -> str:
    now = datetime.now(timezone.utc)
    payload = {
        "aud": _KAKAO_STATE_AUD,
        "sub": str(user_id),
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(minutes=_KAKAO_STATE_TTL_MIN)).timestamp()),
    }
    return jwt.encode(payload, settings.JWT_SECRET, algorithm=settings.JWT_ALGORITHM)


def _decode_state(state: str) -> int | None:
    try:
        payload = jwt.decode(
            state, settings.JWT_SECRET,
            algorithms=[settings.JWT_ALGORITHM],
            audience=_KAKAO_STATE_AUD,
        )
        sub = payload.get("sub")
        return int(sub) if sub is not None else None
    except Exception:
        return None


async def _load_cfg(db: AsyncSession, user_id: int) -> UserTradingConfig | None:
    return (
        await db.execute(
            select(UserTradingConfig).where(UserTradingConfig.user_id == user_id)
        )
    ).scalar_one_or_none()


def _build_status(cfg: UserTradingConfig | None) -> KakaoStatus:
    configured = _kakao_configured()
    if cfg is None:
        return KakaoStatus(configured=configured, connected=False, notifications_enabled=True)
    now = datetime.utcnow()
    connected = bool(
        cfg.kakao_access_token
        and (
            (cfg.kakao_access_expires_at is not None and cfg.kakao_access_expires_at > now)
            or cfg.kakao_refresh_token
        )
    )
    return KakaoStatus(
        configured=configured,
        connected=connected,
        notifications_enabled=cfg.notifications_enabled,
        access_expires_at=cfg.kakao_access_expires_at,
        refresh_expires_at=cfg.kakao_refresh_expires_at,
    )


@router.get("/kakao", response_model=KakaoStatus)
async def kakao_status(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    cfg = await _load_cfg(db, user.id)
    return _build_status(cfg)


@router.get("/kakao/authorize-url")
async def kakao_authorize_url(user: User = Depends(get_current_user)):
    if not _kakao_configured():
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="서버에 카카오 앱(KAKAO_REST_API_KEY/REDIRECT_URI)이 설정돼 있지 않습니다",
        )
    state = _encode_state(user.id)
    url = notifier.authorize_url(state)
    if url is None:
        raise HTTPException(status_code=503, detail="authorize URL 구성 실패")
    return {"url": url}


@router.get("/kakao/callback")
async def kakao_callback(
    code: str | None = Query(default=None),
    state: str | None = Query(default=None),
    error: str | None = Query(default=None),
    error_description: str | None = Query(default=None),
):
    """카카오 → 이 엔드포인트 → /settings?kakao=connected 로 redirect."""
    front_ok = "/settings?kakao=connected"
    front_err = "/settings?kakao=error"

    if error:
        detail = error_description or error
        return RedirectResponse(url=f"{front_err}&reason={detail}")
    if not code or not state:
        return RedirectResponse(url=f"{front_err}&reason=missing_code")

    user_id = _decode_state(state)
    if not user_id:
        return RedirectResponse(url=f"{front_err}&reason=bad_state")

    try:
        tokens = await notifier.exchange_code(code)
    except Exception as e:
        print(f"[settings] 카카오 code 교환 실패 user={user_id}: {e}")
        return RedirectResponse(url=f"{front_err}&reason=token_exchange")

    try:
        await notifier.save_tokens(user_id, tokens)
    except Exception as e:
        print(f"[settings] 카카오 토큰 저장 실패 user={user_id}: {e}")
        return RedirectResponse(url=f"{front_err}&reason=save_failed")

    return RedirectResponse(url=front_ok)


@router.post("/kakao/test")
async def kakao_test(user: User = Depends(get_current_user)):
    ok, reason = await notifier.send_test(user.id)
    if not ok:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=reason)
    return {"ok": True}


@router.patch("/kakao/enabled", response_model=KakaoStatus)
async def kakao_set_enabled(
    payload: KakaoEnabledPayload,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    cfg = await _load_cfg(db, user.id)
    if cfg is None:
        cfg = UserTradingConfig(user_id=user.id)
        db.add(cfg)
        await db.flush()
    cfg.notifications_enabled = payload.enabled
    await db.commit()
    await db.refresh(cfg)
    return _build_status(cfg)


@router.delete("/kakao", response_model=KakaoStatus)
async def kakao_disconnect(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    await notifier.clear_tokens(user.id)
    cfg = await _load_cfg(db, user.id)
    return _build_status(cfg)
