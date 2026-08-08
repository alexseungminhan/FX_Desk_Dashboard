"""FastAPI real-time FX Desk Board.

Serves the static frontend and pushes live Yahoo Finance snapshots to
every connected WebSocket client on a fixed polling interval.
"""
from __future__ import annotations

import asyncio
import logging
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse

import chart_range
import indicator_detail
import naver_search
import seibro_custody
import stock_detail
import ttl_cache
import us_search
import us_stock_detail
from market_data import MarketData

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("fx-desk-board")

PRICE_POLL_SECONDS = 10
NEWS_POLL_SECONDS = 300
KR_RATES_POLL_SECONDS = 600
MOVERS_POLL_SECONDS = 60
KR_MOST_TRADED_POLL_SECONDS = 60
US_MOVERS_POLL_SECONDS = 90
# 수급은 일별 확정치라 자주 볼 이유가 없고, 키워드 뉴스는 검색 스로틀 때문에
# 한 바퀴가 길다 — 둘 다 느리게 돈다. 베이시스는 현·선물 가격이라 시세급.
INVESTOR_FLOW_POLL_SECONDS = 300
KEYWORD_NEWS_POLL_SECONDS = 600
KEYWORD_NEWS_START_DELAY_SECONDS = 25
BASIS_POLL_SECONDS = 60
# SEIBro·KOFIA 는 일별 확정치라 자주 부를 이유가 없다.
DAILY_SOURCES_POLL_SECONDS = 1800

PROJECT_ROOT = Path(__file__).resolve().parent.parent
FRONTEND_DIR = PROJECT_ROOT / "frontend"
MAIN_HTML = PROJECT_ROOT / "FX Desk Board.html"

app = FastAPI(title="FX Desk Board")
market = MarketData()


# 매 틱 갱신되지만 그 자체로는 화면 수치를 바꾸지 않는 키.
_TIMESTAMP_KEYS = {"asOf", "moversAsOf"}


class ConnectionManager:
    """스냅샷 전체가 아니라 직전 틱과 달라진 섹션만 내보낸다.

    스냅샷은 210KB인데 10초 사이에 실제로 바뀌는 건 시세 몇 KB뿐이다
    (뉴스는 300초, 채권 수급은 1800초마다 갱신되므로 각각 30번·180번씩
    같은 내용이 다시 실려 나가고 있었다). 스킴별로 마지막에 내보낸 스냅샷을
    들고 있다가 최상위 키 단위로 비교해, 바뀐 키만 patch 로 보낸다.
    """

    def __init__(self) -> None:
        self.active: set[WebSocket] = set()
        self.scheme: dict[WebSocket, str] = {}
        # 델타의 기준선. 스킴("kr"/"us")당 하나씩만 쌓인다.
        self.last_sent: dict[str, dict] = {}
        # 델타를 이해하지 못하는 접속자(캐시에 남은 옛 app.js). 배포 직후
        # 열려 있던 탭이 깨지지 않도록 이쪽엔 통짜 스냅샷을 계속 보낸다.
        self.legacy: set[WebSocket] = set()

    async def connect(self, ws: WebSocket, scheme: str, delta: bool) -> None:
        await ws.accept()
        self.active.add(ws)
        self.scheme[ws] = scheme
        if not delta:
            self.legacy.add(ws)
        # legacy 가 하나라도 잡히면 옛 app.js 가 캐시에 남아 있다는 뜻이고,
        # 그 접속자는 여전히 틱마다 통짜 스냅샷을 받아 간다. 대역폭이 안 줄면
        # 여기부터 본다.
        log.info(
            "ws connect scheme=%s delta=%s · clients=%d (legacy=%d)",
            scheme, delta, len(self.active), len(self.legacy),
        )

    def disconnect(self, ws: WebSocket) -> None:
        self.active.discard(ws)
        self.scheme.pop(ws, None)
        self.legacy.discard(ws)

    @staticmethod
    def _diff(old: dict | None, new: dict) -> dict:
        if old is None:
            return new
        return {k: v for k, v in new.items() if old.get(k) != v}

    async def send_full(self, ws: WebSocket) -> None:
        """접속 직후 1회. 이후로는 broadcast 가 달라진 것만 덧발라 준다."""
        scheme = self.scheme.get(ws, "kr")
        snap = market.build_snapshot(*_colors_for(scheme))
        # 이 스킴의 유일한 접속자라면 기준선을 지금 스냅샷으로 맞춘다. 아무도
        # 없던 사이에 벌어진 변화를 다음 패치에 실어 보낼 이유가 없다. 다른
        # 접속자가 이미 있으면 기준선은 그들이 받은 상태이므로 건드리지 않는다
        # — 덮어쓰면 그들이 아직 못 받은 변화가 기준선에 흡수돼 영영 사라진다.
        if sum(1 for s in self.scheme.values() if s == scheme) == 1:
            self.last_sent[scheme] = snap
        await ws.send_json({"type": "full", "data": snap} if ws not in self.legacy else snap)

    async def broadcast(self) -> None:
        if not self.active:
            return
        # 스킴당 한 번만 만든다. 예전엔 접속자 수만큼 build_snapshot 을 돌렸다.
        schemes = {self.scheme.get(ws, "kr") for ws in self.active}
        snaps = {s: market.build_snapshot(*_colors_for(s)) for s in schemes}
        patches = {s: self._diff(self.last_sent.get(s), snap) for s, snap in snaps.items()}

        dead = []
        for ws in list(self.active):
            scheme = self.scheme.get(ws, "kr")
            patch = patches.get(scheme)
            if ws in self.legacy:
                # 옛 클라이언트는 패치를 병합할 줄 모르니 통짜로 줄 수밖에 없다.
                # 다만 타임스탬프만 바뀐 틱까지 249KB 를 부을 이유는 없다 —
                # 그런 틱은 건너뛴다. 화면의 '기준 시각'만 잠깐 멎고 수치는
                # 정확하다. 모두 새 app.js 로 넘어오면 이 갈래는 지워도 된다.
                if not patch or not (patch.keys() - _TIMESTAMP_KEYS):
                    continue
                payload = snaps.get(scheme)
            else:
                # 바뀐 게 하나도 없으면 아예 보내지 않는다. 시세 폴링이
                # 실패한 틱에서는 asOf 조차 갱신되지 않아 여기에 걸린다.
                if not patch:
                    continue
                payload = {"type": "patch", "data": patch}
            try:
                await ws.send_json(payload)
            except Exception:
                dead.append(ws)
        for ws in dead:
            self.disconnect(ws)
        self.last_sent.update(snaps)


