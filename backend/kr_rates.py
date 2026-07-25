"""국내 시장금리 — scraped from Naver Finance's market-index page
(finance.naver.com/marketindex), the same 국내시장금리 table a user sees
there: CD(91일), 콜금리, 국고채(3년), 회사채(3년), COFIX 잔액/신규취급액.

Yahoo Finance has no Korean money-market/bond fixings at all, so Naver
is the only free real source here. Fixings update once a day, so this
is polled on a slow interval and each rate's daily history (for the
detail popup chart) comes from the 일별 시세 pages
(marketindex/interestDailyQuote.naver), fetched on demand.
"""
from __future__ import annotations

import logging
import re
from concurrent.futures import ThreadPoolExecutor

import requests
from bs4 import BeautifulSoup

log = logging.getLogger("kr_rates")

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/120.0 Safari/537.36"
    )
}
_MAIN_URL = "https://finance.naver.com/marketindex/"
_DAILY_URL = "https://finance.naver.com/marketindex/interestDailyQuote.naver"
_ROWS_PER_PAGE = 10

# Display names for each Naver marketindexCd (the scraped names carry
# inconsistent spacing like "콜 금리").
RATE_NAMES = {
    "IRR_CD91": "CD금리 (91일)",
    "IRR_CALL": "콜금리",
    "IRR_GOVT03Y": "국고채 (3년)",
    "IRR_CORP03Y": "회사채 (3년)",
    "IRR_COFIXBAL": "COFIX 잔액",
    "IRR_COFIXNEW": "COFIX 신규취급액",
}


def _num(s: str) -> float | None:
    try:
        return float(s.replace(",", "").strip())
    except (ValueError, AttributeError):
        return None


def fetch_kr_rates() -> list[dict]:
    """Return [{code, name, value, change}] for every rate in the
    국내시장금리 table. `change` is signed in %-points (the table shows
    the magnitude; the row's up/down class carries the sign)."""
    r = requests.get(_MAIN_URL, headers=_HEADERS, timeout=8)
    r.raise_for_status()
    r.encoding = "euc-kr"
    soup = BeautifulSoup(r.text, "lxml")

    heading = soup.find("h3", class_="h_interest")
    table = heading.find_next("table") if heading else None
    if table is None:
        log.warning("국내시장금리 table not found — page layout changed?")
        return []

    out = []
    for tr in table.select("tbody tr"):
        a = tr.select_one("th a[href*='marketindexCd=']")
        tds = tr.find_all("td")
        if not a or len(tds) < 2:
            continue
        m = re.search(r"marketindexCd=([A-Z0-9_]+)", a["href"])
        if not m:
            continue
        code = m.group(1)
        value = _num(tds[0].get_text())
        change = _num(tds[1].get_text()) or 0.0
        cls = tr.get("class") or []
        if "down" in cls:
            change = -abs(change)
        elif "up" not in cls:
            change = 0.0
        if value is None:
            continue
        out.append({
            "code": code,
            "name": RATE_NAMES.get(code, a.get_text(strip=True)),
            "value": value,
            "change": change,
        })
    return out


def _fetch_daily_page(code: str, page: int) -> list[dict]:
    r = requests.get(
        _DAILY_URL,
        params={"marketindexCd": code, "page": page},
        headers=_HEADERS,
        timeout=8,
    )
    r.raise_for_status()
    r.encoding = "euc-kr"
    soup = BeautifulSoup(r.text, "lxml")
    rows = []
    for tr in soup.select("table.tbl_exchange tbody tr"):
        date_td = tr.select_one("td.date")
        num_td = tr.select_one("td.num")
        if not date_td or not num_td:
            continue
        value = _num(num_td.get_text())
        if value is None:
            continue
        rows.append({"date": date_td.get_text(strip=True), "value": value})
    return rows


def fetch_rate_history(code: str, count: int) -> list[dict]:
    """Return the last `count` daily fixings for a rate, oldest first,
    as [{date: "YYYY.MM.DD", value: float}]. Pages hold 10 rows each and
    are fetched concurrently — a 1Y window (~25 pages) stays fast."""
    pages = max(1, -(-count // _ROWS_PER_PAGE))
    rows: list[dict] = []
    with ThreadPoolExecutor(max_workers=8) as pool:
        for page_rows in pool.map(lambda p: _fetch_daily_page(code, p), range(1, pages + 1)):
            if not page_rows:
                break
            rows.extend(page_rows)
    rows = rows[:count]
    rows.reverse()  # newest-first on the site -> oldest-first for charting
    return rows
