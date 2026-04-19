import asyncio
import os
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Query, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from sqlalchemy import select

from app.auth import authenticate_websocket, hash_password
from app.config import settings
from app.db.database import AsyncSessionLocal
from app.db.models import User, UserTradingConfig
from app.api import auth as auth_api, dashboard, account, orders
from app.scheduler.jobs import create_scheduler
from app.strategy.ma20 import refresh_ma20_for_active_positions
from app.ws.manager import manager
from app.ws.kiwoom_ws import kiwoom_ws


ALEMBIC_INI = Path(__file__).resolve().parent.parent / "alembic.ini"


def _run_alembic_upgrade() -> None:
    """Alembic 을 동기 API 로 실행 — env.py 가 내부적으로 asyncio.run 을 쓰기 때문에
    호출부의 이벤트 루프와 충돌하지 않도록 별도 스레드에서 돌린다."""
    from alembic import command
    from alembic.config import Config

    cfg = Config(str(ALEMBIC_INI))
    command.upgrade(cfg, "head")


async def run_migrations() -> None:
    await asyncio.to_thread(_run_alembic_upgrade)


async def ensure_admin_user():
    """최초 부팅 시 env 기반 admin 계정과 trading_config 를 upsert."""
    async with AsyncSessionLocal() as db:
        admin = (
            await db.execute(select(User).where(User.email == settings.ADMIN_EMAIL))
        ).scalar_one_or_none()
        if admin is None:
            admin = User(
                email=settings.ADMIN_EMAIL,
                password_hash=hash_password(settings.ADMIN_PASSWORD),
                is_admin=True,
                is_active=True,
            )
            db.add(admin)
            await db.flush()
            print(f"[main] admin 계정 생성: {settings.ADMIN_EMAIL}")

        cfg = (
            await db.execute(
                select(UserTradingConfig).where(UserTradingConfig.user_id == admin.id)
            )
        ).scalar_one_or_none()
        if cfg is None:
            db.add(UserTradingConfig(
                user_id=admin.id,
                total_investment=settings.TOTAL_INVESTMENT,
                kiwoom_app_key=settings.KIWOOM_APP_KEY or None,
                kiwoom_secret_key=settings.KIWOOM_SECRET_KEY or None,
                kiwoom_mock=settings.KIWOOM_MOCK,
            ))
            print(f"[main] admin trading_config 생성 (투자금 {settings.TOTAL_INVESTMENT:,.0f}원)")

        await db.commit()


@asynccontextmanager
async def lifespan(app: FastAPI):
    # 시작
    await run_migrations()
    await ensure_admin_user()
    scheduler = create_scheduler()
    scheduler.start()
    await kiwoom_ws.start()
    try:
        await refresh_ma20_for_active_positions()
    except Exception as e:
        print(f"[main] 초기 MA20 갱신 실패: {e}")
    print("[main] 서버 시작 완료")
    yield
    scheduler.shutdown()
    await kiwoom_ws.stop()
    print("[main] 서버 종료")


app = FastAPI(title="MK Trading System", version="1.0.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(auth_api.router)
app.include_router(dashboard.router)
app.include_router(account.router)
app.include_router(orders.router)


@app.websocket("/ws/realtime")
async def websocket_endpoint(websocket: WebSocket, token: str | None = Query(default=None)):
    # 토큰 검증 — 실패 시 연결 종료
    async with AsyncSessionLocal() as db:
        await authenticate_websocket(websocket, token, db)
    await manager.connect(websocket)
    try:
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        manager.disconnect(websocket)


@app.get("/api/health")
async def health():
    return {"status": "ok"}


# 정적 파일 서빙 (API/WS 등록 후 마지막에 마운트)
_dist = "/app/dist"
if os.path.exists(_dist):
    app.mount("/", StaticFiles(directory=_dist, html=True), name="static")