manager = ConnectionManager()


def _colors_for(scheme: str) -> tuple[str, str, str]:
    if scheme == "us":
        return "#1a8a4a", "#c0392b", "#7a7a7d"  # up, down, flat
    return "#c0392b", "#2f6fb0", "#7a7a7d"  # kr: up=red, down=blue


async def _price_loop() -> None:
    while True:
        try:
            await asyncio.to_thread(market.poll_prices)
            await manager.broadcast()
        except Exception:
            log.exception("price loop iteration failed")
        await asyncio.sleep(PRICE_POLL_SECONDS)


async def _movers_loop() -> None:
    while True:
        try:
            await asyncio.to_thread(market.poll_movers)
        except Exception:
            log.exception("movers loop iteration failed")
        await asyncio.sleep(MOVERS_POLL_SECONDS)


async def _kr_most_traded_loop() -> None:
    while True:
        try:
            await asyncio.to_thread(market.poll_kr_most_traded)
        except Exception:
            log.exception("kr most-traded loop iteration failed")
        await asyncio.sleep(KR_MOST_TRADED_POLL_SECONDS)


async def _us_movers_loop() -> None:
    while True:
        try:
            await asyncio.to_thread(market.poll_us_movers)
        except Exception:
            log.exception("us movers loop iteration failed")
        await asyncio.sleep(US_MOVERS_POLL_SECONDS)


async def _news_loop() -> None:
    while True:
        try:
            await asyncio.to_thread(market.poll_news)
            await asyncio.to_thread(market.poll_fx_news)
        except Exception:
            log.exception("news loop iteration failed")
        await asyncio.sleep(NEWS_POLL_SECONDS)


async def _kr_rates_loop() -> None:
    while True:
        try:
            await asyncio.to_thread(market.poll_kr_rates)
        except Exception:
            log.exception("kr rates loop iteration failed")
        await asyncio.sleep(KR_RATES_POLL_SECONDS)


async def _investor_flow_loop() -> None:
    while True:
        try:
            await asyncio.to_thread(market.poll_investor_flow)
        except Exception:
            log.exception("investor flow loop iteration failed")
        await asyncio.sleep(INVESTOR_FLOW_POLL_SECONDS)


async def _keyword_news_loop() -> None:
    # 기동 직후엔 메인뉴스·환율뉴스·종목검색 인덱스가 한꺼번에 네이버를
    # 두드린다. 거기에 키워드 검색 30여 건을 겹치면 403으로 막히므로
    # 첫 수집만 뒤로 물린다.
    await asyncio.sleep(KEYWORD_NEWS_START_DELAY_SECONDS)
    while True:
        try:
            await asyncio.to_thread(market.poll_keyword_news)
        except Exception:
            log.exception("keyword news loop iteration failed")
        await asyncio.sleep(KEYWORD_NEWS_POLL_SECONDS)


