"""
스케줄러 — APScheduler 기반 자동 작업

스케줄:
  08:50  장 시작 전 매수 주문 자동 전송 (유저별 루프)
  15:40  장 마감 후 종목 스크리닝(글로벌) + 유저별 매수 신호 감지
  15:50  보유 종목 MA20 캐시 갱신 (5차 매도 조건용, 글로벌)
"""
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

from app.db.database import AsyncSessionLocal
from app.db.user_config import list_trading_users
from app.strategy.screener import run_screening
from app.strategy.signal import detect_buy_signals
from app.strategy.executor import execute_pending_buy_orders
from app.strategy.ma20 import refresh_ma20_for_active_positions


async def job_screening():
    print("[scheduler] 종목 스크리닝 시작")
    async with AsyncSessionLocal() as db:
        await run_screening(db)
        users = await list_trading_users(db)
        total = 0
        for u in users:
            signals = await detect_buy_signals(db, u.id)
            total += len(signals)
        print(f"[scheduler] 유저 {len(users)}명 대상 매수 신호 {total}건 생성")


async def job_morning_orders():
    print("[scheduler] 장 시작 전 매수 주문 실행")
    async with AsyncSessionLocal() as db:
        users = await list_trading_users(db)
        for u in users:
            await execute_pending_buy_orders(db, u.id)


async def job_refresh_ma20():
    print("[scheduler] MA20 캐시 갱신")
    await refresh_ma20_for_active_positions()


def create_scheduler() -> AsyncIOScheduler:
    scheduler = AsyncIOScheduler(timezone="Asia/Seoul")

    scheduler.add_job(
        job_morning_orders,
        CronTrigger(day_of_week="mon-fri", hour=8, minute=50, timezone="Asia/Seoul"),
        id="morning_orders",
        replace_existing=True,
    )
    scheduler.add_job(
        job_screening,
        CronTrigger(day_of_week="mon-fri", hour=15, minute=40, timezone="Asia/Seoul"),
        id="screening",
        replace_existing=True,
    )
    scheduler.add_job(
        job_refresh_ma20,
        CronTrigger(day_of_week="mon-fri", hour=15, minute=50, timezone="Asia/Seoul"),
        id="refresh_ma20",
        replace_existing=True,
    )

    return scheduler
