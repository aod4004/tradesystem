#!/usr/bin/env python3
"""Phase 4.2 사전 승인 모드 E2E 테스트 스크립트.

1) morning-approval 기본값 조회 (enabled=false 예상)
2) PATCH true 저장
3) GET 으로 persist 확인
4) POST /orders/approve-pending (대기 신호 없으면 submitted=0)
5) PATCH false 로 원복

실행: python3 scripts/test_morning_approval.py
"""
from __future__ import annotations

import getpass
import json
import sys
import urllib.error
import urllib.request

BASE = "https://trading-aod.duckdns.org"
ADMIN_EMAIL = "aod4004@naver.com"


def request(method, path, *, token=None, body=None):
    url = f"{BASE}{path}"
    data = json.dumps(body).encode() if body is not None else None
    headers = {}
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


def show(title, st, b):
    print(f"\n=== {title}")
    print(f"HTTP {st}")
    print(json.dumps(b, indent=2, ensure_ascii=False))


def main():
    pw = getpass.getpass(f"{ADMIN_EMAIL} 비밀번호: ")
    st, body = request("POST", "/api/auth/login", body={"email": ADMIN_EMAIL, "password": pw})
    if st != 200 or "access_token" not in body:
        print(f"❌ 로그인 실패 HTTP {st}: {body}")
        sys.exit(1)
    token = body["access_token"]
    print(f"✅ 로그인 성공")

    st, b = request("GET", "/api/settings/morning-approval", token=token)
    show("1) 기본값 조회 (enabled=false 예상)", st, b)

    st, b = request("PATCH", "/api/settings/morning-approval",
                    token=token, body={"enabled": True})
    show("2) PATCH enabled=true", st, b)

    st, b = request("GET", "/api/settings/morning-approval", token=token)
    show("3) GET 으로 persist 확인 (enabled=true 예상)", st, b)

    st, b = request("POST", "/api/orders/approve-pending", token=token)
    show("4) POST /orders/approve-pending (대기 신호 없으면 submitted=0)", st, b)

    st, b = request("PATCH", "/api/settings/morning-approval",
                    token=token, body={"enabled": False})
    show("5) PATCH enabled=false (원복)", st, b)

    print("\n✅ 시나리오 완료. 내일 08:50 KST 스케줄러가 돌 때 자동 분기 동작을 확인.")


if __name__ == "__main__":
    main()