async def _basis_loop() -> None:
    while True:
        try:
            await asyncio.to_thread(market.poll_basis)
        except Exception:
            log.exception("basis loop iteration failed")
        await asyncio.sleep(BASIS_POLL_SECONDS)


async def _daily_sources_loop() -> None:
    """SEIBro(채권 커브·외화표시채) + KOFIA(수급·최종호가·단기금리) — 모두 일별 확정치."""
    while True:
        try:
            await asyncio.to_thread(market.poll_bond_curve)
            await asyncio.to_thread(market.poll_fx_bond_issues)
            await asyncio.to_thread(market.poll_bond_flow)
            await asyncio.to_thread(market.poll_bond_quotes)
            await asyncio.to_thread(market.poll_short_term_rates)
            await asyncio.to_thread(market.poll_krw_swap)
        except Exception:
            log.exception("daily sources loop iteration failed")
        await asyncio.sleep(DAILY_SOURCES_POLL_SECONDS)


@app.on_event("startup")
async def startup() -> None:
    # Seed everything before serving so the very first page load already
    # has real numbers instead of blanks. Sources are independent, so
    # they all run concurrently — poll_prices reads its own % baseline
    # out of the same window as the price, so nothing has to precede it.
    await asyncio.gather(
        asyncio.to_thread(market.poll_prices),
        asyncio.to_thread(market.poll_movers),
        asyncio.to_thread(market.poll_kr_most_traded),
        asyncio.to_thread(market.poll_us_movers),
        asyncio.to_thread(market.poll_news),
        asyncio.to_thread(market.poll_fx_news),
        asyncio.to_thread(market.poll_kr_rates),
        asyncio.to_thread(market.poll_investor_flow),
        asyncio.to_thread(market.poll_bond_curve),
        asyncio.to_thread(market.poll_fx_bond_issues),
        asyncio.to_thread(market.poll_bond_flow),
        asyncio.to_thread(market.poll_bond_quotes),
        asyncio.to_thread(market.poll_short_term_rates),
    )
    # 베이시스 이론가는 CD(91일)를 조달금리로 쓰므로 kr_rates 다음에 받는다.
    # 첫 스냅샷부터 대체값이 아닌 실제 고시금리가 들어가게 하려는 것.
    await asyncio.to_thread(market.poll_basis)
    # 스왑포인트 연율은 현물 USD/KRW 를 분모로 쓰므로 price poll 다음에 받는다.
    await asyncio.to_thread(market.poll_krw_swap)
    # Warm the stock-name index (substring search) in the background —
    # not worth delaying first paint for.
    naver_search.refresh_index()
    asyncio.create_task(_price_loop())
    asyncio.create_task(_movers_loop())
    asyncio.create_task(_kr_most_traded_loop())
    asyncio.create_task(_us_movers_loop())
    asyncio.create_task(_news_loop())
    asyncio.create_task(_kr_rates_loop())
    asyncio.create_task(_investor_flow_loop())
    asyncio.create_task(_basis_loop())
    asyncio.create_task(_daily_sources_loop())
    # 키워드 뉴스는 검색 스로틀 때문에 한 바퀴가 20초쯤 걸린다. 첫 화면을
    # 그만큼 늦출 이유가 없어 시드 없이 루프에만 맡긴다 (루프가 즉시 1회 돈다).
    asyncio.create_task(_keyword_news_loop())


@app.websocket("/ws")
async def ws_endpoint(websocket: WebSocket) -> None:
    scheme = websocket.query_params.get("scheme", "kr")
    # delta=1 은 새 app.js 만 붙인다. 없으면 옛 클라이언트로 보고 통짜로 보낸다.
    delta = websocket.query_params.get("delta") == "1"
    await manager.connect(websocket, scheme, delta)
    try:
        await manager.send_full(websocket)
        while True:
            # Clients don't need to send anything; keep the connection
            # open and detect disconnects via recv().
            await websocket.receive_text()
    except WebSocketDisconnect:
        pass
    finally:
        manager.disconnect(websocket)


# Popup payloads aggregate several slow upstream calls; a short TTL
# makes reopening the same popup (or re-typing a search) instant while
# staying well inside the board's own 10s price-poll cadence.
DETAIL_CACHE_TTL = 30
CHART_CACHE_TTL = 60
SEARCH_CACHE_TTL = 300


