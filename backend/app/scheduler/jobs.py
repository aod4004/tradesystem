"""
스케줄러 — APScheduler 기반 자동 작업

스케줄:
  15:40  장 마감 후 종목 스크리닝 + 매수 신호 감지
  08:50  장 시작 전 매수 주문 자동 전송
"""
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.database import AsyncSessionLocal
from app.strategy.screener import run_screening
from app.strategy.signal import detect_buy_signals
from app.strategy.executor import execute_pending_buy_orders


async def job_screening():
    """15:40 — 종목 스크리닝 + 매수 신호 감지"""
    print("[scheduler] 종목 스크리닝 시작")
    async with AsyncSessionLocal() as db:
        await run_screening(db)
        signals = await detect_buy_signals(db)
        print(f"[scheduler] 매수 신호 {len(signals)}건 생성")


async def job_morning_orders():
    """08:50 — 전일 감지된 매수 신호 주문 전송"""
    print("[scheduler] 장 시작 전 매수 주문 실행")
    async with AsyncSessionLocal() as db:
        await execute_pending_buy_orders(db)


def create_scheduler() -> AsyncIOScheduler:
    scheduler = AsyncIOScheduler(timezone="Asia/Seoul")

    # 평일(월~금) 15:40 스크리닝
    scheduler.add_job(
        job_screening,
        CronTrigger(day_of_week="mon-fri", hour=15, minute=40, timezone="Asia/Seoul"),
        id="screening",
        replace_existing=True,
    )

    # 평일(월~금) 08:50 매수 주문
    scheduler.add_job(
        job_morning_orders,
        CronTrigger(day_of_week="mon-fri", hour=8, minute=50, timezone="Asia/Seoul"),
        id="morning_orders",
        replace_existing=True,
    )

    return scheduler
