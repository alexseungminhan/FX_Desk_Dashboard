"""Real-time market data source for the FX Desk Board.

Polls Yahoo Finance (via yfinance, no API key required) on a background
interval and builds the JSON snapshot the frontend renders. Every number
shown in the UI traces back to a real Yahoo Finance quote — nothing here
is synthesized. Instruments Yahoo has no free real-time source for
(Korean CD/KOFR, SOFR, US 2Y) were intentionally dropped in favor of the
UST yield-curve points Yahoo actually publishes (13wk/5y/10y/30y).
"""
from __future__ import annotations

import json
import logging
import time
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pandas as pd
import yfinance as yf

import kr_movers
import naver_news
import us_movers
import yahoo_news

log = logging.getLogger("market_data")
logging.getLogger("yfinance").setLevel(logging.CRITICAL)  # noisy on partial batch misses; we handle failures ourselves

KST = timezone(timedelta(hours=9))

# ---------------------------------------------------------------------------
# Symbol universe
# ---------------------------------------------------------------------------

# All real Yahoo Finance FX spot pairs shown in the "환율" panel,
# grouped by region the way the board displays them.
FX_REGIONS = [
    {"label": "아시아·태평양 APAC", "rows": [
        {"symbol": "USDKRW=X", "pair": "USD/KRW", "name": "달러/원"},
        {"symbol": "USDJPY=X", "pair": "USD/JPY", "name": "달러/엔"},
        {"symbol": "USDCNH=X", "pair": "USD/CNH", "name": "달러/위안"},
        {"symbol": "AUDUSD=X", "pair": "AUD/USD", "name": "호주달러/달러"},
    ]},
    {"label": "유럽 Europe", "rows": [
        {"symbol": "EURUSD=X", "pair": "EUR/USD", "name": "유로/달러"},
        {"symbol": "GBPUSD=X", "pair": "GBP/USD", "name": "파운드/달러"},
        {"symbol": "USDCHF=X", "pair": "USD/CHF", "name": "달러/스위스프랑"},
        {"symbol": "EURGBP=X", "pair": "EUR/GBP", "name": "유로/파운드"},
    ]},
    {"label": "북미 North America", "rows": [
        {"symbol": "USDCAD=X", "pair": "USD/CAD", "name": "달러/캐나다달러"},
        {"symbol": "USDMXN=X", "pair": "USD/MXN", "name": "달러/페소"},
        {"symbol": "DX-Y.NYB", "pair": "DXY 달러지수", "name": "ICE 달러 인덱스"},
    ]},
]

# Global indices grid ("글로벌 지수"), two display columns.
IDX_MAIN = [
    {"symbol": "^KS11", "name": "KOSPI"},
    {"symbol": "^KQ11", "name": "KOSDAQ"},
    {"symbol": "^GSPC", "name": "S&P 500"},
    {"symbol": "^IXIC", "name": "NASDAQ"},
    {"symbol": "^DJI", "name": "다우존스"},
    {"symbol": "^N225", "name": "닛케이 225"},
    {"symbol": "^HSI", "name": "항셍"},
    {"symbol": "^STOXX50E", "name": "유로스톡스 50"},
]

TICKER_STRIP = [
    {"symbol": "^KS11", "label": "KOSPI"},
    {"symbol": "^KQ11", "label": "KOSDAQ"},
    {"symbol": "^IXIC", "label": "NASDAQ"},
    {"symbol": "^GSPC", "label": "S&P 500"},
    {"symbol": "USDKRW=X", "label": "USD/KRW"},
    {"symbol": "USDJPY=X", "label": "USD/JPY"},
    {"symbol": "CL=F", "label": "WTI"},
    {"symbol": "GC=F", "label": "GOLD"},
    {"symbol": "^TNX", "label": "US 10Y"},
]

INDEX_GROUPS = [
    {"label": "한국 Korea", "rows": [
        {"symbol": "^KS11", "name": "코스피"},
        {"symbol": "^KQ11", "name": "코스닥"},
        {"symbol": "^KS200", "name": "코스피200"},
    ]},
    {"label": "미국 US", "rows": [
        {"symbol": "^IXIC", "name": "나스닥"},
        {"symbol": "^GSPC", "name": "S&P 500"},
        {"symbol": "^DJI", "name": "다우산업"},
        {"symbol": "^VIX", "name": "VIX"},
    ]},
    {"label": "아시아 Asia", "rows": [
        {"symbol": "^N225", "name": "니케이225"},
        {"symbol": "^HSI", "name": "항셍 (HK)"},
        {"symbol": "000001.SS", "name": "상해종합"},
    ]},
    {"label": "유럽 Europe", "rows": [
        {"symbol": "^FTSE", "name": "FTSE 100"},
        {"symbol": "^GDAXI", "name": "DAX"},
        {"symbol": "^FCHI", "name": "CAC 40"},
    ]},
]

