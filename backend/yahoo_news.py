"""English-language market headlines from Yahoo Finance (yfinance),
merged with 네이버 증권's Korean headlines (see naver_news.py) for the
"주요 뉴스" panel — the board shows both a domestic and an
international news feed side by side rather than picking just one.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

import yfinance as yf

log = logging.getLogger("yahoo_news")

# A handful of representative tickers/indices whose news feed is broad
# enough to stand in for general market headlines.
NEWS_SOURCES = ["^GSPC", "^KS11", "GC=F", "CL=F", "USDKRW=X"]

_FETCH_COUNT = 50  # per symbol, before the 24h cutoff trims it down


def fetch_recent_news(hours: int = 24) -> list[dict]:
    """Return every headline from the last `hours` hours as
    [{title, url, time (aware datetime, UTC), source, summary}], newest first."""
    cutoff = datetime.now(tz=timezone.utc) - timedelta(hours=hours)
    items: list[dict] = []
    seen_titles: set[str] = set()
    for sym in NEWS_SOURCES:
        try:
            for n in yf.Ticker(sym).get_news(count=_FETCH_COUNT):
                content = n.get("content", n)
                title = content.get("title") or n.get("title")
                if not title or title in seen_titles:
                    continue
                pub = content.get("pubDate") or content.get("displayTime")
                ts = None
                if pub:
                    try:
                        ts = datetime.fromisoformat(pub.replace("Z", "+00:00"))
                    except Exception:
                        ts = None
                if ts is not None and ts < cutoff:
                    continue
                seen_titles.add(title)
                source = (content.get("provider") or {}).get("displayName") or sym
                items.append({
                    "title": title,
                    "url": (content.get("canonicalUrl") or {}).get("url")
                    or (content.get("clickThroughUrl") or {}).get("url"),
                    "time": ts,
                    "source": source,
                    "summary": content.get("summary") or "",
                })
        except Exception:
            log.exception("yahoo news fetch failed for %s", sym)
            continue

    items.sort(key=lambda x: x["time"] or datetime.min.replace(tzinfo=timezone.utc), reverse=True)
    return items
