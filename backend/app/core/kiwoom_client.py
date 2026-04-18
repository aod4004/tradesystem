"""
키움증권 REST API 클라이언트
공식 문서: https://openapi.kiwoom.com/guide/index
"""
import hashlib
import json
import asyncio
from datetime import datetime, timedelta
from typing import Any
import httpx
import redis.asyncio as aioredis
from app.config import settings

# ============================================================
# TR_ID 상수 (공식 문서에서 확인 후 업데이트 필요)
# https://openapi.kiwoom.com/guide/apiDocs
# ============================================================
class TrId:
    # 시세 조회
    STOCK_PRICE      = "FHKST01010100"   # 주식 현재가 시세
    DAILY_CHART      = "FHKST03010100"   # 주식 일봉 차트
    STOCK_INFO       = "FHKST01010500"   # 주식 기본 정보
    FINANCIAL_INFO   = "FHKST66430200"   # 재무 정보 (순이익, 영업이익)
    FOREIGN_RATIO    = "FHKST01010300"   # 외국인 비율
    STOCK_LIST       = "FHKST03030100"   # 전체 종목 목록

    # 주문 (모의: V, 실거래: T)
    BUY_ORDER_REAL   = "TTTC0802U"       # 매수 주문 (실거래)
    SELL_ORDER_REAL  = "TTTC0801U"       # 매도 주문 (실거래)
    BUY_ORDER_MOCK   = "VTTC0802U"       # 매수 주문 (모의)
    SELL_ORDER_MOCK  = "VTTC0801U"       # 매도 주문 (모의)
    CANCEL_ORDER     = "TTTC0803U"       # 주문 취소

    # 계좌 조회
    BALANCE          = "TTTC8434R"       # 계좌 잔고
    DEPOSIT          = "TTTC8908R"       # 예수금


