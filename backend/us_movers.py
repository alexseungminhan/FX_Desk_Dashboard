"""Real US top-gainers / top-losers / most-active rankings, sourced
from Yahoo Finance's own predefined market screeners (day_gainers,
day_losers, most_actives) via yfinance — the same lists shown on
finance.yahoo.com. No Korean-market equivalent exists on the Korean
side of this app; US instruments are Yahoo Finance's territory
end-to-end, same as the rest of the board's US/international data.
"""
from __future__ import annotations

import logging

import yfinance as yf

log = logging.getLogger("us_movers")

_SCREENS = {"gainers": "day_gainers", "losers": "day_losers", "actives": "most_actives"}


def _fetch_screen(kind: str, top_n: int) -> list[dict]:
    res = yf.screen(_SCREENS[kind], count=top_n)
    out = []
    for q in res.get("quotes", []):
        symbol = q.get("symbol")
        price = q.get("regularMarketPrice")
        pct = q.get("regularMarketChangePercent")
        if not symbol or price is None or pct is None:
            continue
        out.append({
            "symbol": symbol,
            "name": q.get("shortName") or q.get("longName") or symbol,
            "price": float(price),
            "pct": float(pct),
            "volume": q.get("regularMarketVolume"),
        })
    return out


def fetch_us_gainers_losers(top_n: int = 10) -> tuple[list[dict], list[dict]]:
    """Return (gainers, losers), each up to `top_n` real rows from
    Yahoo Finance's Day Gainers / Day Losers screeners."""
    gainers, losers = [], []
    try:
        gainers = _fetch_screen("gainers", top_n)
    except Exception:
        log.exception("us day_gainers screen failed")
    try:
        losers = _fetch_screen("losers", top_n)
    except Exception:
        log.exception("us day_losers screen failed")
    return gainers, losers


def fetch_us_most_active(top_n: int = 10) -> list[dict]:
    """Return up to `top_n` real rows from Yahoo Finance's Most Active
    screener, sorted by volume descending (the screener's native order)."""
    try:
        rows = _fetch_screen("actives", top_n)
    except Exception:
        log.exception("us most_actives screen failed")
        return []
    rows.sort(key=lambda r: r.get("volume") or 0, reverse=True)
    return rows
