#!/bin/bash
# 기존 서버에 트레이딩 시스템 추가 배포 스크립트
# 전제: Docker, Apache2, Node.js 이미 설치되어 있음

set -e

# ============================================================
# DuckDNS 도메인을 아래에 입력하세요 (예: mytrading.duckdns.org)
# ============================================================
TRADING_DOMAIN="${TRADING_DOMAIN:-trading-aod.duckdns.org}"
DEPLOY_DIR="/var/www/trading"
PROJECT_DIR="$(cd "$(dirname "$0")" && pwd)"

echo "================================================"
echo " 트레이딩 시스템 배포"
echo " 도메인: $TRADING_DOMAIN"
echo " 프로젝트: $PROJECT_DIR"
echo "================================================"

# ── 1. Apache 모듈 활성화 (이미 활성화된 경우 무시됨) ──────────
echo ""
echo "=== [1/6] Apache 모듈 확인 ==="
sudo a2enmod proxy proxy_http proxy_wstunnel rewrite headers 2>/dev/null || true
echo "    proxy, proxy_http, proxy_wstunnel, rewrite, headers 모듈 활성화"

# ── 2. 환경 변수 파일 ──────────────────────────────────────────
echo ""
echo "=== [2/6] 환경 변수 파일 확인 ==="
if [ ! -f "$PROJECT_DIR/.env" ]; then
    cp "$PROJECT_DIR/.env.example" "$PROJECT_DIR/.env"
    echo "    .env 파일을 생성했습니다."
    echo "    ⚠️  배포 완료 후 반드시 아래 항목을 입력하세요:"
    echo "        nano $PROJECT_DIR/.env"
    echo "        - KIWOOM_APP_KEY"
    echo "        - KIWOOM_SECRET_KEY"
    echo "        - KIWOOM_ACCOUNT_NO"
else
    echo "    기존 .env 파일 유지"
fi

# ── 3. 프론트엔드 빌드 ─────────────────────────────────────────
echo ""
echo "=== [3/6] 프론트엔드 빌드 ==="
cd "$PROJECT_DIR/frontend"
npm install --silent
npm run build
echo "    빌드 완료 → $PROJECT_DIR/dist"

# ── 4. 빌드 결과물 배포 ────────────────────────────────────────
echo ""
echo "=== [4/6] 정적 파일 배포 ==="
sudo mkdir -p "$DEPLOY_DIR"
sudo rsync -a --delete "$PROJECT_DIR/dist/" "$DEPLOY_DIR/dist/"
sudo chown -R www-data:www-data "$DEPLOY_DIR"
echo "    배포 완료 → $DEPLOY_DIR/dist"

# ── 5. Apache VirtualHost 설정 ─────────────────────────────────
echo ""
echo "=== [5/6] Apache VirtualHost 설정 ==="

# 도메인을 trading.conf에 반영
sudo sed "s|mytrading.duckdns.org|$TRADING_DOMAIN|g" \
    "$PROJECT_DIR/apache/trading.conf" \
    | sudo tee /etc/apache2/sites-available/trading.conf > /dev/null

# 기존 사이트는 건드리지 않고 trading.conf만 추가
sudo a2ensite trading.conf

# 설정 문법 검사
sudo apache2ctl configtest
sudo systemctl reload apache2
echo "    VirtualHost 등록 완료: $TRADING_DOMAIN"

# ── 6. Docker 컨테이너 실행 ────────────────────────────────────
echo ""
echo "=== [6/6] Docker 컨테이너 실행 ==="
cd "$PROJECT_DIR"
docker compose up -d --build
echo "    컨테이너 실행 완료"

# ── 완료 메시지 ────────────────────────────────────────────────
echo ""
echo "================================================"
echo " ✅ 배포 완료!"
echo "================================================"
echo ""
echo " 접속 주소: http://$TRADING_DOMAIN"
echo ""
echo " ⚠️  키움 API 키 미입력 시 작동하지 않습니다:"
echo "    nano $PROJECT_DIR/.env"
echo "    항목: KIWOOM_APP_KEY / KIWOOM_SECRET_KEY / KIWOOM_ACCOUNT_NO"
echo "    입력 후: docker compose restart backend"
echo ""
echo " 컨테이너 상태 확인: docker compose ps"
echo " 백엔드 로그 확인:   docker compose logs -f backend"
