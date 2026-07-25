"""Per-stock detail lookup for the "종목 상세" popup.

Sourced from Naver Finance's mobile API (m.stock.naver.com,
fchart.stock.naver.com) rather than Yahoo Finance. For Korean equities
Naver is strictly better here: the volume/거래대금/시가총액 figures
match what the user sees on 네이버 증권 exactly (Yahoo's KRX numbers
drift slightly), it has a real 외국인 소진율 (foreign-ownership rate —
Yahoo has no equivalent for KRX names), real per-minute intraday bars,
and real Korean-language news instead of Yahoo's sparse English
coverage of Korean stocks.
"""
from __future__ import annotations

import html
import logging
from concurrent.futures import ThreadPoolExecutor

import requests

import chart_range

log = logging.getLogger("stock_detail")

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/120.0 Safari/537.36"
    ),
    "Referer": "https://m.stock.naver.com/",
}

_BASIC_URL = "https://m.stock.naver.com/api/stock/{code}/basic"
_INTEGRATION_URL = "https://m.stock.naver.com/api/stock/{code}/integration"
_NEWS_URL = "https://m.stock.naver.com/api/news/stock/{code}?pageSize=5&page=1"


def _get_json(url: str) -> dict:
    r = requests.get(url, headers=_HEADERS, timeout=8)
    r.raise_for_status()
    return r.json()


def _num(s) -> float | None:
    if s is None:
        return None
    try:
        return float(str(s).replace(",", "").replace("+", ""))
    except ValueError:
        return None


def _fetch_news(code: str, limit: int = 3) -> list[dict]:
    try:
        groups = _get_json(_NEWS_URL.format(code=code))
        flat = [it for g in groups for it in g.get("items", [])]
        flat.sort(key=lambda it: it.get("datetime", ""), reverse=True)
        return [
            {"headline": html.unescape(it["title"]), "url": it.get("mobileNewsUrl")}
            for it in flat[:limit]
        ]
    except Exception:
        log.exception("news fetch failed for %s", code)
        return []


def get_stock_detail(symbol: str, name: str, up: str, down: str, flat: str) -> dict | None:
    code, _, suffix = symbol.partition(".")
    market = {"KS": "KOSPI", "KQ": "KOSDAQ"}.get(suffix, suffix)

    # The four upstream calls are independent — run them concurrently so
    # the popup opens in one round-trip's time instead of four.
    with ThreadPoolExecutor(max_workers=4) as pool:
        f_basic = pool.submit(_get_json, _BASIC_URL.format(code=code))
        f_integration = pool.submit(_get_json, _INTEGRATION_URL.format(code=code))
        f_chart = pool.submit(chart_range.get_chart, "stock", symbol, "1D")
        f_news = pool.submit(_fetch_news, code)
        try:
            basic = f_basic.result()
            integration = f_integration.result()
        except Exception:
            log.exception("naver stock detail fetch failed for %s", symbol)
            return None
        chart = f_chart.result()
        news = f_news.result()

    price = _num(basic.get("closePrice"))
    if price is None:
        return None
    chg = _num(basic.get("compareToPreviousClosePrice"))
    pct = _num(basic.get("fluctuationsRatio"))
    color = up if (pct or 0) > 0 else down if (pct or 0) < 0 else flat
    arrow = "▲" if (pct or 0) > 0 else "▼" if (pct or 0) < 0 else "–"

    stats = {row["code"]: row.get("value") for row in integration.get("totalInfos", [])}

    return {
        "symbol": symbol,
        "code": code,
        "market": market,
        "name": name or basic.get("stockName") or symbol,
        "price": basic.get("closePrice", "—"),
        "prevClose": stats.get("lastClosePrice", "—"),
        "chg": f"{chg:+,.0f}" if chg is not None else "—",
        "pct": f"{pct:+.2f}%" if pct is not None else "—",
        "color": color,
        "arrow": arrow,
        "open": stats.get("openPrice", "—"),
        "high": stats.get("highPrice", "—"),
        "low": stats.get("lowPrice", "—"),
        "week52High": stats.get("highPriceOf52Weeks", "—"),
        "week52Low": stats.get("lowPriceOf52Weeks", "—"),
        "volume": stats.get("accumulatedTradingVolume", "—"),
        "tradingValue": stats.get("accumulatedTradingValue", "—"),
        "marketCap": stats.get("marketValue", "—"),
        "per": stats.get("per", "—"),
        "pbr": stats.get("pbr", "—"),
        "foreignRate": stats.get("foreignRate", "—"),
        "chart": chart,
        "news": news,
    }
