"""FastAPI real-time FX Desk Board.

Serves the static frontend and pushes live Yahoo Finance snapshots to
every connected WebSocket client on a fixed polling interval.
"""
from __future__ import annotations

import asyncio
import logging
from pathlib import Path

from fastapi import Body, FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse

import indicator_detail
import naver_search
import stock_detail
import us_stock_detail
from market_data import MarketData

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("fx-desk-board")

PRICE_POLL_SECONDS = 10
NEWS_POLL_SECONDS = 300
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
        except Exception:
            log.exception("news loop iteration failed")
        await asyncio.sleep(NEWS_POLL_SECONDS)


async def _prev_close_loop() -> None:
    while True:
        try:
            await asyncio.to_thread(market.refresh_prev_close)
        except Exception:
            log.exception("prev-close refresh failed")
        await asyncio.sleep(PREV_CLOSE_REFRESH_SECONDS)


@app.on_event("startup")
async def startup() -> None:
    # Seed prev-close + one price poll synchronously so the very first
    # page load already has real numbers instead of blanks.
    await asyncio.to_thread(market.refresh_prev_close)
    await asyncio.to_thread(market.poll_prices)
    await asyncio.to_thread(market.poll_movers)
    await asyncio.to_thread(market.poll_kr_most_traded)
    await asyncio.to_thread(market.poll_us_movers)
    await asyncio.to_thread(market.poll_news)
    asyncio.create_task(_price_loop())
    asyncio.create_task(_movers_loop())
    asyncio.create_task(_kr_most_traded_loop())
    asyncio.create_task(_us_movers_loop())
    asyncio.create_task(_news_loop())
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


@app.get("/api/search")
async def search(q: str = "") -> list[dict]:
    return await asyncio.to_thread(naver_search.search_stocks, q)


@app.get("/api/stock/{symbol}")
async def stock_detail_endpoint(symbol: str, name: str = "", scheme: str = "kr") -> dict:
    up, down, flat = _colors_for(scheme)
    # KR tickers always carry a KRX suffix (005930.KS / 247540.KQ); a bare
    # ticker like "NVDA" is a US symbol — route each to its real source
    # (네이버 for KR, Yahoo Finance for US) rather than guessing at one API.
    is_kr = symbol.endswith(".KS") or symbol.endswith(".KQ")
    fetch = stock_detail.get_stock_detail if is_kr else us_stock_detail.get_stock_detail
    detail = await asyncio.to_thread(fetch, symbol, name, up, down, flat)
    if detail is None:
        raise HTTPException(404, f"no data for {symbol}")
    detail["inWatchlist"] = any(w["symbol"] == symbol for w in market.watchlist)
    return detail


@app.post("/api/watchlist")
async def add_to_watchlist(payload: dict = Body(...)) -> dict:
    symbol, name, market_name = payload.get("symbol"), payload.get("name"), payload.get("market")
    if not symbol or not name:
        raise HTTPException(400, "symbol and name are required")
    market.add_watchlist_item(symbol, name, market_name or "")
    # Fetch the new symbol's price right away rather than making the
    # user wait for the next scheduled poll tick.
    await asyncio.to_thread(market.refresh_symbol, symbol)
    await manager.broadcast()
    return {"ok": True}


@app.delete("/api/watchlist/{symbol}")
async def remove_from_watchlist(symbol: str) -> dict:
    market.remove_watchlist_item(symbol)
    await manager.broadcast()
    return {"ok": True}


@app.get("/api/indicator/{kind}/{symbol:path}")
async def indicator_endpoint(
    kind: str, symbol: str, name: str = "", pair: str = "", contract: str = "", sub: str = "", scheme: str = "kr"
) -> dict:
    up, down, flat = _colors_for(scheme)
    if kind == "fx":
        detail = await asyncio.to_thread(indicator_detail.get_fx_detail, symbol, pair, name, up, down, flat)
    elif kind == "index":
        detail = await asyncio.to_thread(indicator_detail.get_index_detail, symbol, name, up, down, flat)
    elif kind == "commodity":
        detail = await asyncio.to_thread(indicator_detail.get_commodity_detail, symbol, name, contract, up, down, flat)
    elif kind == "rate":
        detail = await asyncio.to_thread(indicator_detail.get_rate_detail, symbol, name, sub, up, down, flat)
    else:
        raise HTTPException(400, f"unknown indicator kind: {kind}")
    if detail is None:
        raise HTTPException(404, f"no data for {kind}/{symbol}")
    return detail


@app.get("/")
async def index() -> FileResponse:
    return FileResponse(MAIN_HTML)


app.mount("/static", StaticFiles(directory=FRONTEND_DIR), name="static")