# Real UST yield-curve points (Yahoo has no free SOFR/KOFR/CD91/US2Y feed).
RATES = [
    {"symbol": "^IRX", "name": "미 국채 13주", "sub": "13-Week T-Bill", "is_yield": True},
    {"symbol": "^FVX", "name": "미 국채 5년", "sub": "5-Year Treasury", "is_yield": True},
    {"symbol": "^TNX", "name": "미 국채 10년", "sub": "10-Year Treasury", "is_yield": True},
    {"symbol": "^TYX", "name": "미 국채 30년", "sub": "30-Year Treasury", "is_yield": True},
]

COMMODITIES = [
    {"symbol": "GC=F", "name": "금", "contract": "GC"},
    {"symbol": "SI=F", "name": "은", "contract": "SI"},
    {"symbol": "PL=F", "name": "백금", "contract": "PL"},
    {"symbol": "CL=F", "name": "WTI 원유", "contract": "CL"},
    {"symbol": "BZ=F", "name": "브렌트유", "contract": "BZ"},
    {"symbol": "NG=F", "name": "천연가스", "contract": "NG"},
    {"symbol": "ZC=F", "name": "옥수수", "contract": "ZC"},
    {"symbol": "ZS=F", "name": "대두", "contract": "ZS"},
]

# Seed watchlist shown as "관심종목 시세" (real Yahoo Finance quotes) the
# first time the app runs — after that the user's own additions/removals
# (persisted to watchlist.json, see load/save below) take over. "국내
# 등락 상위" itself is sourced separately from Naver Finance's real
# market-wide ranking pages (see kr_movers.py) — Yahoo has no free
# full-market screener for KRX, so a watchlist can't stand in for actual
# top movers.
DEFAULT_WATCHLIST = [
    {"symbol": "005930.KS", "name": "삼성전자"},
    {"symbol": "000660.KS", "name": "SK하이닉스"},
    {"symbol": "035420.KS", "name": "NAVER"},
    {"symbol": "035720.KS", "name": "카카오"},
    {"symbol": "051910.KS", "name": "LG화학"},
    {"symbol": "006400.KS", "name": "삼성SDI"},
    {"symbol": "373220.KS", "name": "LG에너지솔루션"},
    {"symbol": "207940.KS", "name": "삼성바이오로직스"},
    {"symbol": "005380.KS", "name": "현대차"},
    {"symbol": "000270.KS", "name": "기아"},
    {"symbol": "105560.KS", "name": "KB금융"},
    {"symbol": "055550.KS", "name": "신한지주"},
    {"symbol": "012330.KS", "name": "현대모비스"},
    {"symbol": "068270.KS", "name": "셀트리온"},
    {"symbol": "247540.KQ", "name": "에코프로비엠"},
]

# Symbols always tracked regardless of the user's watchlist.
FIXED_SYMBOLS = sorted({
    *(row["symbol"] for g in FX_REGIONS for row in g["rows"]),
    *(r["symbol"] for r in TICKER_STRIP),
    *(row["symbol"] for g in INDEX_GROUPS for row in g["rows"]),
    *(r["symbol"] for r in IDX_MAIN),
    *(r["symbol"] for r in RATES),
    *(r["symbol"] for r in COMMODITIES),
})

WATCHLIST_FILE = Path(__file__).resolve().parent / "watchlist.json"

SPARK_LEN = 30


def _fmt_num(v: float, decimals: int) -> str:
    return f"{v:,.{decimals}f}"


def _fmt_usd(v: float, decimals: int = 2) -> str:
    return f"${v:,.{decimals}f}"


def _fmt_volume(v: float | None) -> str:
    if v is None:
        return "—"
    v = float(v)
    if v >= 1_000_000_000:
        return f"{v / 1_000_000_000:.1f}B"
    if v >= 1_000_000:
        return f"{v / 1_000_000:.1f}M"
    if v >= 1_000:
        return f"{v / 1_000:.1f}K"
    return f"{v:,.0f}"


