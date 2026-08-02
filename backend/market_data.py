"""Real-time market data source for the FX Desk Board.

Polls Yahoo Finance (via yfinance, no API key required) on a background
interval and builds the JSON snapshot the frontend renders. Every number
shown in the UI traces back to a real Yahoo Finance quote — nothing here
is synthesized. Instruments Yahoo has no free real-time source for
(Korean CD/KOFR, SOFR, US 2Y) were intentionally dropped in favor of the
UST yield-curve points Yahoo actually publishes (13wk/5y/10y/30y).
"""
from __future__ import annotations

import gc
import logging
import time
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

import multitasking
import yfinance as yf
from yfinance.data import YfData

import fx_news
import kr_movers
import kr_rates
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

# kind/name/pair/contract/sub carry through to the frontend so ticker
# tiles open the same indicator popup as their matching panel rows.
TICKER_STRIP = [
    {"symbol": "^KS11", "label": "KOSPI", "kind": "index", "name": "코스피"},
    {"symbol": "^KQ11", "label": "KOSDAQ", "kind": "index", "name": "코스닥"},
    {"symbol": "^IXIC", "label": "NASDAQ", "kind": "index", "name": "나스닥"},
    {"symbol": "^GSPC", "label": "S&P 500", "kind": "index", "name": "S&P 500"},
    {"symbol": "USDKRW=X", "label": "USD/KRW", "kind": "fx", "name": "달러/원", "pair": "USD/KRW"},
    {"symbol": "USDJPY=X", "label": "USD/JPY", "kind": "fx", "name": "달러/엔", "pair": "USD/JPY"},
    {"symbol": "CL=F", "label": "WTI", "kind": "commodity", "name": "WTI 원유", "contract": "CL"},
    {"symbol": "GC=F", "label": "GOLD", "kind": "commodity", "name": "금", "contract": "GC"},
    {"symbol": "^TNX", "label": "US 10Y", "kind": "rate", "name": "미 국채 10년", "sub": "10-Year Treasury"},
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

# Every symbol the board tracks on the price poll.
FIXED_SYMBOLS = sorted({
    *(row["symbol"] for g in FX_REGIONS for row in g["rows"]),
    *(r["symbol"] for r in TICKER_STRIP),
    *(row["symbol"] for g in INDEX_GROUPS for row in g["rows"]),
    *(r["symbol"] for r in IDX_MAIN),
    *(r["symbol"] for r in RATES),
    *(r["symbol"] for r in COMMODITIES),
})

SPARK_LEN = 30

# Yahoo's batched quote endpoint: price and previous close for every
# tracked symbol in a single request. This is the same data the popups
# read per-symbol via Ticker.get_info(), so the board and the popup you
# open from it cannot disagree — they now resolve to one source.
#
# It replaced daily OHLC bars as the price source because Yahoo's daily
# Close is simply wrong outside indices and rates. For FX and futures it
# reports something near the session's OPEN: USDKRW=X's 07-24 bar closed
# at 1474.04 while that day's last actual tick was 1459.42, which put
# every FX % on the board a session out of step and flipped signs
# outright (ZC=F read +1.83% against a real -3.08%). On indices and
# rates, where the bars are trustworthy, the two agree to the cent.
QUOTE_URL = "https://query2.finance.yahoo.com/v7/finance/quote"
# Long symbol lists get truncated rather than rejected, so they're sent
# in chunks; the board's ~40 symbols normally fit in one request.
QUOTE_CHUNK = 40

# Fallback-path guard only (see _fetch_from_bars). Yahoo's daily history
# also drops whole runs of sessions — a recent ^KS200 window held 07-16
# and 07-27 and nothing between — and the bar before the last one is the
# previous session only if it's actually adjacent. Generous enough to
# clear a weekend plus a 설/추석 closure, which are real adjacency.
MAX_BASELINE_GAP_DAYS = 7


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


# Corporate/fund boilerplate Yahoo carries in shortName. The 종목명
# column is one narrow line, so the suffixes are trimmed to keep the
# distinguishing part of the name visible before the ellipsis kicks in.
# The untrimmed name still rides along as fullName for the tooltip.
_NAME_SUFFIXES = (
    "american depositary shares", "ordinary shares", "common stock",
    "incorporated", "corporation", "company", "limited",
    "inc", "corp", "co", "ltd", "plc", "ag", "nv",
)


def _clean_us_name(name: str | None) -> str:
    """Strip trailing corporate suffixes off a Yahoo shortName —
    "Advanced Micro Devices, Inc." -> "Advanced Micro Devices". Names
    that end in no suffix (most ETFs) come back untouched, punctuation
    and all, so "Vale S.A." doesn't lose its final period."""
    if not name:
        return ""
    out = name.strip()
    while True:
        core = out.rstrip(" .,&")  # matched without the trailing "Inc." period
        lowered = core.lower()
        for suffix in _NAME_SUFFIXES:
            if lowered.endswith(" " + suffix) or lowered.endswith("," + suffix):
                trimmed = core[: len(core) - len(suffix)].rstrip(" .,&")
                if trimmed:
                    out = trimmed
                    break
                return out
        else:
            return out


def _fmt_krw_value(million_won: float) -> str:
    """Format a Naver 거래대금 figure (already in 백만원) as 조/억원,
    matching how 네이버 증권 itself displays trading value."""
    eok = million_won / 100  # 백만원 -> 억원
    jo = eok / 10_000  # 억원 -> 조원
    if jo >= 1:
        return f"{jo:,.2f}조"
    return f"{eok:,.0f}억"


def _usable_price(v) -> float | None:
    """Yahoo hands back NaN (and occasionally 0) for "no value". NaN is
    truthy and propagates silently through every %, so quote fields get
    filtered here rather than trusted."""
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if f == f and f != 0 else None


def _bars_adjacent(prev_ts, last_ts) -> bool:
    """Are these two daily bars consecutive sessions, or is there a hole
    in the series between them? See MAX_BASELINE_GAP_DAYS."""
    try:
        return abs((last_ts - prev_ts).days) <= MAX_BASELINE_GAP_DAYS
    except Exception:
        return True  # unparseable index — trust the bars rather than blank the row


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
        self.states: dict[str, SymbolState] = {
            s: SymbolState(symbol=s) for s in FIXED_SYMBOLS
        }
        self.news: list[dict] = []
        self.fx_news: list[dict] = []
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
        self.kr_rates: list[dict] = []
        self.kr_rates_stale: bool = True

    def all_symbols(self) -> list[str]:
        return list(FIXED_SYMBOLS)

    # -- data acquisition ---------------------------------------------

    def _fetch_quotes(self, symbols: list[str]) -> dict[str, tuple[float, float | None]]:
        """symbol -> (price, prev_close) from Yahoo's batched quote
        endpoint. Raises if the request fails, so the caller can fall
        back rather than silently reporting an empty market."""
        out: dict[str, tuple[float, float | None]] = {}
        for i in range(0, len(symbols), QUOTE_CHUNK):
            chunk = symbols[i:i + QUOTE_CHUNK]
            # Through YfData so the request carries yfinance's own
            # cookie/crumb auth, which this endpoint requires.
            resp = YfData().get(url=QUOTE_URL, params={"symbols": ",".join(chunk)})
            resp.raise_for_status()
            for q in resp.json()["quoteResponse"]["result"]:
                sym = q.get("symbol")
                price = _usable_price(q.get("regularMarketPrice"))
                if sym and price is not None:
                    out[sym] = (price, _usable_price(q.get("regularMarketPreviousClose")))
        return out

    def _fetch_from_bars(self, symbols: list[str]) -> dict[str, tuple[float, float | None]]:
        """Same shape as _fetch_quotes, from daily OHLC bars. Only a
        fallback for when the quote endpoint is unreachable: these bars
        carry the wrong Close for FX and futures (see QUOTE_URL), so the
        board runs degraded on this path rather than blank."""
        data = yf.download(
            tickers=" ".join(symbols),
            period="7d",
            interval="1d",
            group_by="ticker",
            progress=False,
            threads=True,
        )
        out: dict[str, tuple[float, float | None]] = {}
        single = len(symbols) == 1
        for sym in symbols:
            try:
                closes = (data["Close"] if single else data[sym]["Close"]).dropna()
                if len(closes) == 0:
                    continue
                prev = None
                if len(closes) >= 2 and _bars_adjacent(closes.index[-2], closes.index[-1]):
                    prev = float(closes.iloc[-2])
                out[sym] = (float(closes.iloc[-1]), prev)
            except Exception:
                continue
        return out

    def poll_prices(self) -> None:
        """Refresh the price AND its % baseline for every tracked symbol
        from one batched quote request.

        Both numbers come out of the same response, so the price and the
        % printed next to it can never be a session out of step — that
        pairing is the whole point. It used to break two ways: the
        baseline was refreshed on a separate 6-hour loop (so an
        overnight refresh, taken before the new KRX bar existed, left
        every morning % measured against D-2), and the daily bars it
        read report roughly the session's OPEN as the Close on FX and
        futures. Reading both from the quote endpoint fixes both, and
        also makes the board agree with the popups, which read the same
        fields per symbol.
        """
        symbols = self.all_symbols()
        try:
            quotes = self._fetch_quotes(symbols)
        except Exception:
            log.exception("quote fetch failed — falling back to daily bars")
            quotes = {}
        if not quotes:
            try:
                quotes = self._fetch_from_bars(symbols)
            except Exception:
                log.exception("poll_prices failed — keeping last known values")
                return

        now = time.time()
        for sym, (price, prev) in quotes.items():
            st = self.states.get(sym)
            if st is None:
                continue
            st.price = price
            # A missing baseline leaves the last known one in place — the
            # row keeps a real (if slightly old) %, and the next poll
            # corrects it. Never fabricated from the price itself, which
            # is what used to pin USD/CNH at a permanent +0.00%.
            if prev is not None:
                st.prev_close = prev
            st.history.append(price)
            st.stale = False
            st.last_ok = now

        # Anything not refreshed in the last 90s is flagged stale. Rows
        # missing from the response keep their last real value.
        for st in self.states.values():
            if now - st.last_ok > 90:
                st.stale = True

        self.last_snapshot_at = datetime.now(tz=KST)
        # This poll no longer builds DataFrames itself, but the popup and
        # chart endpoints still call yf.download on user interaction, and
        # yfinance's threaded download appends one worker Thread per
        # symbol to multitasking's global TASKS list without ever
        # removing them — left alone that compounded into ~GB/day of RSS
        # (→ OOM restarts) on small hosts. This 10s loop is the natural
        # place to keep sweeping it. Pruning is safe: yf.download awaits
        # completion via shared._DFS, not this list.
        multitasking.config["TASKS"] = [
            t for t in multitasking.config["TASKS"] if t.is_alive()
        ]
        gc.collect()

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

    def poll_fx_news(self) -> None:
        """환율 주요뉴스 from Naver marketindex — shown inside the FX panel."""
        try:
            items = fx_news.fetch_fx_news(limit=10)
        except Exception:
            log.exception("fx news poll failed — keeping last known headlines")
            return
        if not items:
            log.warning("fx news returned no rows — keeping last known headlines")
            return
        self.fx_news = items

    def poll_kr_rates(self) -> None:
        """국내 시장금리 from Naver marketindex (daily fixings)."""
        try:
            rows = kr_rates.fetch_kr_rates()
        except Exception:
            log.exception("poll_kr_rates failed — keeping last known fixings")
            return
        if not rows:
            log.warning("poll_kr_rates returned no rows — keeping last known fixings")
            return
        self.kr_rates = rows
        self.kr_rates_stale = False

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

        ticker = [
            self._row(t["symbol"], t["label"], up, down, flat)
            | {k: t[k] for k in ("label", "kind", "name", "pair", "contract", "sub") if k in t}
            for t in TICKER_STRIP
        ]

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
                "kind": m.get("kind", ""),  # "ETF" | "ETN" | "" (개별종목)
                "price": _fmt_num(m["price"], 0),
                "pct": f"{pct:+.2f}%",
                "color": color,
                "arrow": arrow,
                "stale": self.movers_stale,
            }

        gainers = [mover_row(m, i) for i, m in enumerate(self.mover_gainers)]
        losers = [mover_row(m, i) for i, m in enumerate(self.mover_losers)]

        def kr_most_traded_row(m: dict, i: int) -> dict:
            pct = m["pct"]
            return {
                "rank": i + 1,
                "symbol": m.get("symbol"),
                "name": m["name"] + ("*" if m.get("market") == "KOSDAQ" else ""),
                "market": m.get("market"),
                "kind": m.get("kind", ""),
                "price": _fmt_num(m["price"], 0),
                "pct": f"{pct:+.2f}%",
                "color": up if pct > 0 else down if pct < 0 else flat,
                "arrow": "▲" if pct > 0 else "▼" if pct < 0 else "–",
                "tradingValue": _fmt_krw_value(m["tradingValueMm"]),
                "stale": self.kr_most_traded_stale,
            }

        kr_most_traded = [kr_most_traded_row(m, i) for i, m in enumerate(self.kr_most_traded)]

        def us_mover_row(m: dict, i: int) -> dict:
            pct = m["pct"]
            color = up if pct > 0 else down if pct < 0 else flat
            arrow = "▲" if pct > 0 else "▼" if pct < 0 else "–"
            full_name = m.get("name") or m["symbol"]
            return {
                "rank": i + 1,
                "symbol": m["symbol"],
                "name": _clean_us_name(full_name) or m["symbol"],
                "fullName": full_name,
                "kind": m.get("kind", ""),  # "ETF" | "" (개별종목)
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

        kr_rates_rows = []
        for r in self.kr_rates:
            chg = r["change"]
            kr_rates_rows.append({
                "code": r["code"],
                "name": r["name"],
                "value": f"{r['value']:.2f}%",
                "chg": f"{chg * 100:+.0f}bp" if chg else "0bp",
                "color": up if chg > 0 else down if chg < 0 else flat,
                "arrow": "▲" if chg > 0 else "▼" if chg < 0 else "–",
                "stale": self.kr_rates_stale,
            })

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

        fx_news_rows = [
            {
                "time": news_time(n["time"]),
                "headline": n["title"],
                "tag": n["press"],
                "url": n["url"],
            }
            for n in self.fx_news
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
            "krRates": kr_rates_rows,
            "fxNews": fx_news_rows,
            "news": news,
        }
