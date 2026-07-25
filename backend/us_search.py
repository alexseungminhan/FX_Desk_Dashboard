"""US stock search for the header search bar — Yahoo Finance's
autocomplete via yfinance (which handles the cookie/crumb dance that a
bare HTTP request gets 429'd on).

Complements naver_search.py: that module covers KR listings (한글),
this one matches US tickers and English company names. Results use the
same {symbol, name, market} shape; a bare ticker like "NVDA" is what
main.py's /api/stock route already treats as a US symbol.
"""
from __future__ import annotations

import logging

import yfinance as yf

log = logging.getLogger("us_search")


def _has_ascii_letter(s: str) -> bool:
    return any(c.isascii() and c.isalpha() for c in s)


def search_stocks(query: str, limit: int = 6) -> list[dict]:
    """Return [{symbol, name, market}] for US-listed stocks matching
    `query` (ticker or English name). 한글-only queries are skipped —
    Yahoo's autocomplete doesn't match Korean names."""
    query = query.strip()
    if not query or not _has_ascii_letter(query):
        return []
    try:
        quotes = yf.Search(query, max_results=limit * 3, news_count=0).quotes
    except Exception:
        log.exception("yahoo autocomplete failed for %r", query)
        return []

    out = []
    for q in quotes:
        if q.get("quoteType") != "EQUITY":
            continue
        symbol = q.get("symbol") or ""
        # Suffixed symbols (NVDA.TO, NVD.DE, …) are non-US listings; the
        # popup's US detail path formats everything as USD, so keep only
        # bare US tickers.
        if not symbol or "." in symbol:
            continue
        name = q.get("shortname") or q.get("longname") or symbol
        out.append({
            "symbol": symbol,
            "name": name,
            "market": q.get("exchDisp") or "US",
        })
        if len(out) >= limit:
            break
    return out
