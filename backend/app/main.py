import os
from contextlib import asynccontextmanager
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from app.db.database import init_db
from app.api import dashboard, account, orders
from app.scheduler.jobs import create_scheduler
from app.strategy.ma20 import refresh_ma20_for_active_positions
from app.ws.manager import manager
from app.ws.kiwoom_ws import kiwoom_ws


@asynccontextmanager
async def lifespan(app: FastAPI):
    # 시작
    await init_db()
    scheduler = create_scheduler()
    scheduler.start()
    await kiwoom_ws.start()
    # 보유 종목이 있으면 즉시 MA20 캐시 구축 (스케줄러는 15:50 동작)
    try:
        await refresh_ma20_for_active_positions()
    except Exception as e:
        print(f"[main] 초기 MA20 갱신 실패: {e}")
    print("[main] 서버 시작 완료")
    yield
    # 종료
    scheduler.shutdown()
    await kiwoom_ws.stop()
    print("[main] 서버 종료")


app = FastAPI(title="Stock Auto Trading", version="1.0.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(dashboard.router)
app.include_router(account.router)
app.include_router(orders.router)


@app.websocket("/ws/realtime")
async def websocket_endpoint(websocket: WebSocket):
    await manager.connect(websocket)
    try:
        while True:
            await websocket.receive_text()   # 연결 유지
    except WebSocketDisconnect:
        manager.disconnect(websocket)


@app.get("/api/health")
async def health():
    return {"status": "ok"}


# 정적 파일 서빙 (API 라우터 등록 후 마지막에 마운트)
_dist = "/app/dist"
if os.path.exists(_dist):
    app.mount("/", StaticFiles(directory=_dist, html=True), name="static")
