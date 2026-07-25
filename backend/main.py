"""FastAPI real-time FX Desk Board.

Serves the static frontend and pushes live Yahoo Finance snapshots to
every connected WebSocket client on a fixed polling interval.
"""
from __future__ import annotations

import asyncio
import logging
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
PREV_CLOSE_REFRESH_SECONDS = 6 * 60 * 60

PROJECT_ROOT = Path(__file__).resolve().parent.parent
FRONTEND_DIR = PROJECT_ROOT / "frontend"
MAIN_HTML = PROJECT_ROOT / "FX Desk Board.html"

app = FastAPI(title="FX Desk Board")
market = MarketData()


class ConnectionManager:
    def __init__(self) -> None:
        self.active: set[WebSocket] = set()
        self.scheme: dict[WebSocket, str] = {}

    async def connect(self, ws: WebSocket, scheme: str) -> None:
        await ws.accept()
        self.active.add(ws)
        self.scheme[ws] = scheme

    def disconnect(self, ws: WebSocket) -> None:
        self.active.discard(ws)
        self.scheme.pop(ws, None)

    async def send_snapshot(self, ws: WebSocket) -> None:
        colors = _colors_for(self.scheme.get(ws, "kr"))
        await ws.send_json(market.build_snapshot(*colors))

    async def broadcast(self) -> None:
        dead = []
        for ws in list(self.active):
            try:
                await self.send_snapshot(ws)
            except Exception:
                dead.append(ws)
        for ws in dead:
            self.disconnect(ws)


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


async def _prev_close_loop() -> None:
    while True:
        try:
            await asyncio.to_thread(market.refresh_prev_close)
        except Exception:
            log.exception("prev-close refresh failed")
        await asyncio.sleep(PREV_CLOSE_REFRESH_SECONDS)


@app.on_event("startup")
async def startup() -> None:
    # Seed everything before serving so the very first page load already
    # has real numbers instead of blanks. Sources are independent, so
    # they run concurrently — only poll_prices needs prev-close first
    # (for the % baseline), so it runs after that gather.
    await asyncio.gather(
        asyncio.to_thread(market.refresh_prev_close),
        asyncio.to_thread(market.poll_movers),
        asyncio.to_thread(market.poll_kr_most_traded),
        asyncio.to_thread(market.poll_us_movers),
        asyncio.to_thread(market.poll_news),
        asyncio.to_thread(market.poll_fx_news),
        asyncio.to_thread(market.poll_kr_rates),
    )
    await asyncio.to_thread(market.poll_prices)
    # Warm the stock-name index (substring search) in the background —
    # not worth delaying first paint for.
    naver_search.refresh_index()
    asyncio.create_task(_price_loop())
    asyncio.create_task(_movers_loop())
    asyncio.create_task(_kr_most_traded_loop())
    asyncio.create_task(_us_movers_loop())
    asyncio.create_task(_news_loop())
    asyncio.create_task(_kr_rates_loop())
    asyncio.create_task(_prev_close_loop())


@app.websocket("/ws")
async def ws_endpoint(websocket: WebSocket) -> None:
    scheme = websocket.query_params.get("scheme", "kr")
    await manager.connect(websocket, scheme)
    try:
        await manager.send_snapshot(websocket)
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


@app.get("/api/search")
async def search(q: str = "") -> list[dict]:
    # Empty results aren't cached (get_or_fetch skips None) — the name
    # index may still be warming up right after startup, and a cached
    # miss would pin "no results" for the whole TTL.
    res = await asyncio.to_thread(
        ttl_cache.get_or_fetch, f"search:{q}", SEARCH_CACHE_TTL,
        lambda: naver_search.search_stocks(q) or None,
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
    return FileResponse(MAIN_HTML)


app.mount("/static", StaticFiles(directory=FRONTEND_DIR), name="static")
