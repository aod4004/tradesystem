#!/bin/bash
# 기존 서버에 트레이딩 시스템 추가 배포 스크립트
# 전제: Docker, Node.js 이미 설치 / 웹서버(Caddy 등)는 별도 구성 관리

set -e

DEPLOY_DIR="/var/www/trading"
PROJECT_DIR="$(cd "$(dirname "$0")" && pwd)"

echo "================================================"
echo " 트레이딩 시스템 배포"
echo " 프로젝트: $PROJECT_DIR"
echo "================================================"

# ── 1. 환경 변수 파일 ──────────────────────────────────────────
echo ""
echo "=== [1/4] 환경 변수 파일 확인 ==="
if [ ! -f "$PROJECT_DIR/.env" ]; then
    cp "$PROJECT_DIR/.env.example" "$PROJECT_DIR/.env"
    echo "    .env 파일을 생성했습니다."
    echo "    ⚠️  배포 완료 후 반드시 아래 항목을 입력하세요:"
    echo "        nano $PROJECT_DIR/.env"
    echo "        - KIWOOM_APP_KEY"
    echo "        - KIWOOM_SECRET_KEY"
else
    echo "    기존 .env 파일 유지"
fi

# ── 2. 프론트엔드 빌드 ─────────────────────────────────────────
echo ""
echo "=== [2/4] 프론트엔드 빌드 ==="
cd "$PROJECT_DIR/frontend"
npm install --silent
npm run build
echo "    빌드 완료 → $PROJECT_DIR/dist"

# ── 3. 빌드 결과물 배포 ────────────────────────────────────────
echo ""
echo "=== [3/4] 정적 파일 배포 ==="
sudo mkdir -p "$DEPLOY_DIR"
sudo rsync -a --delete "$PROJECT_DIR/dist/" "$DEPLOY_DIR/dist/"
sudo chown -R www-data:www-data "$DEPLOY_DIR"
echo "    배포 완료 → $DEPLOY_DIR/dist"

# ── 4. Docker 컨테이너 실행 ────────────────────────────────────
echo ""
echo "=== [4/4] Docker 컨테이너 실행 ==="
cd "$PROJECT_DIR"
docker compose up -d --build
echo "    컨테이너 실행 완료"

# ── 완료 메시지 ────────────────────────────────────────────────
echo ""
echo "================================================"
echo " ✅ 배포 완료!"
echo "================================================"
echo ""
echo " 웹서버(Caddy 등) 리버스 프록시 설정은 별도 관리:"
echo "   - 정적 파일 루트: $DEPLOY_DIR/dist"
echo "   - 백엔드 업스트림: http://127.0.0.1:8000"
echo "   - /api, /ws 경로를 백엔드로 프록시"
echo ""
echo " ⚠️  키움 API 키 미입력 시 작동하지 않습니다:"
echo "    nano $PROJECT_DIR/.env"
echo "    항목: KIWOOM_APP_KEY / KIWOOM_SECRET_KEY"
echo "    입력 후: docker compose restart backend"
echo ""
echo " 컨테이너 상태 확인: docker compose ps"
echo " 백엔드 로그 확인:   docker compose logs -f backend"
