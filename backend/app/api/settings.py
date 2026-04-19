"""유저 설정 API — 키움 키 등록/조회/삭제, 투자금 설정."""
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import get_current_user
from app.core.kiwoom_client import KiwoomClient
from app.db.database import get_db
from app.db.models import User, UserTradingConfig


router = APIRouter(prefix="/api/settings", tags=["settings"])


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
    return KiwoomKeysStatus(
        has_keys=False,
        mock=cfg.kiwoom_mock if cfg else True,
        total_investment=cfg.total_investment if cfg else 0.0,
    )
