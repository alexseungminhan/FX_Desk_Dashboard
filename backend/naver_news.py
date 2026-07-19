"""Korean-language headline news for the "주요 뉴스" panel, scraped from
네이버 증권's main news page (finance.naver.com/news/mainnews.naver).

Yahoo's news feed is sparse/English for Korean-market-relevant stories,
so headlines are sourced from Naver Finance instead — the same list a
user sees on 네이버 증권. The page paginates ~20 items at a time and
resets at local midnight (via a `date=` query param), so covering a
real 24-hour window means walking pages across today and, once those
run out, yesterday too.
"""
from __future__ import annotations

import logging
from datetime import date as date_cls, datetime, timedelta, timezone

import requests
from bs4 import BeautifulSoup

log = logging.getLogger("naver_news")

KST = timezone(timedelta(hours=9))

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/120.0 Safari/537.36"
    )
}

_URL = "https://finance.naver.com/news/mainnews.naver"
_BASE = "https://finance.naver.com"
_PAGE_SIZE = 20  # items per page when a page is full (page.naver's own page size)


def _fetch_page(day: date_cls, page: int) -> list[dict]:
    r = requests.get(
        _URL,
        params={"date": day.strftime("%Y-%m-%d"), "page": page},
        headers=_HEADERS,
        timeout=8,
    )
    r.raise_for_status()
    r.encoding = "euc-kr"
    soup = BeautifulSoup(r.text, "lxml")

    items = []
    for li in soup.select(".mainNewsList li.block1"):
        a = li.select_one("dd.articleSubject a")
        if not a:
            continue
        title = a.get_text(strip=True)
        href = a.get("href", "")
        url = href if href.startswith("http") else _BASE + href

        press_el = li.select_one("span.press")
        press = press_el.get_text(strip=True) if press_el else ""

        # articleSummary's first text node is the summary blurb; the
        # press/bar/wdate spans that follow it are siblings, not part
        # of it, so a plain get_text() would double up the press name.
        summary_el = li.select_one("dd.articleSummary")
        summary_text = summary_el.find(string=True, recursive=False) if summary_el else None
        summary = summary_text.strip() if summary_text else ""

        time_el = li.select_one("span.wdate")
        ts = None
        if time_el:
            try:
                ts = datetime.strptime(time_el.get_text(strip=True), "%Y-%m-%d %H:%M:%S")
                ts = ts.replace(tzinfo=KST)
            except ValueError:
                ts = None

        items.append({"title": title, "url": url, "press": press, "summary": summary, "time": ts})
    return items


def fetch_recent_news(hours: int = 24, max_pages: int = 12) -> list[dict]:
    """Return every headline from the last `hours` hours (walking pages
    across today and, if needed, yesterday), newest first. `max_pages`
    is a hard cap on requests so a scrape-target change or an unusually
    heavy news day can't turn this into an unbounded crawl."""
    cutoff = datetime.now(tz=KST) - timedelta(hours=hours)
    day = datetime.now(tz=KST).date()
    page = 1
    items: list[dict] = []

    for _ in range(max_pages):
        try:
            page_items = _fetch_page(day, page)
        except Exception:
            log.exception("naver mainnews fetch failed (date=%s page=%s)", day, page)
            break

        if not page_items:
            # End of this day's pages — step back to the previous day so
            # the 24h window can still reach further back if needed.
            day -= timedelta(days=1)
            page = 1
            continue

        items.extend(page_items)
        oldest = page_items[-1]["time"]
        if oldest is not None and oldest < cutoff:
            break
        if len(page_items) < _PAGE_SIZE:
            day -= timedelta(days=1)
            page = 1
        else:
            page += 1

    items = [it for it in items if it["time"] is None or it["time"] >= cutoff]
    items.sort(key=lambda x: x["time"] or datetime.min.replace(tzinfo=KST), reverse=True)
    return items
