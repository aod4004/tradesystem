#!/usr/bin/env python3
"""키움 REST API 조건검색(CNSRLST/CNSRREQ) 동작 검증 스크립트.

목적: 모의투자 환경에서 WebSocket 기반 조건검색이 실제로 돌아가는지,
      영웅문4 에서 사전 저장된 조건식이 있는지 확인.

실행 (서버에서, 컨테이너 안):
    docker compose exec backend python scripts/test_condition_search.py [유저ID]

기본 유저ID=1 (admin). 다른 유저로 테스트하려면 인자로 전달.

동작:
 1) user_trading_config 에서 해당 유저의 키움 키 로드
 2) 키움 WS 접속 → LOGIN 전문 전송 → 응답 대기
 3) CNSRLST 전송 → 저장된 조건식 목록 덤프
 4) 조건식이 있으면 첫 번째 seq 로 CNSRREQ 전송 → 결과 종목 수 출력
 5) 연결 종료

조건식이 없으면 영웅문4 에서 먼저 조건식을 만들어야 함 — 이 스크립트가 알려줌.
"""
from __future__ import annotations

import asyncio
import json
import sys

import websockets
from sqlalchemy import select

from app.db.database import AsyncSessionLocal
from app.db.models import UserTradingConfig
from app.core.kiwoom_client import KiwoomClient


async def main(user_id: int) -> int:
    async with AsyncSessionLocal() as db:
        cfg = (
            await db.execute(
                select(UserTradingConfig).where(UserTradingConfig.user_id == user_id)
            )
        ).scalar_one_or_none()

    if cfg is None or not cfg.kiwoom_app_key or not cfg.kiwoom_secret_key:
        print(f"[FAIL] user={user_id} 키움 키가 등록되지 않음")
        return 2

    client = KiwoomClient(
        app_key=cfg.kiwoom_app_key,
        secret_key=cfg.kiwoom_secret_key,
        mock=cfg.kiwoom_mock,
    )
    try:
        token = await client.get_token()
    except Exception as e:
        print(f"[FAIL] 토큰 발급 실패: {e}")
        return 3

    url = client.ws_url
    print(f"[info] 접속 URL: {url} (mock={cfg.kiwoom_mock})")

    async with websockets.connect(url, ping_interval=20, ping_timeout=20) as ws:
        # 1) LOGIN
        await ws.send(json.dumps({"trnm": "LOGIN", "token": token}))
        while True:
            raw = await ws.recv()
            msg = json.loads(raw)
            if msg.get("trnm") == "LOGIN":
                if msg.get("return_code", 0) != 0:
                    print(f"[FAIL] LOGIN 실패: {msg.get('return_msg')}")
                    return 4
                print("[ok] LOGIN 성공")
                break

        # 2) CNSRLST — 서버에 저장된 조건식 목록
        await ws.send(json.dumps({"trnm": "CNSRLST"}))
        cnsrlst_msg = None
        for _ in range(20):
            raw = await asyncio.wait_for(ws.recv(), timeout=10)
            msg = json.loads(raw)
            if msg.get("trnm") == "PING":
                await ws.send(raw)
                continue
            if msg.get("trnm") == "CNSRLST":
                cnsrlst_msg = msg
                break
        if cnsrlst_msg is None:
            print("[FAIL] CNSRLST 응답이 오지 않음 (타임아웃)")
            return 5

        print("\n=== CNSRLST 응답 ===")
        print(json.dumps(cnsrlst_msg, ensure_ascii=False, indent=2))

        if cnsrlst_msg.get("return_code", 0) != 0:
            print(f"\n[FAIL] CNSRLST return_code != 0: {cnsrlst_msg.get('return_msg')}")
            return 6

        conditions = cnsrlst_msg.get("data") or []
        if not conditions:
            print(
                "\n[warn] 저장된 조건식이 0개 — 영웅문4(HTS) 에서 조건식을 먼저 만들고 저장해야 합니다.\n"
                "       접속: 영웅문4 → [0150] 조건검색 → 조건 작성 → 이름 지정 → 서버 저장."
            )
            return 0

        print(f"\n[ok] 저장된 조건식 {len(conditions)}개:")
        for c in conditions:
            print(f"  seq={c.get('seq')}  name={c.get('name')}")

        # 3) 첫 번째 조건식으로 CNSRREQ — 결과 종목 수 확인
        seq = conditions[0].get("seq")
        print(f"\n[info] seq={seq} 로 CNSRREQ 테스트 (search_type=0, stex_tp=K)")
        await ws.send(json.dumps({
            "trnm": "CNSRREQ",
            "seq": str(seq),
            "search_type": "0",   # 0: 일반 조건검색, 1: 실시간
            "stex_tp": "K",       # KRX
        }))
        cnsrreq_msg = None
        for _ in range(20):
            raw = await asyncio.wait_for(ws.recv(), timeout=15)
            msg = json.loads(raw)
            if msg.get("trnm") == "PING":
                await ws.send(raw)
                continue
            if msg.get("trnm") == "CNSRREQ":
                cnsrreq_msg = msg
                break
        if cnsrreq_msg is None:
            print("[FAIL] CNSRREQ 응답이 오지 않음 (타임아웃)")
            return 7

        if cnsrreq_msg.get("return_code", 0) != 0:
            print(f"[FAIL] CNSRREQ return_code != 0: {cnsrreq_msg.get('return_msg')}")
            print(json.dumps(cnsrreq_msg, ensure_ascii=False, indent=2))
            return 8

        items = cnsrreq_msg.get("data") or []
        print(f"[ok] CNSRREQ 응답 — 매칭 종목 {len(items)}개")
        print("\n=== CNSRREQ 첫 5건 샘플 ===")
        for it in items[:5]:
            print(f"  {it}")

    return 0


if __name__ == "__main__":
    uid = int(sys.argv[1]) if len(sys.argv) > 1 else 1
    sys.exit(asyncio.run(main(uid)))
