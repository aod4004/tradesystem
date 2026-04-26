"""
키움증권 REST API 클라이언트
공식 문서: https://openapi.kiwoom.com/

특징:
- 모든 요청이 POST (조회도 POST)
- 헤더: authorization(Bearer), api-id, cont-yn, next-key
- appkey/secretkey는 토큰 발급 시에만 사용
- 계좌번호는 토큰에 귀속 — 주문/조회 body에 전달하지 않음
- HashKey 불필요

Phase 2.5 부터:
 - 시스템 키(env)는 `get_kiwoom_client()` — 스크리너/MA 등 시장 데이터 조회 fallback.
 - 유저 키는 `get_or_create_user_client(user_id, cfg)` — 레지스트리에 캐시되어 scheduler/WS/executor 가 공유.
 - 레지스트리 정리: `invalidate_user_client` (키 변경/삭제), `close_all_user_clients` (서버 종료).
"""
import asyncio
import hashlib
import random
from datetime import datetime
from typing import Any, TYPE_CHECKING
import httpx
import redis.asyncio as aioredis
from app.config import settings


# ---------------------------------------------------------------------- #
#  app_key 별 동시 요청 상한 — 키움 rate limit 공유 버킷 보호
# ---------------------------------------------------------------------- #
# 스크리너(시스템 키) 와 UI 호출(유저 키, admin 은 시스템 키와 동일)이 같은 app_key
# 를 쓰면 rate limit 을 공유한다. 이 세마포어로 app_key 별 총 동시 요청을 제한해
# 스크리닝 중에도 UI 호출이 429 없이 대기 후 처리되도록 한다.
_PER_KEY_CONCURRENCY = 3
_app_key_semaphores: dict[str, asyncio.Semaphore] = {}

# 429 재시도 정책 — 키움은 request rate 초과 시 '429 null' 을 던진다.
# Retry-After 헤더가 있으면 그 값을, 없으면 지수 백오프 + 작은 jitter.
_RATE_LIMIT_MAX_RETRIES = 5
_RATE_LIMIT_BASE_DELAY = 1.0   # 초 — 1, 2, 4, 8, 16 형태로 증가


def _get_request_semaphore(app_key: str) -> asyncio.Semaphore:
    sem = _app_key_semaphores.get(app_key)
    if sem is None:
        sem = asyncio.Semaphore(_PER_KEY_CONCURRENCY)
        _app_key_semaphores[app_key] = sem
    return sem

if TYPE_CHECKING:
    from app.db.models import UserTradingConfig


class TrId:
    # OAuth
    TOKEN_ISSUE   = "au10001"   # 접근토큰 발급
    TOKEN_REVOKE  = "au10002"   # 접근토큰 폐기

    # 종목정보 (/api/dostk/stkinfo)
    STOCK_INFO    = "ka10001"   # 주식기본정보 (현재가/시총/재무/외인 통합)
    STOCK_LIST    = "ka10099"   # 종목정보 리스트 (시장별)
    STOCK_DETAIL  = "ka10100"   # 종목정보 조회

    # 차트 (/api/dostk/chart)
    DAILY_CHART   = "ka10081"   # 주식일봉차트

    # 주문 (/api/dostk/ordr)
    BUY_ORDER     = "kt10000"   # 매수주문
    SELL_ORDER    = "kt10001"   # 매도주문
    MODIFY_ORDER  = "kt10002"   # 정정주문
    CANCEL_ORDER  = "kt10003"   # 취소주문

    # 계좌 (/api/dostk/acnt)
    DEPOSIT       = "kt00001"   # 예수금상세현황
    BALANCE       = "kt00018"   # 계좌평가잔고내역


# 엔드포인트 경로 (api-id 별)
_PATH_BY_API = {
    TrId.STOCK_INFO:   "/api/dostk/stkinfo",
    TrId.STOCK_LIST:   "/api/dostk/stkinfo",
    TrId.STOCK_DETAIL: "/api/dostk/stkinfo",
    TrId.DAILY_CHART:  "/api/dostk/chart",
    TrId.BUY_ORDER:    "/api/dostk/ordr",
    TrId.SELL_ORDER:   "/api/dostk/ordr",
    TrId.MODIFY_ORDER: "/api/dostk/ordr",
    TrId.CANCEL_ORDER: "/api/dostk/ordr",
    TrId.DEPOSIT:      "/api/dostk/acnt",
    TrId.BALANCE:      "/api/dostk/acnt",
}


