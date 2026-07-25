"""환율 주요뉴스 — the exact headline list shown on Naver Finance's
market-index page (finance.naver.com/marketindex). Scraped from the
"더보기" target page (news_list.naver, section 429 = 환율) instead of
the widget itself because the widget truncates titles to ~20 chars.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

import requests
from bs4 import BeautifulSoup

log = logging.getLogger("fx_news")

KST = timezone(timedelta(hours=9))

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/120.0 Safari/537.36"
    )
}
_URL = "https://finance.naver.com/news/news_list.naver"
_PARAMS = {"mode": "LSS3D", "section_id": "101", "section_id2": "258", "section_id3": "429"}
_BASE = "https://finance.naver.com"


def fetch_fx_news(limit: int = 10) -> list[dict]:
    """Return the latest FX headlines as
    [{title, url, press, time}], newest first."""
    r = requests.get(_URL, params=_PARAMS, headers=_HEADERS, timeout=8)
    r.raise_for_status()
    r.encoding = "euc-kr"
    soup = BeautifulSoup(r.text, "lxml")

    subjects = soup.select(".realtimeNewsList .articleSubject a")
    summaries = soup.select(".realtimeNewsList dd.articleSummary")

    items = []
    for a, summary in zip(subjects, summaries):
        title = a.get_text(strip=True)
        href = a.get("href", "")
        url = href if href.startswith("http") else _BASE + href

        press_el = summary.select_one("span.press")
        press = press_el.get_text(strip=True) if press_el else ""

        ts = None
        time_el = summary.select_one("span.wdate")
        if time_el:
            try:
                ts = datetime.strptime(time_el.get_text(strip=True), "%Y-%m-%d %H:%M")
                ts = ts.replace(tzinfo=KST)
            except ValueError:
                pass

        items.append({"title": title, "url": url, "press": press, "time": ts})
        if len(items) >= limit:
            break
    return items
