#!/usr/bin/env python3
"""Phase 4.1 리스크 가드 E2E 테스트 스크립트.

실행: `python3 scripts/test_risk_guards.py`
  - admin 비밀번호를 getpass 로 숨김 입력 (특수문자 안전)
  - /api/auth/login 으로 토큰 획득
  - 4단계 시나리오 순차 실행 후 결과 출력

stdlib 만 사용 — 추가 의존성 없음.
"""
from __future__ import annotations

import getpass
import json
import sys
import urllib.error
import urllib.request

BASE = "https://trading-aod.duckdns.org"
ADMIN_EMAIL = "aod4004@naver.com"


def request(
    method: str,
    path: str,
    *,
    token: str | None = None,
    body: dict | None = None,
) -> tuple[int, dict]:
    url = f"{BASE}{path}"
    data = json.dumps(body).encode() if body is not None else None
    headers: dict[str, str] = {}
    if data is not None:
        headers["Content-Type"] = "application/json"
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req) as r:
            return r.status, json.load(r)
    except urllib.error.HTTPError as e:
        raw = e.read().decode(errors="replace")
        try:
            return e.code, json.loads(raw)
        except Exception:
            return e.code, {"_raw": raw[:500]}
    except Exception as e:
        return 0, {"_error": f"{type(e).__name__}: {e}"}


def show(title: str, status: int, body: dict) -> None:
    print(f"\n=== {title}")
    print(f"HTTP {status}")
    print(json.dumps(body, indent=2, ensure_ascii=False))


def main() -> None:
    pw = getpass.getpass(f"{ADMIN_EMAIL} 비밀번호: ")
    if not pw:
        print("❌ 비밀번호 미입력")
        sys.exit(1)

    status, body = request(
        "POST", "/api/auth/login",
        body={"email": ADMIN_EMAIL, "password": pw},
    )
    if status != 200 or "access_token" not in body:
        print(f"❌ 로그인 실패: HTTP {status}\n{json.dumps(body, indent=2, ensure_ascii=False)}")
        sys.exit(1)
    token = body["access_token"]
    print(f"✅ 로그인 성공 (토큰 {len(token)}자)")

    st, b = request("GET", "/api/settings/risk-guards", token=token)
    show("1) 기본값 조회 (enabled=true, default_max_position_ratio=0.1 예상)", st, b)

    st, b = request(
        "PATCH", "/api/settings/risk-guards",
        token=token, body={"daily_order_amount_limit": 10000},
    )
    show("2) 일일 금액 한도 10,000원 설정", st, b)

    st, b = request(
        "POST", "/api/orders/manual",
        token=token,
        body={"stock_code": "005930", "order_type": "buy", "quantity": 1, "price": 60000},
    )
    show("3) 60,000원 매수 주문 — HTTP 422 + guard_daily_amount 예상", st, b)

    st, b = request(
        "PATCH", "/api/settings/risk-guards",
        token=token, body={"clear_amount": True},
    )
    show("4) 한도 해제 (clear_amount)", st, b)

    print("\n✅ 시나리오 완료. 카카오톡 '나에게 보내기' 에 🛑 매수 주문 차단 메시지 수신 여부를 확인.")


if __name__ == "__main__":
    main()
