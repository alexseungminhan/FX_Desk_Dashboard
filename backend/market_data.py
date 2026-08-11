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

import bond_curve
import bond_flow
import bond_quotes
import fx_bond_issue
import fx_news
import keyword_news
import kospi200_basis
import kr_index
import kr_investor_flow
import kr_movers
import kr_rates
import krw_swap
import naver_news
import short_term_rates
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


# 프론트의 KW_NEWS_PAGE_SIZE × KW_NEWS_PAGES 와 짝을 이룬다 (app.js).
# 화면이 안 쓰는 뒤쪽 결과까지 매 스냅샷마다 실어 보내지 않기 위한 상한.
KEYWORD_NEWS_SNAPSHOT_LIMIT = 45
# 환율 뉴스도 같은 3페이지 구성 (app.js 의 FX_NEWS_PAGE_SIZE × 3).
FX_NEWS_SNAPSHOT_LIMIT = 45


def _fmt_signed_flow(v: float, unit: str) -> str:
    """투자자 순매수 표시. 네이버가 주는 값이 이미 억원이라 조 단위만
    접는다(선물은 계약 수 그대로). 순매수/순매도는 부호가 전부라 +는 항상
    붙인다."""
    if unit == "contract":
        return f"{v:+,.0f}" if round(v) else "0"
    if abs(v) >= 10_000:
        return f"{v / 10_000:+,.2f}조"
    # 억 미만은 반올림하면 0이 되는데, 거기에 부호를 붙이면 "-0억"처럼
    # 방향이 있는 것처럼 보인다. 0은 부호 없이 0으로 둔다.
    return f"{v:+,.0f}억" if round(v) else "0억"


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
        self.investor_flow: dict = {}
        self.investor_flow_stale: bool = True
        self.keyword_news: dict = {}
        self.keyword_news_attempted: bool = False
        self.basis: dict | None = None
        self.basis_stale: bool = True
        self.bond_curve: dict | None = None
        self.bond_curve_stale: bool = True
        self.fx_bond_issues: list[dict] = []
        self.bond_flow: dict | None = None
        self.bond_flow_stale: bool = True
        self.swap_points: dict | None = None
        self.irs_crs: dict | None = None
        self.fx_implied: dict | None = None
        self.bond_quotes: dict | None = None
        self.short_term_rates: dict | None = None

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

        The one exception is the three KRX indices, which Yahoo delays by
        20 minutes and Naver publishes live — those get overwritten from
        Naver below (see kr_index).
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

        # Live KOSPI/KOSDAQ/KOSPI200 on top of Yahoo's 20-minute-delayed
        # quotes. Whatever Naver doesn't return keeps the Yahoo value —
        # delayed still beats a blank row.
        try:
            quotes.update(kr_index.fetch_prices())
        except Exception:
            log.exception("naver index poll failed — keeping delayed Yahoo quotes")

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
        """환율 주요뉴스 from Naver marketindex — shown inside the FX panel.
        화면이 3페이지(15건×3)를 넘기므로 그만큼 채워온다 — 이 섹션은
        하루 20건씩만 실려서 날짜를 거슬러 올라가며 모은다 (fx_news.py)."""
        try:
            items = fx_news.fetch_fx_news(limit=FX_NEWS_SNAPSHOT_LIMIT)
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

    def _cd_rate(self) -> float | None:
        """CD(91일) 고시금리 — 선물 이론가의 조달금리 자리에 들어간다."""
        for r in self.kr_rates:
            if r.get("code") == "IRR_CD91":
                return r.get("value")
        return None

    def poll_investor_flow(self) -> None:
        """투자자별 매매동향 (코스피·코스닥·선물) — kr_investor_flow.py."""
        try:
            data = kr_investor_flow.fetch_investor_flow()
        except Exception:
            log.exception("poll_investor_flow failed — keeping last known flows")
            return
        if not data:
            log.warning("poll_investor_flow returned nothing — keeping last known flows")
            return
        self.investor_flow = data
        self.investor_flow_stale = False

    def poll_keyword_news(self) -> None:
        """외화채권·M&A 등 주제별 키워드 뉴스 — keyword_news.py."""
        try:
            data = keyword_news.fetch_keyword_news()
        except Exception:
            log.exception("poll_keyword_news failed — keeping last known items")
            return
        self.keyword_news_attempted = True
        # 그룹 전체가 비는 건 검색이 막혔다는 뜻이므로 직전 결과를 살린다.
        if not any(g.get("items") for g in data.values()):
            log.warning("poll_keyword_news returned no items — keeping last known items")
            return
        self.keyword_news = data

    def poll_basis(self) -> None:
        """코스피200 현·선물 베이시스 — kospi200_basis.py."""
        try:
            data = kospi200_basis.fetch_basis(self._cd_rate())
        except Exception:
            log.exception("poll_basis failed — keeping last known basis")
            return
        if not data:
            log.warning("poll_basis returned nothing — keeping last known basis")
            return
        self.basis = data
        self.basis_stale = False

    def poll_bond_curve(self) -> None:
        """채권 만기수익률 곡선 (SEIBro) — bond_curve.py."""
        try:
            data = bond_curve.fetch_bond_curve()
        except Exception:
            log.exception("poll_bond_curve failed — keeping last known curve")
            return
        if not data:
            log.warning("poll_bond_curve returned nothing — keeping last known curve")
            return
        self.bond_curve = data
        self.bond_curve_stale = False

    def poll_fx_bond_issues(self) -> None:
        """외화표시채 발행 내역 (SEIBro) — fx_bond_issue.py."""
        try:
            rows = fx_bond_issue.fetch_fx_bond_issues()
        except Exception:
            log.exception("poll_fx_bond_issues failed — keeping last known issues")
            return
        if not rows:
            log.warning("poll_fx_bond_issues returned nothing — keeping last known issues")
            return
        self.fx_bond_issues = rows

    def poll_bond_flow(self) -> None:
        """장외채권 투자자별 순매수 (KOFIA) — bond_flow.py."""
        try:
            data = bond_flow.fetch_bond_flow()
        except Exception:
            log.exception("poll_bond_flow failed — keeping last known flows")
            return
        if not data:
            log.warning("poll_bond_flow returned nothing — keeping last known flows")
            return
        self.bond_flow = data
        self.bond_flow_stale = False

    def _usdkrw_spot(self) -> float | None:
        """스왑포인트 연율 환산의 분모. 보드가 이미 받고 있는 값을 쓴다."""
        st = self.states.get("USDKRW=X")
        return st.price if st and st.price else None

    def poll_krw_swap(self) -> None:
        """원화 FX 스왑포인트 · IRS · CRS (서울외국환중개) — krw_swap.py."""
        try:
            points = krw_swap.fetch_swap_points(self._usdkrw_spot())
            curves = krw_swap.fetch_irs_crs()
        except Exception:
            log.exception("poll_krw_swap failed — keeping last known swaps")
            return
        if points:
            self.swap_points = points
        if curves:
            self.irs_crs = curves
        if not points and not curves:
            log.warning("poll_krw_swap returned nothing — keeping last known swaps")

        # FX-implied 원화 금리·CCS 베이시스. 스왑포인트·IRS 1Y·CD 91일이 다
        # 있어야 나오고, 셋 중 하나라도 비면 조용히 지난 값을 유지한다 —
        # 반쯤 채운 베이시스를 띄우느니 안 띄우는 게 낫다.
        try:
            implied = krw_swap.fetch_implied(
                self.swap_points, self.irs_crs, self._cd_rate())
        except Exception:
            log.exception("fetch_implied failed — keeping last known implied basis")
            return
        if implied:
            self.fx_implied = implied

    def poll_bond_quotes(self) -> None:
        """지표종목 최종호가수익률 (KOFIA) — bond_quotes.py."""
        try:
            data = bond_quotes.fetch_bond_quotes()
        except Exception:
            log.exception("poll_bond_quotes failed — keeping last known quotes")
            return
        if not data:
            log.warning("poll_bond_quotes returned nothing — keeping last known quotes")
            return
        self.bond_quotes = data

    def poll_short_term_rates(self) -> None:
        """CP·전단채 대표수익률 (KOFIA) — short_term_rates.py."""
        try:
            data = short_term_rates.fetch_short_term_rates()
        except Exception:
            log.exception("poll_short_term_rates failed — keeping last known rates")
            return
        if not data:
            log.warning("poll_short_term_rates returned nothing — keeping last known rates")
            return
        self.short_term_rates = data

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

        # -- 수급: 투자자별 순매수 -------------------------------------
        # 순매수는 빨강(up), 순매도는 파랑(down) — 보드의 다른 KR 패널과
        # 같은 색 규칙을 그대로 쓴다.
        # 네이버 표의 컬럼 순서 그대로 — 원본과 나란히 놓고 검증하기 쉽다.
        # 기관계는 그 아래 6개 주체의 합이라, 막대로 같이 그리면 이중 계상이
        # 된다. chart 플래그로 표에는 남기고 차트에서만 뺀다.
        INVESTORS = [
            ("individual", "개인", True), ("foreign", "외국인", True),
            ("institution", "기관계", False),
            ("financial", "금융투자", True), ("insurance", "보험", True),
            ("trust", "투신", True), ("bank", "은행", True),
            ("otherFinance", "기타금융", True), ("pension", "연기금", True),
            ("corporate", "기타법인", True),
        ]

        def flow_market(data: dict) -> dict:
            unit = data["unit"]
            periods = {}
            for p in kr_investor_flow.PERIODS:
                vals = data["periods"][p["key"]]
                periods[p["key"]] = [
                    {
                        "key": key,
                        "label": label,
                        "value": _fmt_signed_flow(vals[key], unit),
                        # 차트는 스케일을 직접 잡아야 해서 원본 수치도 같이 내린다.
                        "raw": vals[key],
                        "chart": in_chart,
                        "color": up if vals[key] > 0 else down if vals[key] < 0 else flat,
                    }
                    for key, label, in_chart in INVESTORS
                ]
            return {
                "label": data["label"],
                "unitLabel": "계약" if unit == "contract" else "억원",
                "asOf": data["asOf"],
                "days": data["days"],
                "periods": periods,
                "stale": self.investor_flow_stale,
            }

        investor_flow = {k: flow_market(v) for k, v in self.investor_flow.items()}

        # -- 현·선물 베이시스 ------------------------------------------
        basis = None
        if self.basis:
            b = self.basis
            basis = {
                "spot": f"{b['spot']:,.2f}",
                "futures": f"{b['futures']:,.2f}",
                "contract": b["contract"],
                "expiry": b["expiry"],
                "daysToExpiry": b["daysToExpiry"],
                "basis": f"{b['basis']:+,.2f}",
                "basisColor": up if b["basis"] > 0 else down if b["basis"] < 0 else flat,
                "state": b["state"],
                "theoretical": f"{b['theoretical']:,.2f}" if b["theoretical"] is not None else "—",
                "theoBasis": f"{b['theoBasis']:+,.2f}" if b["theoBasis"] is not None else "—",
                "spread": f"{b['spread']:+.2f}%" if b["spread"] is not None else "—",
                "spreadColor": (
                    flat if b["spread"] is None
                    else up if b["spread"] > 0 else down if b["spread"] < 0 else flat
                ),
                "valuation": b["valuation"] or "—",
                "assumption": (
                    f"조달금리 CD91 {b['rate']:.2f}%"
                    + ("(대체값)" if b["rateIsFallback"] else "")
                    + f" · 배당수익률 {b['dividendYield']:.1f}% 가정"
                ),
                "stamp": b["stamp"],
                "stale": self.basis_stale,
            }

        # -- 채권 수익률 곡선 ------------------------------------------
        # 금리는 색으로 방향을 나타낼 게 없는 수준(레벨) 값이라 색을 입히지
        # 않는다. 국고채 대비 스프레드만 bp로 같이 내려 크레딧 폭을 본다.
        bond_curve_rows = None
        if self.bond_curve:
            curves = self.bond_curve["curves"]
            base = {p["label"]: p["yield"] for p in curves[0]["points"]} if curves else {}
            bond_curve_rows = {
                "asOf": self.bond_curve["asOf"],
                "tenors": [t[1] for t in bond_curve.TENORS],
                "baseLabel": curves[0]["label"] if curves else "",
                "curves": [
                    {
                        "key": c["key"],
                        "label": c["label"],
                        "points": [
                            {
                                "label": p["label"],
                                "years": p["years"],
                                "yield": p["yield"],
                                "value": f"{p['yield']:.2f}",
                                "spread": (
                                    None if i == 0 or p["label"] not in base
                                    else round((p["yield"] - base[p["label"]]) * 100)
                                ),
                            }
                            for p in c["points"]
                        ],
                    }
                    for i, c in enumerate(curves)
                ],
                "stale": self.bond_curve_stale,
            }

        # -- 원화 스왑 (스왑포인트 · IRS/CRS · 국고채-IRS) ---------------
        # 금리·연율은 레벨이라 색을 안 입힌다. 부호가 뜻을 갖는 두 값만
        # 색을 준다 — 통화베이시스(CRS-IRS)와 국고채-IRS 스프레드.
        def _fmt_pt(v):
            return f"{v:,.0f}" if v is not None else "—"

        def _fmt_ann(v):
            return f"{v:+.2f}%" if v is not None else "—"

        # 스왑포인트 Mid = (Bid + Offer) / 2. 한쪽만 고시된 만기는 폭이
        # 없어 Mid 가 성립하지 않으므로 비운다 — 연율 환산에 쓰는
        # krw_swap 쪽 mid(한쪽만 있으면 그 값) 와는 일부러 다르다.
        def _fmt_mid(o):
            b, a = o.get("bid"), o.get("offer")
            if b is None or a is None:
                return "—"
            m = (b + a) / 2
            # 폭이 홀수면 .5 가 떨어진다 — 그때만 소수를 보인다.
            return f"{m:,.0f}" if m == int(m) else f"{m:,.1f}"

        swap_rows = None
        if self.swap_points:
            sp = self.swap_points
            swap_rows = {
                "asOf": sp["asOf"],
                "spot": f"{sp['spot']:,.2f}" if sp["spot"] else "—",
                # 연율 환산에 쓴 스팟이 고시일 종가인지 실시간인지 — 스왑포인트가
                # 전영업일 고시라 이게 어긋나면 연율이 통째로 밀린다.
                "spotSource": sp.get("spotSource", ""),
                "spotAsOf": sp.get("spotAsOf", ""),
                "spotLive": f"{sp['spotLive']:,.2f}" if sp.get("spotLive") else "",
                "rows": [
                    {
                        "label": r["label"],
                        "smbs": {
                            "bid": _fmt_pt(r["smbs"]["bid"]),
                            "offer": _fmt_pt(r["smbs"]["offer"]),
                            "mid": _fmt_mid(r["smbs"]),
                            "annualized": _fmt_ann(r["smbs"]["annualized"]),
                        },
                        "kmb": {
                            "bid": _fmt_pt(r["kmb"]["bid"]),
                            "offer": _fmt_pt(r["kmb"]["offer"]),
                            "mid": _fmt_mid(r["kmb"]),
                            "annualized": _fmt_ann(r["kmb"]["annualized"]),
                        },
                    }
                    for r in sp["rows"]
                ],
            }

        # -- FX-implied 원화 금리 · CCS 베이시스 --------------------------
        # basis 만 부호가 뜻을 가진다 (음수 = 스왑시장 원화 조달이 IRS 보다
        # 싸다). 나머지는 레벨이라 색을 안 입힌다.
        implied_rows = None
        if self.fx_implied:
            fi = self.fx_implied
            usd = fi["usd"]

            def _pct(v, n=2):
                return f"{v * 100:.{n}f}%" if v is not None else "—"

            def _bp(v):
                return f"{v:+.2f}bp" if v is not None else "—"

            six = fi.get("sixMonth")
            implied_rows = {
                "asOf": fi.get("asOf", ""),
                "spotDate": fi.get("spotDate", ""),
                "quoteDate": fi.get("quoteDate", ""),
                "spotSource": fi.get("spotSource", ""),
                "spotAsOf": fi.get("spotAsOf", ""),
                "usdSource": usd["sourceLabel"],
                "usdAsOf": usd.get("asOf", ""),
                "usdNote": usd.get("note", ""),
                "pointSource": fi.get("pointSource", ""),
                # 화면에는 6M 이 보간이라는 사실 한 줄 + 입력이 어긋났을 때의
                # 데이터 경고만 올린다. 보간 폭은 바로 위 방식별 값 줄에 이미
                # 찍히고, 컨벤션 얘기는 par 열과 각주가 답이라 또 적으면 중복이다.
                # fx_implied 는 경고를 전부 계속 계산하므로 로그에는 남는다.
                "warnings": ([f"6M IRS 는 고시가 없어 보간값이다 ({six['method']})."]
                             if six else []) + fi.get("dataWarnings", []),
                "sixMethod": six["method"] if six else "",
                "sixSpreadBp": f"{six['spreadBp']:.1f}bp" if six else "",
                "sixVariants": [
                    {"name": {"linear_days": "일수 선형", "log_df": "log-DF",
                              "pchip": "PCHIP"}.get(k, k),
                     "value": _pct(v, 4)}
                    for k, v in (six["variants"].items() if six else [])
                ],
                "rows": [
                    {
                        "label": r["label"],
                        "days": str(r["days"]),
                        "valueDate": (fi.get("valueDates", {}).get(r["label"]) or "")[5:],
                        "swapRate": _pct(r["swapRate"]),
                        "usdRate": _pct(r["usdRate"], 4),
                        # 화면 값은 par swap rate — KRW IRS 고시와 같은 물건이다.
                        # 단리 zero 는 pricer `KRW Zero` 대조용 검증열로 나란히 둔다.
                        "yield": _pct(r["parRate"], 4),
                        "yieldSimple": _pct(r["yieldSimple"], 4),
                        "irs": _pct(r["irs"], 3),
                        "irsSource": r["irsSource"],
                        "interpolated": r["interpolated"],
                        # 분기 그리드에 보간으로 채운 점이 있으면(1Y 의 9M) 표시.
                        "pillarSource": r.get("pillarSource", ""),
                        "basis": _bp(r["basisBp"]),
                        "basisColor": (
                            flat if not r["basisBp"]
                            else up if r["basisBp"] > 0 else down),
                        "crossTerm": _bp(r["crossTermBp"]),
                    }
                    for r in fi["rows"]
                ],
            }

        irs_crs_rows = None
        if self.irs_crs:
            ic = self.irs_crs
            irs_crs_rows = {
                "asOf": ic["asOf"],
                "source": ic.get("source", ""),
                "rows": [
                    {
                        "label": r["label"],
                        "irs": f"{r['irs']:.3f}" if r["irs"] is not None else "—",
                        "crs": f"{r['crs']:.3f}" if r["crs"] is not None else "—",
                        "basis": f"{r['basisBp']:+d}bp" if r["basisBp"] is not None else "—",
                        "basisColor": (
                            flat if not r["basisBp"] else up if r["basisBp"] > 0 else down
                        ),
                    }
                    for r in ic["rows"]
                ],
            }

        # 국고채(현물) - IRS 스프레드. 국고채는 KOFIA 최종호가(bond_quotes),
        # IRS 는 위 커브 — 겹치는 만기(1Y/3Y/5Y/10Y)만 계산한다.
        bond_irs_rows = None
        if self.bond_quotes and self.irs_crs:
            ktb = {r["label"]: r["yield"] for r in self.bond_quotes["rows"]}
            irs_by_term = {r["label"]: r["irs"] for r in self.irs_crs["rows"]}
            pairs = [("1Y", "국고채권(1년)"), ("3Y", "국고채권(3년)"),
                     ("5Y", "국고채권(5년)"), ("10Y", "국고채권(10년)")]
            rows_ = []
            for term, ktb_label in pairs:
                b, i = ktb.get(ktb_label), irs_by_term.get(term)
                if b is None or i is None:
                    continue
                spread_bp = round((b - i) * 100, 1)
                rows_.append({
                    "label": term,
                    "ktb": f"{b:.3f}",
                    "irs": f"{i:.3f}",
                    "spread": f"{spread_bp:+.1f}bp",
                    "spreadColor": flat if not spread_bp else up if spread_bp > 0 else down,
                })
            if rows_:
                bond_irs_rows = {"rows": rows_}

        # -- 지표종목 최종호가수익률 ------------------------------------
        # 금리 레벨 자체는 방향이 없으니 색을 안 입히고, 전일대비만 색을
        # 준다. 금리 상승은 채권 약세라 KR 스킴의 up(빨강)과 뜻이 다르므로
        # 여기서는 상승/하락 그대로 읽으라고 화살표를 같이 붙인다.
        bond_quote_rows = None
        if self.bond_quotes:
            bond_quote_rows = {
                "asOf": self.bond_quotes["asOf"],
                "rows": [
                    {
                        "label": r["label"],
                        "term": r["term"],
                        "yield": f"{r['yield']:.3f}",
                        "changeBp": (
                            "—" if r["changeBp"] is None
                            else f"{r['changeBp']:+.1f}bp"
                        ),
                        "changeColor": (
                            flat if not r["changeBp"]
                            else up if r["changeBp"] > 0 else down
                        ),
                        "arrow": (
                            "–" if not r["changeBp"]
                            else "▲" if r["changeBp"] > 0 else "▼"
                        ),
                        "range": (
                            f"{r['low']:.2f} ~ {r['high']:.2f}"
                            if r["low"] is not None and r["high"] is not None else "—"
                        ),
                    }
                    for r in self.bond_quotes["rows"]
                ],
            }

        # -- 단기금융시장 금리 (CP·전단채) -----------------------------
        short_term_rows = None
        if self.short_term_rates:
            st = self.short_term_rates
            short_term_rows = {
                "asOf": st["asOf"],
                "tenors": st["tenors"],
                "rows": [
                    {
                        "label": r["label"],
                        "rates": [
                            "—" if v is None else f"{v:.2f}" for v in r["rates"]
                        ],
                        # 거래대금이 백만원 단위라 조/억으로 접는다.
                        "amount": _fmt_krw_value(r["amount"]) if r["amount"] else "—",
                    }
                    for r in st["rows"]
                ],
            }

        # -- 채권 수급 -------------------------------------------------
        # 주식 수급과 같은 색 규칙 (순매수 빨강 / 순매도 파랑). 단위가 이미
        # 억원이라 조 단위만 접는다.
        def fmt_eok(v: int) -> str:
            if abs(v) >= 10_000:
                return f"{v / 10_000:+,.2f}조"
            return f"{v:+,}억" if v else "0억"

        bond_flow_rows = None
        if self.bond_flow:
            bf = self.bond_flow
            bond_flow_rows = {
                "asOf": bf["asOf"],
                "bondTypes": bf["bondTypes"],
                "periods": {
                    p["key"]: {
                        bond_type: [
                            {
                                "key": c["key"],
                                "label": c["label"],
                                "value": fmt_eok(c["value"]),
                                "raw": c["value"],
                                "color": up if c["value"] > 0 else down if c["value"] < 0 else flat,
                            }
                            for c in cells
                        ]
                        for bond_type, cells in bf["periods"][p["key"]].items()
                    }
                    for p in bond_flow.PERIODS
                },
                "stale": self.bond_flow_stale,
            }

        # -- 외화표시채 발행 -------------------------------------------
        fx_bond_rows = [
            {k: v for k, v in row.items() if k != "sortKey"}
            for row in self.fx_bond_issues
        ]

        # -- 키워드 뉴스 -----------------------------------------------
        # 스냅샷은 10초마다 접속자 전원에게 통째로 나간다. 화면은 그룹당
        # 3페이지(45건)까지만 보여주므로 M&A처럼 150건 가까이 잡히는
        # 그룹을 다 실어 보내면 매 틱마다 버리는 데이터를 나르게 된다.
        keyword_news_rows = {
            key: {
                "label": g["label"],
                "items": [
                    {
                        "when": it["when"] or "—",
                        "headline": it["title"],
                        "press": it["press"],
                        "hit": it["hit"],
                        "url": it["url"],
                    }
                    for it in g["items"][:KEYWORD_NEWS_SNAPSHOT_LIMIT]
                ],
            }
            for key, g in self.keyword_news.items()
        }

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
            "investorFlow": investor_flow,
            "investorFlowPeriods": kr_investor_flow.PERIODS,
            "basis": basis,
            "keywordNews": keyword_news_rows,
            # 화면이 "불러오는 중"과 "수집 실패"를 구분하게 한다. 클라우드에서
            # 네이버 검색이 IP로 막히면 영영 안 채워지는데, 그때 계속
            # 로딩 문구만 띄우면 고장을 못 알아챈다.
            "keywordNewsStatus": (
                "ok" if any(g.get("items") for g in keyword_news_rows.values())
                else "failed" if self.keyword_news_attempted else "pending"
            ),
            "bondCurve": bond_curve_rows,
            "fxBondIssues": fx_bond_rows,
            "bondFlow": bond_flow_rows,
            "bondFlowPeriods": bond_flow.PERIODS,
            "bondQuotes": bond_quote_rows,
            "shortTermRates": short_term_rows,
            "swapPoints": swap_rows,
            "fxImplied": implied_rows,
            "irsCrs": irs_crs_rows,
            "bondIrs": bond_irs_rows,
        }
