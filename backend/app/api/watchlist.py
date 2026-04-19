"""
관심 종목 (user watchlist) API

유저가 직접 등록하는 매수 대상. 스크리닝 후보와 함께 매수 신호 감지에 사용된다.
"""
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import get_current_user
from app.core.kiwoom_client import get_kiwoom_client, get_or_create_user_client, to_int
from app.db.database import get_db
from app.db.models import User, UserWatchlist
from app.db.user_config import get_trading_config


router = APIRouter(prefix="/api/watchlist", tags=["watchlist"])


class WatchlistAddRequest(BaseModel):
    stock_code: str = Field(..., min_length=1, max_length=10)


def _normalize_code(code: str) -> str:
    return code.strip().upper()


@router.get("")
async def list_watchlist(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    rows = (await db.execute(
        select(UserWatchlist)
        .where(UserWatchlist.user_id == user.id)
        .order_by(UserWatchlist.added_at.desc())
    )).scalars().all()
    return [
        {
            "stock_code": r.stock_code,
            "stock_name": r.stock_name,
            "added_at": r.added_at.isoformat(),
        }
        for r in rows
    ]


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
    if existing:
        return {
            "stock_code": existing.stock_code,
            "stock_name": existing.stock_name,
            "added_at": existing.added_at.isoformat(),
        }

    # 종목 코드 검증 + 이름 조회. 유저 키가 있으면 유저 키로, 없으면 시스템 키로 폴백.
    cfg = await get_trading_config(db, user.id)
    if cfg is not None and cfg.kiwoom_app_key and cfg.kiwoom_secret_key:
        client = get_or_create_user_client(user.id, cfg)
    else:
        client = get_kiwoom_client()
    try:
        info = await client.get_stock_info(code)
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"종목 조회 실패: {e}",
        )

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

    return {
        "stock_code": row.stock_code,
        "stock_name": row.stock_name,
        "added_at": row.added_at.isoformat(),
    }


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