def to_int(value: Any) -> int:
    """키움 응답의 +/- 부호 및 zero-padding 제거 후 int 변환"""
    if value is None or value == "":
        return 0
    s = str(value).strip().replace(",", "")
    if not s:
        return 0
    try:
        return int(float(s))
    except ValueError:
        return 0


def to_float(value: Any) -> float:
    if value is None or value == "":
        return 0.0
    s = str(value).strip().replace(",", "")
    if not s:
        return 0.0
    try:
        return float(s)
    except ValueError:
        return 0.0


class KiwoomClient:
    def __init__(
        self,
        app_key: str | None = None,
        secret_key: str | None = None,
        mock: bool | None = None,
    ):
        self._app_key = app_key if app_key is not None else settings.KIWOOM_APP_KEY
        self._secret_key = secret_key if secret_key is not None else settings.KIWOOM_SECRET_KEY
        self._mock = mock if mock is not None else settings.KIWOOM_MOCK
        self._http = httpx.AsyncClient(timeout=30)
        self._redis: aioredis.Redis | None = None

    @property
    def base_url(self) -> str:
        return "https://mockapi.kiwoom.com" if self._mock else "https://api.kiwoom.com"

    @property
    def ws_url(self) -> str:
        host = "mockapi.kiwoom.com" if self._mock else "api.kiwoom.com"
        return f"wss://{host}:10000/api/dostk/websocket"

    def _token_cache_key(self) -> str:
        """app_key 별로 토큰 캐시를 분리 — 유저별 키 교차오염 방지."""
        digest = hashlib.sha256(self._app_key.encode()).hexdigest()[:12]
        return f"kiwoom:access_token:{digest}"

    async def _get_redis(self) -> aioredis.Redis:
        if self._redis is None:
            self._redis = await aioredis.from_url(settings.REDIS_URL)
        return self._redis

    # ------------------------------------------------------------------ #
    #  인증 (au10001)
    # ------------------------------------------------------------------ #
    async def get_token(self) -> str:
        if not self._app_key or not self._secret_key:
            raise ValueError("키움 app_key/secret_key 가 설정되지 않았습니다")

        redis = await self._get_redis()
        cache_key = self._token_cache_key()
        cached = await redis.get(cache_key)
        if cached:
            return cached.decode()

        token, expires_dt = await self._issue_token()
        ttl = self._calc_ttl(expires_dt)
        await redis.setex(cache_key, ttl, token)
        return token

    async def invalidate_token(self) -> None:
        """현재 app_key 의 Redis 토큰 캐시를 즉시 삭제.

        LOGIN 8005 등 토큰 무효 신호를 받았을 때 호출. 다음 get_token() 에서
        /oauth2/token 으로 새 토큰을 받게 된다. app_key 별 캐시이므로 다른
        유저 영향 없음 (admin 키가 시스템 env 키와 동일하면 시스템 호출도
        다음 1회만 재발급 비용 — 정상 발급 가능 상태라면 무시 가능).
        """
        if not self._app_key:
            return
        try:
            redis = await self._get_redis()
            await redis.delete(self._token_cache_key())
        except Exception as e:
            print(f"[kiwoom_client] invalidate_token 실패 (무시): {e}")

    async def _issue_token(self) -> tuple[str, str]:
        url = f"{self.base_url}/oauth2/token"
        body = {
            "grant_type": "client_credentials",
            "appkey": self._app_key,
            "secretkey": self._secret_key,
        }
        resp = await self._http.post(
            url,
            headers={"Content-Type": "application/json;charset=UTF-8"},
            json=body,
        )
        resp.raise_for_status()
        data = resp.json()
        if data.get("return_code", 0) != 0:
            raise ValueError(f"토큰 발급 실패: {data.get('return_msg')}")
        return data["token"], data.get("expires_dt", "")

    @staticmethod
    def _calc_ttl(expires_dt: str) -> int:
        """expires_dt(YYYYMMDDHHMMSS) → 만료 10분 전까지의 초"""
        try:
            exp = datetime.strptime(expires_dt, "%Y%m%d%H%M%S")
            delta = (exp - datetime.now()).total_seconds() - 600
            return max(60, int(delta))
        except Exception:
            return 82800  # fallback: 23시간

    # ------------------------------------------------------------------ #
    #  공통 호출
    # ------------------------------------------------------------------ #
    async def request(
        self,
        api_id: str,
        body: dict,
        *,
        cont_yn: str = "N",
        next_key: str = "",
    ) -> dict:
        path = _PATH_BY_API.get(api_id)
        if path is None:
            raise ValueError(f"알 수 없는 api_id: {api_id}")

        token = await self.get_token()
        url = f"{self.base_url}{path}"
        headers = {
            "Content-Type": "application/json;charset=UTF-8",
            "authorization": f"Bearer {token}",
            "api-id": api_id,
            "cont-yn": cont_yn,
            "next-key": next_key,
        }

        # 429 재시도 — 세마포어 안에서 반복해 다른 호출이 먼저 통과하도록.
        # 슬롯을 점유한 채 기다리면 뒤의 호출도 block 되므로 재시도 사이엔 슬롯을 놓는다.
        attempt = 0
        while True:
            async with _get_request_semaphore(self._app_key):
                resp = await self._http.post(url, headers=headers, json=body)
            if resp.status_code != 429:
                break
            if attempt >= _RATE_LIMIT_MAX_RETRIES:
                resp.raise_for_status()
            retry_after = resp.headers.get("retry-after")
            if retry_after:
                try:
                    delay = float(retry_after)
                except ValueError:
                    delay = _RATE_LIMIT_BASE_DELAY
            else:
                delay = _RATE_LIMIT_BASE_DELAY * (2 ** attempt)
            delay += random.uniform(0, 0.3)   # jitter — 재시도 동기화 방지
            attempt += 1
            await asyncio.sleep(delay)

        resp.raise_for_status()
        data = resp.json()
        if data.get("return_code", 0) != 0:
            raise ValueError(f"API 오류 [{api_id}]: {data.get('return_msg')}")
        # 응답 헤더(cont-yn, next-key)를 함께 싣는다 — 연속조회 지원
        data["_cont_yn"] = resp.headers.get("cont-yn", "N")
        data["_next_key"] = resp.headers.get("next-key", "")
        return data

    # ------------------------------------------------------------------ #
    #  종목정보
    # ------------------------------------------------------------------ #
    async def get_stock_info(self, stock_code: str) -> dict:
        """
        주식기본정보 (ka10001)
        현재가·시총·연고저·재무(매출/영업이익/당기순이익)·외인비율 통합 조회
        """
        return await self.request(TrId.STOCK_INFO, {"stk_cd": stock_code})

    async def get_stock_list(self, market_tp: str = "0") -> list[dict]:
        """
        종목정보 리스트 (ka10099)
        market_tp: 0=코스피, 10=코스닥, 8=ETF 등
        연속조회 자동 처리하여 전체 종목 반환
        """
        out: list[dict] = []
        cont_yn, next_key = "N", ""
        while True:
            data = await self.request(
                TrId.STOCK_LIST,
                {"mrkt_tp": market_tp},
                cont_yn=cont_yn,
                next_key=next_key,
            )
            out.extend(data.get("list", []) or [])
            if data["_cont_yn"] != "Y" or not data["_next_key"]:
                break
            cont_yn, next_key = "Y", data["_next_key"]
        return out

    async def get_stock_detail(self, stock_code: str) -> dict:
        """종목정보 조회 (ka10100)"""
        return await self.request(TrId.STOCK_DETAIL, {"stk_cd": stock_code})

    # ------------------------------------------------------------------ #
    #  차트
    # ------------------------------------------------------------------ #
    async def get_daily_chart(
        self,
        stock_code: str,
        base_date: str | None = None,
        adjusted: bool = True,
    ) -> list[dict]:
        """
        주식일봉차트 (ka10081)
        최신순 정렬된 일봉 리스트 반환.
        각 항목: dt, cur_prc(종가), open_pric, high_pric, low_pric, trde_qty 등
        """
        base_dt = base_date or datetime.now().strftime("%Y%m%d")
        data = await self.request(
            TrId.DAILY_CHART,
            {
                "stk_cd": stock_code,
                "base_dt": base_dt,
                "upd_stkpc_tp": "1" if adjusted else "0",
            },
        )
        return data.get("stk_dt_pole_chart_qry", []) or []

    # ------------------------------------------------------------------ #
    #  주문
    # ------------------------------------------------------------------ #
    async def place_order(
        self,
        stock_code: str,
        order_type: str,   # "buy" | "sell"
        quantity: int,
        price: int = 0,
        trade_type: str = "0",   # 0=지정가, 3=시장가
        exchange: str = "KRX",
    ) -> dict:
        """
        지정가/시장가 주문 (kt10000 매수 / kt10001 매도)
        price=0 이면 시장가, 그 외 지정가
        """
        api_id = TrId.BUY_ORDER if order_type == "buy" else TrId.SELL_ORDER
        body = {
            "dmst_stex_tp": exchange,
            "stk_cd": stock_code,
            "ord_qty": str(quantity),
            "ord_uv": str(price) if price > 0 else "",
            "trde_tp": trade_type,
            "cond_uv": "",
        }
        return await self.request(api_id, body)

    async def cancel_order(
        self,
        orig_order_no: str,
        stock_code: str,
        cancel_qty: int = 0,   # 0 = 잔량 전부 취소
        exchange: str = "KRX",
    ) -> dict:
        """주문 취소 (kt10003)"""
        body = {
            "dmst_stex_tp": exchange,
            "orig_ord_no": orig_order_no,
            "stk_cd": stock_code,
            "cncl_qty": str(cancel_qty),
        }
        return await self.request(TrId.CANCEL_ORDER, body)

    # ------------------------------------------------------------------ #
    #  계좌
    # ------------------------------------------------------------------ #
    async def get_deposit(self, query_type: str = "3") -> dict:
        """예수금상세현황 (kt00001). query_type: 3=추정, 2=일반"""
        return await self.request(TrId.DEPOSIT, {"qry_tp": query_type})

    async def get_balance(
        self,
        query_type: str = "1",
        exchange: str = "KRX",
    ) -> dict:
        """계좌평가잔고내역 (kt00018). query_type: 1=합산, 2=개별"""
        return await self.request(
            TrId.BALANCE,
            {"qry_tp": query_type, "dmst_stex_tp": exchange},
        )

    async def close(self):
        await self._http.aclose()
        if self._redis:
            await self._redis.aclose()


