"""Stock-name search for the header search bar.

Two sources, merged:

1. Naver Finance's public autocomplete (ac.stock.naver.com) — good
   ranking and covers ETFs, but it only matches complete tokens:
   "하이닉스" finds SK하이닉스, "하이닉" finds nothing.
2. A local index of every KOSPI/KOSDAQ listing (scraped from Naver's
   market-cap listing pages, refreshed every few hours in a background
   thread) — enables real substring matching so partial input like
   "하이닉" still finds SK하이닉스.
"""
from __future__ import annotations

import logging
import threading
import time

import requests

log = logging.getLogger("naver_search")

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/120.0 Safari/537.36"
    ),
    "Referer": "https://m.stock.naver.com/",
}
_AC_URL = "https://ac.stock.naver.com/ac"
_LIST_URL = "https://m.stock.naver.com/api/stocks/marketValue/{market}"
_TYPE_TO_YAHOO_SUFFIX = {"KOSPI": ".KS", "KOSDAQ": ".KQ"}

# -- full-listing index (for substring matches) ------------------------

_INDEX_TTL = 6 * 3600
_index: list[dict] = []
_index_built_at = 0.0
_index_building = False
_index_lock = threading.Lock()


def _build_index() -> None:
    global _index, _index_built_at, _index_building
    out: list[dict] = []
    try:
        for market, suffix in (("KOSPI", ".KS"), ("KOSDAQ", ".KQ")):
            for page in range(1, 41):  # hard cap: 40 pages x 100 per market
                r = requests.get(
                    _LIST_URL.format(market=market),
                    params={"page": page, "pageSize": 100},
                    headers=_HEADERS,
                    timeout=8,
                )
                r.raise_for_status()
                stocks = r.json().get("stocks", [])
                if not stocks:
                    break
                for s in stocks:
                    # Listing pages include ETF/ETN rows; the popup's
                    # detail APIs only make sense for equities.
                    if s.get("stockEndType") != "stock":
                        continue
                    code, name = s.get("itemCode"), s.get("stockName")
                    if code and name:
                        out.append({"symbol": code + suffix, "name": name, "market": market})
                if len(stocks) < 100:
                    break
        if out:
            with _index_lock:
                _index = out
                _index_built_at = time.time()
            log.info("stock name index built: %d listings", len(out))
        else:
            log.warning("stock name index build returned nothing")
    except Exception:
        log.exception("stock name index build failed")
    finally:
        _index_building = False


def refresh_index() -> None:
    """(Re)build the listing index in a background thread if it's
    missing or stale. Cheap to call — no-ops while fresh/building."""
    global _index_building
    with _index_lock:
        if _index_building or time.time() - _index_built_at < _INDEX_TTL:
            return
        _index_building = True
    threading.Thread(target=_build_index, daemon=True).start()


def _index_snapshot() -> list[dict]:
    with _index_lock:
        return list(_index)


# -- search ------------------------------------------------------------

def _ac_results(query: str, limit: int) -> list[dict]:
    try:
        r = requests.get(
            _AC_URL,
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


def search_stocks(query: str, limit: int = 8) -> list[dict]:
    """Return [{symbol, name, market}] for Korean stocks matching
    `query`. `symbol` is already suffixed for yfinance ("005930.KS")."""
    query = query.strip()
    if not query:
        return []
    refresh_index()

    out = _ac_results(query, limit)

    # Fill remaining slots with substring matches from the full listing
    # — this is what makes partial input like "하이닉" work.
    q = query.lower()
    if len(out) < limit:
        seen = {r["symbol"] for r in out}
        local = [
            s for s in _index_snapshot()
            if q in s["name"].lower() and s["symbol"] not in seen
        ]
        # Earlier match position first, then shorter (more exact) names.
        local.sort(key=lambda s: (s["name"].lower().find(q), len(s["name"])))
        out.extend(local[: limit - len(out)])
    return out
