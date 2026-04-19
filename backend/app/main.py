import os
from contextlib import asynccontextmanager
from fastapi import FastAPI, Query, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from sqlalchemy import select

from app.auth import authenticate_websocket, hash_password
from app.config import settings
from app.db.database import AsyncSessionLocal, init_db
from app.db.models import User
from app.api import auth as auth_api, dashboard, account, orders
from app.scheduler.jobs import create_scheduler
from app.strategy.ma20 import refresh_ma20_for_active_positions
from app.ws.manager import manager
from app.ws.kiwoom_ws import kiwoom_ws


async def ensure_admin_user():
    """최초 부팅 시 env 기반 admin 계정 생성"""
    async with AsyncSessionLocal() as db:
        existing = (
            await db.execute(select(User).where(User.email == settings.ADMIN_EMAIL))
        ).scalar_one_or_none()
        if existing:
            return
        db.add(User(
            email=settings.ADMIN_EMAIL,
            password_hash=hash_password(settings.ADMIN_PASSWORD),
            is_admin=True,
            is_active=True,
        ))
        await db.commit()
        print(f"[main] admin 계정 생성: {settings.ADMIN_EMAIL}")


@asynccontextmanager
async def lifespan(app: FastAPI):
    # 시작
    await init_db()
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
