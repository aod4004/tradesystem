#!/bin/bash
# 키움 조건검색 셋업 & 검증 헬퍼
#   - 로그인 토큰 발급
#   - /api/settings/condition-search GET (서버 저장 조건식 목록)
#   - seq=0 을 기본 선택값으로 PATCH
#   - /api/orders/run-screening 트리거
#
# 사용: ADMIN_EMAIL 환경변수로 이메일 전달(기본 aod4004@naver.com). 비밀번호는 대화식.
#   bash scripts/condition_setup.sh
#   ADMIN_EMAIL=other@example.com bash scripts/condition_setup.sh
#
# 조건식 seq 를 바꾸려면 CONDITION_SEQ / CONDITION_NAME 환경변수.
set -e

BASE="${BASE:-http://localhost:8000}"
ADMIN_EMAIL="${ADMIN_EMAIL:-aod4004@naver.com}"
CONDITION_SEQ="${CONDITION_SEQ:-0}"
CONDITION_NAME="${CONDITION_NAME:-5P_Auto Filtering}"

read -sp "Password for ${ADMIN_EMAIL}: " PW
echo
TOKEN=$(curl -s -X POST "${BASE}/api/auth/login" \
  -H "Content-Type: application/json" \
  -d "{\"email\":\"${ADMIN_EMAIL}\",\"password\":\"${PW}\"}" \
  | python3 -c 'import sys,json; print(json.load(sys.stdin)["access_token"])')
unset PW

if [ -z "${TOKEN}" ]; then
  echo "[FAIL] 토큰 발급 실패 — 이메일/비밀번호 확인"
  exit 1
fi
echo "[ok] TOKEN length=${#TOKEN}"

echo
echo "=== 1) 조건식 목록 조회 (GET /api/settings/condition-search) ==="
curl -s -H "Authorization: Bearer ${TOKEN}" "${BASE}/api/settings/condition-search" | python3 -m json.tool

echo
echo "=== 2) seq=${CONDITION_SEQ} 선택 (PATCH) ==="
curl -s -X PATCH -H "Authorization: Bearer ${TOKEN}" -H "Content-Type: application/json" \
  -d "{\"seq\":${CONDITION_SEQ},\"name\":\"${CONDITION_NAME}\"}" \
  "${BASE}/api/settings/condition-search" | python3 -m json.tool

echo
echo "=== 3) 수동 스크리닝 트리거 (POST /api/orders/run-screening) ==="
curl -s -X POST -H "Authorization: Bearer ${TOKEN}" "${BASE}/api/orders/run-screening" | python3 -m json.tool

echo
echo "[hint] 진행 상황:  watch -n2 'curl -s -H \"Authorization: Bearer ${TOKEN}\" ${BASE}/api/orders/run-screening/status | python3 -m json.tool'"
echo "[hint] 로그:       docker compose logs -f backend | grep -E 'condition_screener|signal'"
