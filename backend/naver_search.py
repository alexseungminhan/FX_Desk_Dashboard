"""Stock-name search, backed by Naver Finance's public autocomplete
endpoint. Used to let the user find a Korean stock by name and add it
to the watchlist — no manual ticker-code lookup required.
"""
from __future__ import annotations

import logging

import requests

log = logging.getLogger("naver_search")

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/120.0 Safari/537.36"
    )
}
_URL = "https://ac.stock.naver.com/ac"
_TYPE_TO_YAHOO_SUFFIX = {"KOSPI": ".KS", "KOSDAQ": ".KQ"}


def search_stocks(query: str, limit: int = 8) -> list[dict]:
    """Return [{symbol, name, market}] for Korean stocks matching `query`.
    `symbol` is already suffixed for direct use with yfinance
    (e.g. "005930.KS")."""
    query = query.strip()
    if not query:
        return []
    try:
        r = requests.get(
            _URL,
            params={"q": query, "target": "stock,index"},
            headers=_HEADERS,
            timeout=6,
        )
        r.raise_for_status()
        data = r.json()
    except Exception:
        log.exception("naver autocomplete failed for %r", query)
        return []

    out = []
    for item in data.get("items", []):
        if item.get("category") != "stock":
            continue
        market = item.get("typeCode")
        suffix = _TYPE_TO_YAHOO_SUFFIX.get(market)
        if not suffix:
            continue
        out.append({
            "symbol": item["code"] + suffix,
            "name": item["name"],
            "market": market,
        })
        if len(out) >= limit:
            break
    return out
