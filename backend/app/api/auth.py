"""
인증 엔드포인트 — 회원가입 / 로그인 / 현재 사용자 조회 / 탈퇴
"""
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import (
    create_access_token,
    get_current_user,
    hash_password,
    verify_password,
)
from app.db.database import get_db
from app.db.models import (
    BuySignal, Order, Position, User, UserTradingConfig,
)

router = APIRouter(prefix="/api/auth", tags=["auth"])


class SignupRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=8, max_length=200)


class LoginRequest(BaseModel):
    email: EmailStr
    password: str


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    user: dict


class UserResponse(BaseModel):
    id: int
    email: str
    is_admin: bool


def _user_public(u: User) -> dict:
    return {"id": u.id, "email": u.email, "is_admin": u.is_admin}


@router.post("/signup", response_model=TokenResponse)
async def signup(req: SignupRequest, db: Annotated[AsyncSession, Depends(get_db)]):
    existing = (await db.execute(select(User).where(User.email == req.email))).scalar_one_or_none()
    if existing:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="email already registered")
    user = User(
        email=str(req.email),
        password_hash=hash_password(req.password),
        is_admin=False,
        is_active=True,
    )
    db.add(user)
    await db.commit()
    await db.refresh(user)
    token = create_access_token(user.id, user.email)
    return TokenResponse(access_token=token, user=_user_public(user))


@router.post("/login", response_model=TokenResponse)
async def login(req: LoginRequest, db: Annotated[AsyncSession, Depends(get_db)]):
    user = (await db.execute(select(User).where(User.email == req.email))).scalar_one_or_none()
    if not user or not verify_password(req.password, user.password_hash):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid credentials")
    if not user.is_active:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="user inactive")
    token = create_access_token(user.id, user.email)
    return TokenResponse(access_token=token, user=_user_public(user))


@router.get("/me", response_model=UserResponse)
async def me(current: Annotated[User, Depends(get_current_user)]):
    return UserResponse(id=current.id, email=current.email, is_admin=current.is_admin)


class DeleteAccountRequest(BaseModel):
    password: str


@router.delete("/me")
async def delete_me(
    req: DeleteAccountRequest,
    current: Annotated[User, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    """회원 탈퇴 — 비밀번호 재확인 후 해당 유저와 관련 데이터 모두 삭제.

    관련 데이터: orders, buy_signals, positions, user_trading_config.
    키움 계좌의 실제 자산은 건드리지 않는다 (시스템 데이터만 정리).
    """
    if not verify_password(req.password, current.password_hash):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid password",
        )

    await db.execute(delete(Order).where(Order.user_id == current.id))
    await db.execute(delete(BuySignal).where(BuySignal.user_id == current.id))
    await db.execute(delete(Position).where(Position.user_id == current.id))
    await db.execute(
        delete(UserTradingConfig).where(UserTradingConfig.user_id == current.id)
    )
    await db.execute(delete(User).where(User.id == current.id))
    await db.commit()
    return {"deleted": True}