def _fmt_krw_value(million_won: float) -> str:
    """Format a Naver 거래대금 figure (already in 백만원) as 조/억원,
    matching how 네이버 증권 itself displays trading value."""
    eok = million_won / 100  # 백만원 -> 억원
    jo = eok / 10_000  # 억원 -> 조원
    if jo >= 1:
        return f"{jo:,.2f}조"
    return f"{eok:,.0f}억"


def _decimals_for(symbol: str, price: float) -> int:
    if symbol.endswith("=X"):
        return 4 if price < 50 else 2
    if symbol in ("^TNX", "^TYX", "^FVX", "^IRX"):
        return 2
    if symbol.endswith(".KS") or symbol.endswith(".KQ"):
        return 0  # individual KRW-denominated equities trade in whole won
    return 2


@dataclass
class SymbolState:
    symbol: str
    price: float | None = None
    prev_close: float | None = None
    history: deque = field(default_factory=lambda: deque(maxlen=SPARK_LEN))
    stale: bool = True
    last_ok: float = 0.0

    @property
    def pct(self) -> float | None:
        if self.price is None or not self.prev_close:
            return None
        return (self.price - self.prev_close) / self.prev_close * 100.0

    def color(self, up: str, down: str, flat: str) -> str:
        p = self.pct
        if p is None:
            return flat
        return up if p > 0 else down if p < 0 else flat

    def arrow(self) -> str:
        p = self.pct
        if p is None:
            return "–"
        return "▲" if p > 0 else "▼" if p < 0 else "–"

    def spark_path(self, w: int = 132, h: int = 34) -> str | None:
        pts = list(self.history)
        if len(pts) < 2:
            return None
        lo, hi = min(pts), max(pts)
        rng = (hi - lo) or 1.0
        coords = [
            (i / (len(pts) - 1) * w, h - ((v - lo) / rng) * (h - 2) - 1)
            for i, v in enumerate(pts)
        ]
        line = " ".join(
            f"{'M' if i == 0 else 'L'}{x:.1f} {y:.1f}" for i, (x, y) in enumerate(coords)
        )
        area = f"{line} L {w} {h} L 0 {h} Z"
        return line, area


