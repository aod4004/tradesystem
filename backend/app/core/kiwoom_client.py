"""
키움증권 REST API 클라이언트
공식 문서: https://openapi.kiwoom.com/

특징:
- 모든 요청이 POST (조회도 POST)
- 헤더: authorization(Bearer), api-id, cont-yn, next-key
- appkey/secretkey는 토큰 발급 시에만 사용
- 계좌번호는 토큰에 귀속 — 주문/조회 body에 전달하지 않음
- HashKey 불필요
"""
from datetime import datetime
from typing import Any
import httpx
import redis.asyncio as aioredis
from app.config import settings


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
    def __init__(self):
        self._http = httpx.AsyncClient(timeout=30)
        self._redis: aioredis.Redis | None = None

    async def _get_redis(self) -> aioredis.Redis:
        if self._redis is None:
            self._redis = await aioredis.from_url(settings.REDIS_URL)
        return self._redis

    # ------------------------------------------------------------------ #
    #  인증 (au10001)
    # ------------------------------------------------------------------ #
    async def get_token(self) -> str:
        redis = await self._get_redis()
        cached = await redis.get("kiwoom:access_token")
        if cached:
            return cached.decode()

        token, expires_dt = await self._issue_token()
        ttl = self._calc_ttl(expires_dt)
        await redis.setex("kiwoom:access_token", ttl, token)
        return token

    async def _issue_token(self) -> tuple[str, str]:
        url = f"{settings.KIWOOM_BASE_URL}/oauth2/token"
        body = {
            "grant_type": "client_credentials",
            "appkey": settings.KIWOOM_APP_KEY,
            "secretkey": settings.KIWOOM_SECRET_KEY,
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
        url = f"{settings.KIWOOM_BASE_URL}{path}"
        headers = {
            "Content-Type": "application/json;charset=UTF-8",
            "authorization": f"Bearer {token}",
            "api-id": api_id,
            "cont-yn": cont_yn,
            "next-key": next_key,
        }
        resp = await self._http.post(url, headers=headers, json=body)
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


# 싱글턴
_client: KiwoomClient | None = None


def get_kiwoom_client() -> KiwoomClient:
    global _client
    if _client is None:
        _client = KiwoomClient()
    return _client
