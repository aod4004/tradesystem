import os
from contextlib import asynccontextmanager

import redis.asyncio as aioredis
from fastapi import FastAPI, Query, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from sqlalchemy import select, text

from app.auth import authenticate_websocket, hash_password
from app.config import settings
from app.core.kiwoom_client import close_all_user_clients, get_kiwoom_client
from app.core.notifier import notifier as kakao_notifier
from app.db.database import AsyncSessionLocal
from app.db.models import User, UserTradingConfig
from app.api import (
    auth as auth_api, dashboard, account, orders,
    settings as settings_api, watchlist as watchlist_api,
)
from app.scheduler.jobs import create_scheduler
from app.strategy.ma20 import refresh_ma20_for_active_positions
from app.ws.manager import manager
from app.ws.kiwoom_ws import kiwoom_pool


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
    # Alembic 은 컨테이너 엔트리포인트에서 uvicorn 전에 수행된다 (Dockerfile 참고).
    await ensure_admin_user()
    scheduler = create_scheduler()
    scheduler.start()
    # 키 등록된 모든 유저의 키움 WS 시작 (각 유저 토큰 사용)
    try:
        await kiwoom_pool.start()
    except Exception as e:
        print(f"[main] 키움 풀 시작 실패: {e}")
    try:
        await refresh_ma20_for_active_positions()
    except Exception as e:
        print(f"[main] 초기 MA 갱신 실패: {e}")
    print("[main] 서버 시작 완료")
    yield
    scheduler.shutdown()
    await kiwoom_pool.stop()
    await close_all_user_clients()
    await kakao_notifier.close()
    print("[main] 서버 종료")


app = FastAPI(title="5P Trading System", version="1.0.0", lifespan=lifespan)

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
app.include_router(settings_api.router)
app.include_router(watchlist_api.router)


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
    """컴포넌트별 상태 — DB / Redis / 키움 시스템 토큰 / 키움 WS 풀."""
    components: dict[str, dict] = {}

    # DB ----
    try:
        async with AsyncSessionLocal() as db:
            await db.execute(text("SELECT 1"))
        components["db"] = {"ok": True}
    except Exception as e:
        components["db"] = {"ok": False, "error": str(e)}

    # Redis ----
    try:
        r = await aioredis.from_url(settings.REDIS_URL)
        pong = await r.ping()
        await r.aclose()
        components["redis"] = {"ok": bool(pong)}
    except Exception as e:
        components["redis"] = {"ok": False, "error": str(e)}

    # 키움 시스템 토큰 (env 키) ----
    kiwoom_info: dict = {"ok": False}
    if settings.KIWOOM_APP_KEY and settings.KIWOOM_SECRET_KEY:
        try:
            client = get_kiwoom_client()
            token = await client.get_token()
            kiwoom_info = {"ok": bool(token), "mock": settings.KIWOOM_MOCK}
        except Exception as e:
            kiwoom_info = {"ok": False, "error": str(e)}
    else:
        kiwoom_info = {"ok": False, "error": "system keys not configured"}
    components["kiwoom_system"] = kiwoom_info

    # 키움 WS 풀 ----
    components["kiwoom_ws_pool"] = {
        "ok": True,
        "connections": len(kiwoom_pool._connections),
        "users": sorted(kiwoom_pool._connections.keys()),
    }

    # 알림(카카오) ----
    components["kakao"] = {
        "configured": bool(settings.KAKAO_REST_API_KEY and settings.KAKAO_REDIRECT_URI),
    }

    overall = all(v.get("ok", True) for v in components.values() if isinstance(v, dict))
    return {"status": "ok" if overall else "degraded", "components": components}


# 정적 파일 서빙 (API/WS 등록 후 마지막에 마운트)
_dist = "/app/dist"
if os.path.exists(_dist):
    app.mount("/", StaticFiles(directory=_dist, html=True), name="static")
