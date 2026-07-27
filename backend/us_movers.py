"""Real US top-gainers / top-losers / most-active rankings, sourced
from Yahoo Finance's own predefined market screeners (day_gainers,
day_losers, most_actives) via yfinance — the same lists shown on
finance.yahoo.com. No Korean-market equivalent exists on the Korean
side of this app; US instruments are Yahoo Finance's territory
end-to-end, same as the rest of the board's US/international data.

ETFs are ranked alongside the stocks. Yahoo's predefined screeners are
stocks-only and yfinance can't reach anything else — yf.screen() picks
the screener's quoteType off the query class ("EQUITY" for EquityQuery,
"MUTUALFUND" for FundQuery), so there is no ETF path through it. Yahoo's
screener endpoint itself does accept quoteType "ETF", so the ETF side is
queried directly against that endpoint (through yfinance's own session,
which carries the crumb/cookie auth) and merged into each ranking.
"""
from __future__ import annotations

import json
import logging

import yfinance as yf
from yfinance.const import _QUERY1_URL_
from yfinance.data import YfData

log = logging.getLogger("us_movers")

_SCREENS = {"gainers": "day_gainers", "losers": "day_losers", "actives": "most_actives"}

_SCREENER_URL = f"{_QUERY1_URL_}/v1/finance/screener"
_SCREENER_PARAMS = {
    "corsDomain": "finance.yahoo.com", "formatted": "false", "lang": "en-US", "region": "US",
}

# Mirrors of the predefined equity screens' own filters so the ETF side
# is ranked on the same terms as the stock side, minus the market-cap
# floor (funds don't carry intradaymarketcap — the volume floor is what
# keeps illiquid micro-ETFs out).
_US_REGION = {"operator": "eq", "operands": ["region", "us"]}
_ETF_SCREENS = {
    "gainers": {
        "sortField": "percentchange", "sortType": "DESC",
        "operands": [
            _US_REGION,
            {"operator": "gt", "operands": ["percentchange", 3]},
            {"operator": "gte", "operands": ["intradayprice", 5]},
            {"operator": "gt", "operands": ["dayvolume", 15000]},
        ],
    },
    "losers": {
        "sortField": "percentchange", "sortType": "ASC",
        "operands": [
            _US_REGION,
            {"operator": "lt", "operands": ["percentchange", -2.5]},
            {"operator": "gte", "operands": ["intradayprice", 5]},
            {"operator": "gt", "operands": ["dayvolume", 20000]},
        ],
    },
    "actives": {
        "sortField": "dayvolume", "sortType": "DESC",
        "operands": [
            _US_REGION,
            {"operator": "gt", "operands": ["dayvolume", 5000000]},
        ],
    },
}


def _row(q: dict, is_etf: bool) -> dict | None:
    symbol = q.get("symbol")
    price = q.get("regularMarketPrice")
    pct = q.get("regularMarketChangePercent")
    if not symbol or price is None or pct is None:
        return None
    return {
        "symbol": symbol,
        "name": q.get("shortName") or q.get("longName") or symbol,
        "price": float(price),
        "pct": float(pct),
        "volume": q.get("regularMarketVolume"),
        "kind": "ETF" if is_etf else "",
    }


def _fetch_screen(kind: str, top_n: int) -> list[dict]:
    """Stocks, via Yahoo's predefined screener."""
    res = yf.screen(_SCREENS[kind], count=top_n)
    return [r for r in (_row(q, False) for q in res.get("quotes", [])) if r]


def _fetch_etf_screen(kind: str, top_n: int) -> list[dict]:
    """ETFs, via the same screener endpoint with quoteType ETF."""
    screen = _ETF_SCREENS[kind]
    body = {
        "offset": 0, "size": top_n, "userId": "", "userIdType": "guid",
        "quoteType": "ETF",
        "sortField": screen["sortField"], "sortType": screen["sortType"],
        "query": {"operator": "and", "operands": screen["operands"]},
    }
    resp = YfData().post(
        _SCREENER_URL,
        data=json.dumps(body, separators=(",", ":"), ensure_ascii=False),
        params=_SCREENER_PARAMS,
    )
    resp.raise_for_status()
    res = resp.json()["finance"]["result"][0]
    # Yahoo occasionally tags a row EQUITY inside its own ETF universe;
    # the screener it came out of is the authority, not the row's field.
    return [r for r in (_row(q, True) for q in res.get("quotes", [])) if r]


def _merge(stocks: list[dict], etfs: list[dict], key, reverse: bool, top_n: int) -> list[dict]:
    """One ranking over both universes. Dedup by symbol (a ticker can
    surface in both screens) keeping the stock row, since its quote
    fields come from the screener Yahoo itself classifies it under."""
    seen = {r["symbol"] for r in stocks}
    merged = stocks + [r for r in etfs if r["symbol"] not in seen]
    merged.sort(key=key, reverse=reverse)
    return merged[:top_n]


def _screen_both(kind: str, per_side: int) -> tuple[list[dict], list[dict]]:
    stocks: list[dict] = []
    etfs: list[dict] = []
    try:
        stocks = _fetch_screen(kind, per_side)
    except Exception:
        log.exception("us %s stock screen failed", kind)
    try:
        etfs = _fetch_etf_screen(kind, per_side)
    except Exception:
        log.exception("us %s ETF screen failed", kind)
    return stocks, etfs


# Each side is over-fetched so the merged top_n is a real cross-universe
# ranking rather than "top 10 stocks plus whatever ETFs fit".
_PER_SIDE = 50


def fetch_us_gainers_losers(top_n: int = 10) -> tuple[list[dict], list[dict]]:
    """Return (gainers, losers), each up to `top_n` real rows drawn from
    Yahoo Finance's Day Gainers / Day Losers screeners across both
    stocks and ETFs, ranked together by % change."""
    g_stocks, g_etfs = _screen_both("gainers", _PER_SIDE)
    l_stocks, l_etfs = _screen_both("losers", _PER_SIDE)
    gainers = _merge(g_stocks, g_etfs, key=lambda r: r["pct"], reverse=True, top_n=top_n)
    losers = _merge(l_stocks, l_etfs, key=lambda r: r["pct"], reverse=False, top_n=top_n)
    return gainers, losers


def fetch_us_most_active(top_n: int = 10) -> list[dict]:
    """Return up to `top_n` real rows from Yahoo Finance's Most Active
    screeners (stocks + ETFs), sorted by volume descending."""
    stocks, etfs = _screen_both("actives", _PER_SIDE)
    return _merge(stocks, etfs, key=lambda r: r.get("volume") or 0, reverse=True, top_n=top_n)
