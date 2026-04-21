"""
스케줄러 — APScheduler 기반 자동 작업

스케줄:
  08:50  장 시작 전 매수 주문 자동 전송 (유저별 루프, 각 유저 키움 키 사용)
  15:40  장 마감 후 종목 스크리닝(글로벌, 시스템 키) + 유저별 매수 신호 감지(유저 키)
  15:50  보유 종목 MA 캐시 갱신 (MA20/60/120 터치 매도 조건용, 시스템 키)
"""
from datetime import datetime

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

from app.core.kiwoom_client import get_or_create_user_client
from app.core.notifier import notify_admins_fire, notify_user_fire
from app.db.database import AsyncSessionLocal
from app.db.user_config import (
    get_total_investment, get_trading_config, list_trading_users,
)
from app.strategy.screener import run_screening, run_condition_screening
from app.strategy.signal import detect_buy_signals
from app.strategy.executor import execute_pending_buy_orders
from app.strategy.approval import summarize_pending_signals
from app.strategy.ma20 import refresh_ma20_for_active_positions


async def job_screening():
    print("[scheduler] 종목 스크리닝 시작")
    async with AsyncSessionLocal() as db:
        # 조건식(condition_seq)이 설정된 유저가 있으면 그 조건식으로 스크리닝.
        # 첫 번째 설정된 유저(일반적으로 admin)의 조건식을 글로벌 소스로 사용.
        # 설정된 유저가 없으면 기존 전종목 스크리너로 fallback.
        users = await list_trading_users(db)
        condition_owner = None
        for u in users:
            cfg = await get_trading_config(db, u.id)
            if cfg and cfg.condition_seq is not None and cfg.kiwoom_app_key and cfg.kiwoom_secret_key:
                condition_owner = (u.id, cfg)
                break

        try:
            if condition_owner is not None:
                uid, cfg = condition_owner
                client = get_or_create_user_client(uid, cfg)
                print(f"[scheduler] 조건검색 경로 — user={uid} seq={cfg.condition_seq} ({cfg.condition_name})")
                await run_condition_screening(db, uid, client, str(cfg.condition_seq))
            else:
                print("[scheduler] 조건식 미설정 — 전종목 스크리너 fallback")
                await run_screening(db)
        except Exception as e:
            print(f"[scheduler] 스크리닝 실패: {e}")
            notify_admins_fire(
                f"🔥 스크리닝 작업 실패\n{type(e).__name__}: {e}",
                dedup_key="scheduler_screening_error",
            )
            return

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

            # Phase 4.2 — 승인 모드 on 이면 주문 전송 대신 요약 알림
            if cfg.require_morning_approval:
                try:
                    total_invest = await get_total_investment(db, u.id)
                    summary = await summarize_pending_signals(db, u.id, total_invest)
                except Exception as e:
                    print(f"[scheduler] 유저 {u.id} 대기 신호 요약 실패: {e}")
                    continue
                if summary["count"] == 0:
                    print(f"[scheduler] user={u.id} 승인 모드 — 오늘 대기 신호 없음")
                    continue
                today = datetime.utcnow().strftime("%Y-%m-%d")
                notify_user_fire(
                    u.id,
                    _format_approval_request(summary),
                    dedup_key=f"morning_approval_request:{u.id}:{today}",
                )
                print(f"[scheduler] user={u.id} 승인 대기 {summary['count']}건 요약 전송")
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


def _format_approval_request(summary: dict) -> str:
    lines = [
        "🔔 장 시작 전 승인 요청",
        f"오늘 매수 신호 {summary['count']}건 · 예정 금액 {int(summary['total_amount']):,}원",
        "",
    ]
    for s in summary["items"][:10]:
        lines.append(
            f"• {s['stock_name'] or s['stock_code']} ({s['stock_code']}) "
            f"{s['trigger_round']}차 · {s['quantity']}주 @ {s['target_order_price']:,}원"
        )
    if summary["count"] > 10:
        lines.append(f"... 외 {summary['count'] - 10}건")
    lines.append("")
    lines.append("대시보드에서 확인 후 승인/제외하세요.")
    return "\n".join(lines)


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