# ---------------------------------------------------------------------- #
#  시스템(env) 싱글턴 — 스크리너 전용
# ---------------------------------------------------------------------- #
_system_client: KiwoomClient | None = None


def get_kiwoom_client() -> KiwoomClient:
    """env 기반 시스템 키움 클라이언트 — 스크리닝(시장 데이터)용 fallback."""
    global _system_client
    if _system_client is None:
        _system_client = KiwoomClient()
    return _system_client


def make_user_client(cfg: "UserTradingConfig") -> KiwoomClient:
    """유저별 설정으로 ephemeral 클라이언트 생성. 호출 후 close() 권장.

    registry 를 통하지 않고 단건 호출(예: 계좌 잔고 조회 API) 에서 쓴다.
    """
    return KiwoomClient(
        app_key=cfg.kiwoom_app_key or "",
        secret_key=cfg.kiwoom_secret_key or "",
        mock=cfg.kiwoom_mock,
    )


# ---------------------------------------------------------------------- #
#  유저별 클라이언트 레지스트리
# ---------------------------------------------------------------------- #
# 스케줄러/WS/executor 가 한 유저의 키로 반복 호출하므로 httpx/Redis 리소스를 공유한다.
# 키가 바뀌면 이전 클라이언트를 닫고 새로 생성.

