"""
스케줄러 — APScheduler 기반 자동 작업

스케줄:
  08:50  장 시작 전 매수 주문 자동 전송 (유저별 루프, 각 유저 키움 키 사용)
  15:40  장 마감 후 종목 스크리닝(글로벌, 시스템 키) + 유저별 매수 신호 감지(유저 키)
  15:50  보유 종목 MA 캐시 갱신 (MA20/60/120 터치 매도 조건용, 시스템 키)
"""
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

from app.core.kiwoom_client import get_or_create_user_client
from app.core.notifier import notify_admins_fire, notify_user_fire
from app.db.database import AsyncSessionLocal
from app.db.user_config import get_trading_config, list_trading_users
from app.strategy.screener import run_screening
from app.strategy.signal import detect_buy_signals
from app.strategy.executor import execute_pending_buy_orders
from app.strategy.ma20 import refresh_ma20_for_active_positions


async def job_screening():
    print("[scheduler] 종목 스크리닝 시작")
    async with AsyncSessionLocal() as db:
        try:
            await run_screening(db)
        except Exception as e:
            print(f"[scheduler] run_screening 실패: {e}")
            notify_admins_fire(
                f"🔥 스크리닝 작업 실패\n{type(e).__name__}: {e}",
                dedup_key="scheduler_screening_error",
            )
            return
        users = await list_trading_users(db)
        total = 0
        skipped = 0
        for u in users:
            cfg = await get_trading_config(db, u.id)
            if cfg is None or not cfg.kiwoom_app_key or not cfg.kiwoom_secret_key:
                skipped += 1
                continue
            client = get_or_create_user_client(u.id, cfg)
            try:
                signals = await detect_buy_signals(db, u.id, client)
                total += len(signals)
            except Exception as e:
                print(f"[scheduler] 유저 {u.id} 신호 감지 실패: {e}")
                notify_user_fire(
                    u.id,
                    f"⚠️ 매수 신호 감지 실패\n{type(e).__name__}: {e}",
                    dedup_key=f"signal_detect_error:{u.id}",
                )
        print(
            f"[scheduler] 유저 {len(users)}명 중 {len(users) - skipped}명 대상 "
            f"매수 신호 {total}건 생성 (키 미등록 {skipped}명 스킵)"
        )


async def job_morning_orders():
    print("[scheduler] 장 시작 전 매수 주문 실행")
    async with AsyncSessionLocal() as db:
        users = await list_trading_users(db)
        for u in users:
            cfg = await get_trading_config(db, u.id)
            if cfg is None or not cfg.kiwoom_app_key or not cfg.kiwoom_secret_key:
                continue
            client = get_or_create_user_client(u.id, cfg)
            try:
                await execute_pending_buy_orders(db, u.id, client)
            except Exception as e:
                print(f"[scheduler] 유저 {u.id} 매수 실행 실패: {e}")
                notify_user_fire(
                    u.id,
                    f"⚠️ 자동 매수 실행 실패\n{type(e).__name__}: {e}",
                    dedup_key=f"morning_orders_error:{u.id}",
                )


async def job_refresh_ma20():
    print("[scheduler] MA 캐시 갱신")
    try:
        await refresh_ma20_for_active_positions()
    except Exception as e:
        print(f"[scheduler] MA 캐시 갱신 실패: {e}")
        notify_admins_fire(
            f"🔥 MA 캐시 갱신 실패\n{type(e).__name__}: {e}",
            dedup_key="ma_refresh_error",
        )


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