class MarketData:
    """Holds the latest known-good state for every tracked symbol."""

    def __init__(self) -> None:
        self.watchlist: list[dict] = self._load_watchlist()
        self.states: dict[str, SymbolState] = {
            s: SymbolState(symbol=s) for s in self.all_symbols()
        }
        self.news: list[dict] = []
        self.last_snapshot_at: datetime | None = None
        self.mover_gainers: list[dict] = []
        self.mover_losers: list[dict] = []
        self.movers_stale: bool = True
        self.movers_updated_at: datetime | None = None
        self.kr_most_traded: list[dict] = []
        self.kr_most_traded_stale: bool = True
        self.us_gainers: list[dict] = []
        self.us_losers: list[dict] = []
        self.us_most_active: list[dict] = []
        self.us_movers_stale: bool = True

    # -- watchlist persistence -------------------------------------------

    def _load_watchlist(self) -> list[dict]:
        if WATCHLIST_FILE.exists():
            try:
                return json.loads(WATCHLIST_FILE.read_text())
            except Exception:
                log.exception("failed to read %s — falling back to default", WATCHLIST_FILE)
        return [dict(w) for w in DEFAULT_WATCHLIST]

    def _save_watchlist(self) -> None:
        try:
            WATCHLIST_FILE.write_text(json.dumps(self.watchlist, ensure_ascii=False, indent=2))
        except Exception:
            log.exception("failed to write %s", WATCHLIST_FILE)

    def all_symbols(self) -> list[str]:
        return sorted({*FIXED_SYMBOLS, *(w["symbol"] for w in self.watchlist)})

    def add_watchlist_item(self, symbol: str, name: str, market: str) -> None:
        if any(w["symbol"] == symbol for w in self.watchlist):
            return
        self.watchlist.append({"symbol": symbol, "name": name, "market": market})
        self._save_watchlist()
        if symbol not in self.states:
            self.states[symbol] = SymbolState(symbol=symbol)

    def remove_watchlist_item(self, symbol: str) -> None:
        self.watchlist = [w for w in self.watchlist if w["symbol"] != symbol]
        self._save_watchlist()
        self.states.pop(symbol, None)

    def refresh_symbol(self, symbol: str) -> None:
        """Fetch prev-close + current price for a single symbol — used
        right after the user adds it, so they don't wait for the next
        scheduled poll tick to see a real quote."""
        st = self.states.get(symbol)
        if st is None:
            return
        try:
            hist = yf.Ticker(symbol).history(period="5d", interval="5m")
            closes = hist["Close"].dropna()
            if len(closes) == 0:
                return
            daily = yf.Ticker(symbol).history(period="5d", interval="1d")["Close"].dropna()
            if len(daily) >= 2:
                st.prev_close = float(daily.iloc[-2])
            elif len(daily) == 1:
                st.prev_close = float(daily.iloc[-1])
            st.price = float(closes.iloc[-1])
            st.history.append(st.price)
            st.stale = False
            st.last_ok = time.time()
        except Exception:
            log.exception("refresh_symbol failed for %s", symbol)

    # -- data acquisition ---------------------------------------------

    def refresh_prev_close(self) -> None:
        """Fetch yesterday's close for every symbol (cheap, once/day)."""
        symbols = self.all_symbols()
        try:
            data = yf.download(
                tickers=" ".join(symbols),
                period="5d",
                interval="1d",
                group_by="ticker",
                progress=False,
                threads=True,
            )
        except Exception:
            log.exception("refresh_prev_close failed")
            return
        for sym in symbols:
            try:
                closes = data[sym]["Close"].dropna()
                if len(closes) >= 2:
                    self.states[sym].prev_close = float(closes.iloc[-2])
                elif len(closes) == 1:
                    self.states[sym].prev_close = float(closes.iloc[-1])
            except Exception:
                continue

    def poll_prices(self) -> None:
        """Batch-fetch current intraday prices for every tracked symbol.

        period=5d/interval=5m (rather than 1d/1m) so weekends and
        pre-/post-market gaps still resolve to the last real print
        instead of an empty window — markets that are genuinely closed
        show their last real close, not a blank.
        """
        symbols = self.all_symbols()
        try:
            data = yf.download(
                tickers=" ".join(symbols),
                period="5d",
                interval="5m",
                group_by="ticker",
                progress=False,
                threads=True,
            )
        except Exception:
            log.exception("poll_prices failed — keeping last known values")
            return

        now = time.time()
        single = len(symbols) == 1
        for sym in symbols:
            st = self.states[sym]
            try:
                closes = data["Close"] if single else data[sym]["Close"]
                closes = closes.dropna()
                if len(closes) == 0:
                    continue
                price = float(closes.iloc[-1])
                if st.prev_close is None:
                    st.prev_close = float(closes.iloc[0])
                st.price = price
                st.history.append(price)
                st.stale = False
                st.last_ok = now
            except Exception:
                # Leave st.price/prev_close untouched — frontend keeps
                # showing the last real value with a stale indicator.
                continue

        # Anything not refreshed in the last 90s is flagged stale.
        for st in self.states.values():
            if now - st.last_ok > 90:
                st.stale = True

        self.last_snapshot_at = datetime.now(tz=KST)

    def poll_news(self) -> None:
        """Korean headlines from 네이버 증권 + English headlines from
        Yahoo Finance, covering the last 24 hours, merged and sorted
        newest-first. The frontend only renders a page at a time
        ("더보기") but keyword search runs against this whole window,
        not just whatever happens to be on screen."""
        naver_items: list[dict] = []
        yahoo_items: list[dict] = []
        try:
            naver_items = naver_news.fetch_recent_news(hours=24)
        except Exception:
            log.exception("naver news poll failed")
        try:
            yahoo_items = yahoo_news.fetch_recent_news(hours=24)
        except Exception:
            log.exception("yahoo news poll failed")

        merged = [
            {"title": n["title"], "url": n["url"], "time": n["time"], "source": n["press"], "summary": n["summary"]}
            for n in naver_items
        ] + [
            {"title": n["title"], "url": n["url"], "time": n["time"], "source": n["source"], "summary": n["summary"]}
            for n in yahoo_items
        ]
        if not merged:
            log.warning("poll_news returned no rows — keeping last known headlines")
            return

        def sort_key(item: dict):
            t = item["time"]
            return t.astimezone(timezone.utc) if t else datetime.min.replace(tzinfo=timezone.utc)

        merged.sort(key=sort_key, reverse=True)
        # Hard cap as a safety net against an unusually heavy news day —
        # not a normal limit, 24h of both feeds rarely gets near this.
        self.news = merged[:300]

    def poll_movers(self) -> None:
        """Real KOSPI+KOSDAQ top gainers/losers from Naver Finance — see
        kr_movers.py for why Yahoo can't provide this."""
        try:
            gainers, losers = kr_movers.fetch_movers()
        except Exception:
            log.exception("poll_movers failed — keeping last known ranking")
            return
        if not gainers and not losers:
            # Both empty almost certainly means the scrape broke (site
            # layout change, block, etc) rather than a real quiet market.
            log.warning("poll_movers returned no rows — keeping last known ranking")
            return
        self.mover_gainers = gainers
        self.mover_losers = losers
        self.movers_stale = False
        self.movers_updated_at = datetime.now(tz=KST)

    def poll_kr_most_traded(self) -> None:
        """Real KOSPI+KOSDAQ top-by-trading-value from Naver Finance."""
        try:
            rows = kr_movers.fetch_most_traded()
        except Exception:
            log.exception("poll_kr_most_traded failed — keeping last known ranking")
            return
        if not rows:
            log.warning("poll_kr_most_traded returned no rows — keeping last known ranking")
            return
        self.kr_most_traded = rows
        self.kr_most_traded_stale = False

    def poll_us_movers(self) -> None:
        """Real US day gainers/losers/most-active from Yahoo Finance's
        own screeners — see us_movers.py."""
        try:
            gainers, losers = us_movers.fetch_us_gainers_losers()
            actives = us_movers.fetch_us_most_active()
        except Exception:
            log.exception("poll_us_movers failed — keeping last known ranking")
            return
        if not gainers and not losers and not actives:
            log.warning("poll_us_movers returned no rows — keeping last known ranking")
            return
        self.us_gainers = gainers
        self.us_losers = losers
        self.us_most_active = actives
        self.us_movers_stale = False

    # -- snapshot assembly ----------------------------------------------

    def _row(self, symbol: str, name: str, up: str, down: str, flat: str, extra: dict | None = None) -> dict:
        st = self.states[symbol]
        price = st.price
        decimals = _decimals_for(symbol, price or 0)
        is_pct_symbol = symbol in ("^IRX", "^FVX", "^TNX", "^TYX")
        row = {
            "symbol": symbol,
            "name": name,
            "price": _fmt_num(price, decimals) + ("%" if is_pct_symbol else "") if price is not None else "—",
            "color": st.color(up, down, flat),
            "arrow": st.arrow(),
            "pct": f"{st.pct:+.2f}%" if st.pct is not None else "—",
            "stale": st.stale,
        }
        if extra:
            row.update(extra)
        return row

    def build_snapshot(self, up: str, down: str, flat: str) -> dict:
        def fx_row(f: dict) -> dict:
            st = self.states[f["symbol"]]
            row = self._row(f["symbol"], f["pair"], up, down, flat)
            row["pair"] = f["pair"]
            row["name"] = f["name"]
            chg = None
            if st.price is not None and st.prev_close is not None:
                chg = st.price - st.prev_close
            row["chg"] = _fmt_num(chg, _decimals_for(f["symbol"], st.price or 0)) if chg is not None else "—"
            return row

        fx_regions = [
            {"label": g["label"], "rows": [fx_row(f) for f in g["rows"]]}
            for g in FX_REGIONS
        ]

        ticker = [self._row(t["symbol"], t["label"], up, down, flat) | {"label": t["label"]} for t in TICKER_STRIP]

        index_groups = [
            {
                "label": g["label"],
                "rows": [self._row(r["symbol"], r["name"], up, down, flat) for r in g["rows"]],
            }
            for g in INDEX_GROUPS
        ]

        idx_main = [self._row(r["symbol"], r["name"], up, down, flat) for r in IDX_MAIN]

        rates = []
        for r in RATES:
            st = self.states[r["symbol"]]
            row = self._row(r["symbol"], r["name"], up, down, flat)
            row["sub"] = r["sub"]
            chg_bp = None
            if st.price is not None and st.prev_close is not None:
                chg_bp = (st.price - st.prev_close) * 100  # yield pts -> bp
            row["chg"] = f"{chg_bp:+.0f}bp" if chg_bp is not None else "—"
            row["value"] = row["price"]
            rates.append(row)

        commodities = []
        for c in COMMODITIES:
            row = self._row(c["symbol"], c["name"], up, down, flat)
            row["contract"] = c["contract"]
            commodities.append(row)

        def mover_row(m: dict, i: int) -> dict:
            pct = m["pct"]
            color = up if pct > 0 else down if pct < 0 else flat
            arrow = "▲" if pct > 0 else "▼" if pct < 0 else "–"
            name = m["name"] + ("*" if m.get("market") == "KOSDAQ" else "")
            return {
                "rank": i + 1,
                "symbol": m.get("symbol"),
                "name": name,
                "market": m.get("market"),
                "price": _fmt_num(m["price"], 0),
                "pct": f"{pct:+.2f}%",
                "color": color,
                "arrow": arrow,
                "stale": self.movers_stale,
            }

        gainers = [mover_row(m, i) for i, m in enumerate(self.mover_gainers)]
        losers = [mover_row(m, i) for i, m in enumerate(self.mover_losers)]

        def kr_most_traded_row(m: dict, i: int) -> dict:
            return {
                "rank": i + 1,
                "symbol": m.get("symbol"),
                "name": m["name"] + ("*" if m.get("market") == "KOSDAQ" else ""),
                "market": m.get("market"),
                "price": _fmt_num(m["price"], 0),
                "tradingValue": _fmt_krw_value(m["tradingValueMm"]),
                "stale": self.kr_most_traded_stale,
            }

        kr_most_traded = [kr_most_traded_row(m, i) for i, m in enumerate(self.kr_most_traded)]

        def us_mover_row(m: dict, i: int) -> dict:
            pct = m["pct"]
            color = up if pct > 0 else down if pct < 0 else flat
            arrow = "▲" if pct > 0 else "▼" if pct < 0 else "–"
            return {
                "rank": i + 1,
                "symbol": m["symbol"],
                "name": m["symbol"],
                "fullName": m.get("name") or m["symbol"],
                "price": _fmt_usd(m["price"]),
                "pct": f"{pct:+.2f}%",
                "color": color,
                "arrow": arrow,
                "stale": self.us_movers_stale,
            }

        def us_active_row(m: dict, i: int) -> dict:
            row = us_mover_row(m, i)
            row["volume"] = _fmt_volume(m.get("volume"))
            return row

        us_gainers = [us_mover_row(m, i) for i, m in enumerate(self.us_gainers)]
        us_losers = [us_mover_row(m, i) for i, m in enumerate(self.us_losers)]
        us_most_active = [us_active_row(m, i) for i, m in enumerate(self.us_most_active)]

        watchlist = []
        for w in self.watchlist:
            st = self.states.get(w["symbol"])
            if st is None or st.price is None:
                continue
            watchlist.append(self._row(w["symbol"], w["name"], up, down, flat))

        today_kst = datetime.now(tz=KST).date()

        def news_time(t: datetime | None) -> str:
            if t is None:
                return "--:--"
            t_kst = t.astimezone(KST)
            # 24시간 전체를 보여주므로 오늘이 아닌 기사는 날짜도 함께
            # 표시해 "14:05"가 오늘인지 어제인지 헷갈리지 않게 한다.
            fmt = "%H:%M" if t_kst.date() == today_kst else "%m/%d %H:%M"
            return t_kst.strftime(fmt)

        news = [
            {
                "time": news_time(n["time"]),
                "headline": n["title"],
                "tag": n["source"],
                "url": n["url"],
                "summary": n["summary"],
            }
            for n in self.news
        ]

        return {
            "asOf": self.last_snapshot_at.isoformat() if self.last_snapshot_at else None,
            "moversAsOf": self.movers_updated_at.isoformat() if self.movers_updated_at else None,
            "ticker": ticker,
            "fxRegions": fx_regions,
            "indexGroups": index_groups,
            "idxMain": idx_main,
            "rates": rates,
            "commAll": commodities,
            "krMostTraded": kr_most_traded,
            "usGainers": us_gainers,
            "usLosers": us_losers,
            "usMostActive": us_most_active,
            "gainers": gainers,
            "losers": losers,
            "watchlist": watchlist,
            "news": news,
        }