def _search_all_markets(q: str) -> list[dict] | None:
    # KR (네이버) and US (Yahoo) autocomplete are independent upstreams —
    # query both at once so the slower one doesn't serialize the other.
    with ThreadPoolExecutor(max_workers=2) as pool:
        kr = pool.submit(naver_search.search_stocks, q)
        us = pool.submit(us_search.search_stocks, q)
        return (kr.result() + us.result()) or None


@app.get("/api/search")
async def search(q: str = "") -> list[dict]:
    # Empty results aren't cached (get_or_fetch skips None) — the name
    # index may still be warming up right after startup, and a cached
    # miss would pin "no results" for the whole TTL.
    res = await asyncio.to_thread(
        ttl_cache.get_or_fetch, f"search:{q}", SEARCH_CACHE_TTL,
        lambda: _search_all_markets(q),
    )
    return res or []


@app.get("/api/stock/{symbol}")
async def stock_detail_endpoint(symbol: str, name: str = "", scheme: str = "kr") -> dict:
    up, down, flat = _colors_for(scheme)
    # KR tickers always carry a KRX suffix (005930.KS / 247540.KQ); a bare
    # ticker like "NVDA" is a US symbol — route each to its real source
    # (네이버 for KR, Yahoo Finance for US) rather than guessing at one API.
    is_kr = symbol.endswith(".KS") or symbol.endswith(".KQ")
    fetch = stock_detail.get_stock_detail if is_kr else us_stock_detail.get_stock_detail
    detail = await asyncio.to_thread(
        ttl_cache.get_or_fetch, f"stock:{symbol}:{scheme}", DETAIL_CACHE_TTL,
        lambda: fetch(symbol, name, up, down, flat),
    )
    if detail is None:
        raise HTTPException(404, f"no data for {symbol}")
    return detail


@app.get("/api/indicator/{kind}/{symbol:path}")
async def indicator_endpoint(
    kind: str, symbol: str, name: str = "", pair: str = "", contract: str = "", sub: str = "", scheme: str = "kr"
) -> dict:
    up, down, flat = _colors_for(scheme)
    if kind == "fx":
        fetch = lambda: indicator_detail.get_fx_detail(symbol, pair, name, up, down, flat)
    elif kind == "index":
        fetch = lambda: indicator_detail.get_index_detail(symbol, name, up, down, flat)
    elif kind == "commodity":
        fetch = lambda: indicator_detail.get_commodity_detail(symbol, name, contract, up, down, flat)
    elif kind == "rate":
        fetch = lambda: indicator_detail.get_rate_detail(symbol, name, sub, up, down, flat)
    elif kind == "krrate":
        fetch = lambda: indicator_detail.get_krrate_detail(symbol, name, up, down, flat)
    else:
        raise HTTPException(400, f"unknown indicator kind: {kind}")
    detail = await asyncio.to_thread(
        ttl_cache.get_or_fetch, f"indicator:{kind}:{symbol}:{scheme}", DETAIL_CACHE_TTL, fetch,
    )
    if detail is None:
        raise HTTPException(404, f"no data for {kind}/{symbol}")
    return detail


@app.get("/api/chart/{kind}/{symbol:path}")
async def chart_endpoint(kind: str, symbol: str, range: str = "1D") -> dict:
    if range not in chart_range.RANGES:
        raise HTTPException(400, f"unknown range: {range}")
    chart = await asyncio.to_thread(
        ttl_cache.get_or_fetch, f"chart:{kind}:{symbol}:{range}", CHART_CACHE_TTL,
        lambda: chart_range.get_chart(kind, symbol, range),
    )
    if chart is None:
        raise HTTPException(404, f"no chart for {kind}/{symbol} ({range})")
    return chart


@app.get("/api/seibro-custody")
async def seibro_custody_endpoint() -> dict:
    return await asyncio.to_thread(seibro_custody.build)


@app.get("/")
async def index() -> FileResponse:
    # no-cache 는 "캐시하지 마라"가 아니라 "쓰기 전에 반드시 물어봐라"다.
    # 이게 없으면 Cache-Control 이 아예 없는 응답이라 브라우저가 휴리스틱
    # 캐싱으로 재검증 없이 옛 HTML 을 꺼내 쓰고, 그러면 app.js 의 ?v= 를
    # 아무리 올려도 새 스크립트가 영영 내려가지 않는다.
    # 라우트에서 직접 돌려주는 FileResponse 는 StaticFiles 와 달리
    # If-None-Match 를 안 보므로 재검증이 304 가 아니라 매번 200(45KB)이다.
    # 페이지 로드당 한 번이라 WS 트래픽에 비하면 무시할 수준.
    return FileResponse(MAIN_HTML, headers={"Cache-Control": "no-cache"})


app.mount("/static", StaticFiles(directory=FRONTEND_DIR), name="static")