_user_clients: dict[int, KiwoomClient] = {}


def get_or_create_user_client(user_id: int, cfg: "UserTradingConfig") -> KiwoomClient:
    """cfg 의 현재 키로 유효한 KiwoomClient 반환. 키가 바뀌었으면 교체."""
    app_key = cfg.kiwoom_app_key or ""
    secret_key = cfg.kiwoom_secret_key or ""
    existing = _user_clients.get(user_id)
    if (
        existing is not None
        and existing._app_key == app_key
        and existing._secret_key == secret_key
        and existing._mock == cfg.kiwoom_mock
    ):
        return existing
    # 키 변경 — 기존 클라이언트를 정리 후 새로 생성.
    # close 는 async 라 여기선 등록만 해두고 호출부에서 invalidate_user_client 를 거치는 게 이상적이나,
    # 호출 편의를 위해 동기로 덮어씌우고 이전 인스턴스는 GC 에 맡김 (httpx 는 __del__ 에서 정리됨).
    new_client = KiwoomClient(app_key=app_key, secret_key=secret_key, mock=cfg.kiwoom_mock)
    _user_clients[user_id] = new_client
    return new_client


async def invalidate_user_client(user_id: int) -> None:
    """유저 클라이언트를 명시적으로 종료 — 키 삭제/회원 탈퇴 시 호출."""
    client = _user_clients.pop(user_id, None)
    if client is not None:
        try:
            await client.close()
        except Exception:
            pass


async def close_all_user_clients() -> None:
    """서버 종료 시 전체 정리."""
    for user_id in list(_user_clients.keys()):
        await invalidate_user_client(user_id)
