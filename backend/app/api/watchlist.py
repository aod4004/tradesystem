"""
관심 종목 (user watchlist) API

유저가 직접 등록하는 매수 대상. 스크리닝 후보와 함께 매수 신호 감지에 사용된다.
"""
import asyncio

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import get_current_user
from app.core.kiwoom_client import (
    KiwoomClient, get_kiwoom_client, get_or_create_user_client, to_int, to_float,
)
from app.db.database import get_db
from app.db.models import User, UserWatchlist
from app.db.user_config import get_trading_config


router = APIRouter(prefix="/api/watchlist", tags=["watchlist"])


class WatchlistAddRequest(BaseModel):
    stock_code: str = Field(..., min_length=1, max_length=10)


def _normalize_code(code: str) -> str:
    return code.strip().upper()


def _resolve_client(cfg, user_id: int) -> KiwoomClient:
    """키 등록된 유저면 유저 키, 아니면 시스템 키."""
    if cfg is not None and cfg.kiwoom_app_key and cfg.kiwoom_secret_key:
        return get_or_create_user_client(user_id, cfg)
    return get_kiwoom_client()


def _shell_payload(row: UserWatchlist, error: str | None = None) -> dict:
    """ka10001 조회 실패 시 기본 응답 (가격/재무 0)."""
    return {
        "stock_code": row.stock_code,
        "stock_name": row.stock_name,
        "added_at": row.added_at.isoformat(),
        "current_price": 0,
        "high_1y": 0,
        "low_1y": 0,
        "market_cap": 0,
        "net_income": 0.0,
        "operating_income": 0.0,
        "foreign_ratio": 0.0,
        "drop_from_high": 0.0,
        "rise_from_low": 0.0,
        "error": error,
    }


def _enriched_payload(row: UserWatchlist, info: dict) -> dict:
    current_price = to_int(info.get("cur_prc"))
    high_1y = abs(to_int(info.get("250hgst")))
    low_1y = abs(to_int(info.get("250lwst")))
    listed_shares = to_int(info.get("flo_stk")) * 1000    # 천주 → 주
    market_cap = listed_shares * current_price if listed_shares > 0 else 0
    net_income = to_float(info.get("cup_nga"))
    operating_income = to_float(info.get("bus_pro"))
    foreign_ratio = to_float(info.get("for_exh_rt"))

    drop_from_high = (
        round((1 - current_price / high_1y) * 100, 2)
        if high_1y > 0 and current_price > 0 else 0.0
    )
    rise_from_low = (
        round(high_1y / low_1y, 2) if low_1y > 0 else 0.0
    )
    return {
        "stock_code": row.stock_code,
        "stock_name": row.stock_name,
        "added_at": row.added_at.isoformat(),
        "current_price": current_price,
        "high_1y": high_1y,
        "low_1y": low_1y,
        "market_cap": market_cap,
        "net_income": net_income,
        "operating_income": operating_income,
        "foreign_ratio": foreign_ratio,
        "drop_from_high": drop_from_high,
        "rise_from_low": rise_from_low,
        "error": None,
    }


@router.get("")
async def list_watchlist(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """관심 종목 목록 — ka10001 로 현재가/재무 실시간 enrich."""
    rows = (await db.execute(
        select(UserWatchlist)
        .where(UserWatchlist.user_id == user.id)
        .order_by(UserWatchlist.added_at.desc())
    )).scalars().all()
    if not rows:
        return []

    cfg = await get_trading_config(db, user.id)
    client = _resolve_client(cfg, user.id)

    # 병렬 조회 — rate limit 회피 위해 동시 3개로 제한
    sem = asyncio.Semaphore(3)

    async def fetch_one(row: UserWatchlist) -> dict:
        async with sem:
            try:
                info = await client.get_stock_info(row.stock_code)
            except Exception as e:
                return _shell_payload(row, error=str(e))
            return _enriched_payload(row, info)

    return await asyncio.gather(*(fetch_one(r) for r in rows))


@router.post("", status_code=status.HTTP_201_CREATED)
async def add_watchlist(
    req: WatchlistAddRequest,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    code = _normalize_code(req.stock_code)

    existing = (await db.execute(
        select(UserWatchlist).where(
            UserWatchlist.user_id == user.id,
            UserWatchlist.stock_code == code,
        )
    )).scalar_one_or_none()

    cfg = await get_trading_config(db, user.id)
    client = _resolve_client(cfg, user.id)
    try:
        info = await client.get_stock_info(code)
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"종목 조회 실패: {e}",
        )

    if existing:
        return _enriched_payload(existing, info)

    # ka10001 응답의 종목명 후보 필드들 — 실제 응답 키가 불확실해 여러 후보를 시도
    stock_name = ""
    for key in ("stk_nm", "stock_name", "hname", "stk_name", "name"):
        val = info.get(key)
        if val and str(val).strip():
            stock_name = str(val).strip()
            break

    # 이름이 없더라도 현재가가 있으면 유효한 코드로 간주
    if not stock_name:
        if to_int(info.get("cur_prc")) <= 0:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="유효하지 않은 종목 코드입니다",
            )
        stock_name = code

    row = UserWatchlist(
        user_id=user.id,
        stock_code=code,
        stock_name=stock_name,
    )
    db.add(row)
    await db.commit()
    await db.refresh(row)

    return _enriched_payload(row, info)


@router.delete("/{stock_code}")
async def remove_watchlist(
    stock_code: str,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    code = _normalize_code(stock_code)
    row = (await db.execute(
        select(UserWatchlist).where(
            UserWatchlist.user_id == user.id,
            UserWatchlist.stock_code == code,
        )
    )).scalar_one_or_none()
    if row is None:
        raise HTTPException(status_code=404, detail="not in watchlist")
    await db.delete(row)
    await db.commit()
    return {"ok": True}
