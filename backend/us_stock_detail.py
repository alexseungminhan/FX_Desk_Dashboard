"""Per-stock detail lookup for US tickers in the "종목 상세" popup —
sourced from Yahoo Finance (yfinance), the same provider already used
for every other US/international instrument on this board. Mirrors
stock_detail.py's Naver-backed KR version so both markets share one
popup; main.py routes to whichever module matches the symbol's suffix.

A few KR-specific stats have no US equivalent (외국인 소진율 is a
KRX-only concept) and 거래대금 isn't published directly by Yahoo for
equities, so it's approximated as price × volume rather than dropped,
since an approximate dollar-volume figure is still more useful here
than a blank.
"""
from __future__ import annotations

import logging

import yfinance as yf

import chart_range

log = logging.getLogger("us_stock_detail")


def _fmt_usd(v, decimals: int = 2) -> str:
    return f"${v:,.{decimals}f}" if v is not None else "—"


def _fmt_large_usd(v) -> str:
    if v is None:
        return "—"
    v = float(v)
    if v >= 1_000_000_000_000:
        return f"${v / 1_000_000_000_000:.2f}T"
    if v >= 1_000_000_000:
        return f"${v / 1_000_000_000:.2f}B"
    if v >= 1_000_000:
        return f"${v / 1_000_000:.2f}M"
    return f"${v:,.0f}"


def _fmt_volume(v) -> str:
    if v is None:
        return "—"
    v = float(v)
    if v >= 1_000_000_000:
        return f"{v / 1_000_000_000:.2f}B"
    if v >= 1_000_000:
        return f"{v / 1_000_000:.1f}M"
    if v >= 1_000:
        return f"{v / 1_000:.1f}K"
    return f"{v:,.0f}"


def _fmt_ratio(v) -> str:
    return f"{v:.2f}" if v is not None else "—"


def _fetch_news(symbol: str, limit: int = 3) -> list[dict]:
    try:
        out = []
        for n in yf.Ticker(symbol).get_news(count=limit):
            content = n.get("content", n)
            title = content.get("title")
            if not title:
                continue
            url = (content.get("canonicalUrl") or {}).get("url") or (content.get("clickThroughUrl") or {}).get("url")
            out.append({"headline": title, "url": url})
        return out[:limit]
    except Exception:
        log.exception("news fetch failed for %s", symbol)
        return []


def get_stock_detail(symbol: str, name: str, up: str, down: str, flat: str) -> dict | None:
    try:
        info = yf.Ticker(symbol).get_info()
    except Exception:
        log.exception("us stock detail fetch failed for %s", symbol)
        return None

    price = info.get("regularMarketPrice") or info.get("currentPrice")
    if price is None:
        return None
    prev = info.get("previousClose") or info.get("regularMarketPreviousClose")
    chg = price - prev if prev else None
    pct = (chg / prev * 100) if chg is not None and prev else None
    color = up if (pct or 0) > 0 else down if (pct or 0) < 0 else flat
    arrow = "▲" if (pct or 0) > 0 else "▼" if (pct or 0) < 0 else "–"

    volume = info.get("regularMarketVolume") or info.get("volume")
    trading_value = price * volume if volume else None

    return {
        "symbol": symbol,
        "code": symbol,
        "market": info.get("fullExchangeName") or info.get("exchange") or "US",
        "name": name or info.get("shortName") or info.get("longName") or symbol,
        "price": _fmt_usd(price),
        "prevClose": _fmt_usd(prev),
        "chg": f"{chg:+,.2f}" if chg is not None else "—",
        "pct": f"{pct:+.2f}%" if pct is not None else "—",
        "color": color,
        "arrow": arrow,
        "open": _fmt_usd(info.get("regularMarketOpen") or info.get("open")),
        "high": _fmt_usd(info.get("dayHigh") or info.get("regularMarketDayHigh")),
        "low": _fmt_usd(info.get("dayLow") or info.get("regularMarketDayLow")),
        "week52High": _fmt_usd(info.get("fiftyTwoWeekHigh")),
        "week52Low": _fmt_usd(info.get("fiftyTwoWeekLow")),
        "volume": _fmt_volume(volume),
        "tradingValue": _fmt_large_usd(trading_value),
        "marketCap": _fmt_large_usd(info.get("marketCap")),
        "per": _fmt_ratio(info.get("trailingPE")),
        "pbr": _fmt_ratio(info.get("priceToBook")),
        "foreignRate": "—",  # KRX-only concept, no US equivalent
        "chart": chart_range.get_chart("stock", symbol, "1D"),
        "news": _fetch_news(symbol),
    }