class KiwoomClient:
    def __init__(self):
        self._http = httpx.AsyncClient(timeout=30)
        self._redis: aioredis.Redis | None = None
        self._token: str | None = None
        self._token_expires: datetime | None = None

    async def _get_redis(self) -> aioredis.Redis:
        if self._redis is None:
            self._redis = await aioredis.from_url(settings.REDIS_URL)
        return self._redis

    # ------------------------------------------------------------------ #
    #  인증
    # ------------------------------------------------------------------ #
    async def get_token(self) -> str:
        """액세스 토큰 반환 (Redis 캐싱, 자동 갱신)"""
        redis = await self._get_redis()
        cached = await redis.get("kiwoom:access_token")
        if cached:
            return cached.decode()

        token = await self._issue_token()
        # 만료 1시간 전에 갱신되도록 TTL 설정 (기본 24시간 - 1시간)
        await redis.setex("kiwoom:access_token", 82800, token)
        return token

    async def _issue_token(self) -> str:
        url = f"{settings.KIWOOM_BASE_URL}/oauth2/tokenP"
        body = {
            "grant_type": "client_credentials",
            "appkey": settings.KIWOOM_APP_KEY,
            "secretkey": settings.KIWOOM_SECRET_KEY,
        }
        resp = await self._http.post(url, json=body)
        resp.raise_for_status()
        data = resp.json()
        return data["access_token"]

    async def _get_hashkey(self, body: dict) -> str:
        """주문 보안을 위한 HashKey 발급"""
        token = await self.get_token()
        url = f"{settings.KIWOOM_BASE_URL}/uapi/hashkey"
        headers = self._base_headers(token)
        resp = await self._http.post(url, headers=headers, json=body)
        resp.raise_for_status()
        return resp.json()["HASH"]

    def _base_headers(self, token: str, tr_id: str = "") -> dict:
        return {
            "Authorization": f"Bearer {token}",
            "appkey": settings.KIWOOM_APP_KEY,
            "secretkey": settings.KIWOOM_SECRET_KEY,
            "tr_id": tr_id,
            "Content-Type": "application/json; charset=utf-8",
        }

    # ------------------------------------------------------------------ #
    #  공통 요청
    # ------------------------------------------------------------------ #
    async def get(self, path: str, tr_id: str, params: dict) -> dict:
        token = await self.get_token()
        headers = self._base_headers(token, tr_id)
        url = f"{settings.KIWOOM_BASE_URL}{path}"
        resp = await self._http.get(url, headers=headers, params=params)
        resp.raise_for_status()
        data = resp.json()
        if data.get("return_code", 0) != 0:
            raise ValueError(f"API 오류 [{tr_id}]: {data.get('return_msg')}")
        return data

    async def post(self, path: str, tr_id: str, body: dict, use_hashkey: bool = False) -> dict:
        token = await self.get_token()
        headers = self._base_headers(token, tr_id)
        if use_hashkey:
            headers["hashkey"] = await self._get_hashkey(body)
        url = f"{settings.KIWOOM_BASE_URL}{path}"
        resp = await self._http.post(url, headers=headers, json=body)
        resp.raise_for_status()
        data = resp.json()
        if data.get("return_code", 0) != 0:
            raise ValueError(f"API 오류 [{tr_id}]: {data.get('return_msg')}")
        return data

    # ------------------------------------------------------------------ #
    #  시세 조회
    # ------------------------------------------------------------------ #
    async def get_current_price(self, stock_code: str) -> dict:
        """주식 현재가 조회"""
        return await self.get(
            "/uapi/domestic-stock/v1/quotations/inquire-price",
            TrId.STOCK_PRICE,
            {"fid_cond_mrkt_div_code": "J", "fid_input_iscd": stock_code},
        )

    async def get_daily_chart(self, stock_code: str, period_div: str = "D") -> dict:
        """일봉 차트 조회 (period_div: D=일, W=주, M=월)"""
        today = datetime.now().strftime("%Y%m%d")
        year_ago = (datetime.now() - timedelta(days=365)).strftime("%Y%m%d")
        return await self.get(
            "/uapi/domestic-stock/v1/quotations/inquire-daily-itemchartprice",
            TrId.DAILY_CHART,
            {
                "fid_cond_mrkt_div_code": "J",
                "fid_input_iscd": stock_code,
                "fid_input_date_1": year_ago,
                "fid_input_date_2": today,
                "fid_period_div_code": period_div,
                "fid_org_adj_prc": "1",
            },
        )

    async def get_stock_info(self, stock_code: str) -> dict:
        """종목 기본 정보 (시가총액, 주가 등)"""
        return await self.get(
            "/uapi/domestic-stock/v1/quotations/inquire-search-stock-info",
            TrId.STOCK_INFO,
            {"fid_input_iscd": stock_code},
        )

    async def get_financial_info(self, stock_code: str) -> dict:
        """재무 정보 (순이익, 영업이익)"""
        return await self.get(
            "/uapi/domestic-stock/v1/quotations/inquire-financial-info",
            TrId.FINANCIAL_INFO,
            {"fid_input_iscd": stock_code},
        )

    async def get_foreign_ratio(self, stock_code: str) -> dict:
        """외국인 보유 비율"""
        return await self.get(
            "/uapi/domestic-stock/v1/quotations/inquire-foreign-institution",
            TrId.FOREIGN_RATIO,
            {"fid_input_iscd": stock_code},
        )

    async def get_stock_list(self, market: str = "0") -> dict:
        """전체 종목 목록 (market: 0=KOSPI, 1=KOSDAQ)"""
        return await self.get(
            "/uapi/domestic-stock/v1/quotations/inquire-stock-list",
            TrId.STOCK_LIST,
            {"fid_cond_mrkt_div_code": "J", "fid_blng_cls_code": market},
        )

    # ------------------------------------------------------------------ #
    #  주문
    # ------------------------------------------------------------------ #
    async def place_order(
        self,
        account_no: str,
        stock_code: str,
        order_type: str,   # "buy" | "sell"
        quantity: int,
        price: int,
    ) -> dict:
        """지정가 주문"""
        if order_type == "buy":
            tr_id = TrId.BUY_ORDER_MOCK if settings.KIWOOM_MOCK else TrId.BUY_ORDER_REAL
            side_code = "02"   # 매수
        else:
            tr_id = TrId.SELL_ORDER_MOCK if settings.KIWOOM_MOCK else TrId.SELL_ORDER_REAL
            side_code = "01"   # 매도

        body = {
            "CANO": account_no[:8],
            "ACNT_PRDT_CD": account_no[8:],
            "PDNO": stock_code,
            "ORD_DVSN": "00",          # 지정가
            "ORD_QTY": str(quantity),
            "ORD_UNPR": str(price),
            "CTAC_TLNO": "",
        }
        return await self.post(
            "/uapi/domestic-stock/v1/trading/order-cash",
            tr_id,
            body,
            use_hashkey=True,
        )

    async def cancel_order(self, account_no: str, org_order_no: str, quantity: int) -> dict:
        """주문 취소"""
        body = {
            "CANO": account_no[:8],
            "ACNT_PRDT_CD": account_no[8:],
            "KRX_FWDG_ORD_ORGNO": "",
            "ORGN_ODNO": org_order_no,
            "ORD_DVSN": "00",
            "RVSE_CNCL_DVSN_CD": "02",  # 취소
            "ORD_QTY": str(quantity),
            "ORD_UNPR": "0",
        }
        return await self.post(
            "/uapi/domestic-stock/v1/trading/order-rvsecncl",
            TrId.CANCEL_ORDER,
            body,
            use_hashkey=True,
        )

    # ------------------------------------------------------------------ #
    #  계좌 조회
    # ------------------------------------------------------------------ #
    async def get_balance(self, account_no: str) -> dict:
        """계좌 잔고 조회"""
        return await self.get(
            "/uapi/domestic-stock/v1/trading/inquire-balance",
            TrId.BALANCE,
            {
                "CANO": account_no[:8],
                "ACNT_PRDT_CD": account_no[8:],
                "AFHR_FLPR_YN": "N",
                "OFL_YN": "N",
                "INQR_DVSN": "01",
                "UNPR_DVSN": "01",
                "FUND_STTL_ICLD_YN": "N",
                "FNCG_AMT_AUTO_RDPT_YN": "N",
                "PRCS_DVSN": "01",
                "CTX_AREA_FK100": "",
                "CTX_AREA_NK100": "",
            },
        )

    async def get_deposit(self, account_no: str) -> dict:
        """예수금 조회"""
        return await self.get(
            "/uapi/domestic-stock/v1/trading/inquire-psbl-order",
            TrId.DEPOSIT,
            {
                "CANO": account_no[:8],
                "ACNT_PRDT_CD": account_no[8:],
                "PDNO": "005930",
                "ORD_UNPR": "0",
                "ORD_DVSN": "01",
                "CMA_EVLU_AMT_ICLD_YN": "Y",
                "OVRS_ICLD_YN": "N",
            },
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
