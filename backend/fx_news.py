"""환율 주요뉴스 — the exact headline list shown on Naver Finance's
market-index page (finance.naver.com/marketindex). Scraped from the
"더보기" target page (news_list.naver, section 429 = 환율) instead of
the widget itself because the widget truncates titles to ~20 chars.

이 섹션은 하루에 20건씩만 싣고 페이지가 아니라 `date=` 로 나뉜다
(page=2 는 빈 목록이 온다). 화면의 3페이지를 채우려면 날짜를 하루씩
거슬러 올라가며 모아야 한다 — naver_news.py 가 메인뉴스에서 쓰는 것과
같은 방식이다.
"""
from __future__ import annotations

import logging
import re
from datetime import date as date_cls, datetime, timedelta, timezone

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


def _fetch_day(day: date_cls | None) -> list[dict]:
    params = dict(_PARAMS)
    if day is not None:
        params["date"] = day.strftime("%Y%m%d")
    r = requests.get(_URL, params=params, headers=_HEADERS, timeout=8)
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
    return items


def _article_key(url: str) -> str:
    """같은 기사가 어제 목록과 오늘 목록에 걸치면 URL 끝의 `date=` 만
    달라진 채 두 번 잡힌다. 기사 식별자만 뽑아 중복을 없앤다."""
    ids = re.findall(r"(?:article_id|office_id)=(\d+)", url)
    return "|".join(ids) if ids else url


def fetch_fx_news(limit: int = 45, max_days: int = 7) -> list[dict]:
    """Return the latest FX headlines as [{title, url, press, time}],
    newest first. 오늘부터 하루씩 거슬러 올라가며 `limit` 건을 채운다.
    `max_days` 는 스크래핑 대상이 바뀌었을 때 무한정 과거로 파고드는 걸
    막는 상한이다."""
    day = datetime.now(tz=KST).date()
    seen: set[str] = set()
    # 같은 기사가 기사 ID만 다른 채 두 번 실리기도 한다(연합뉴스 재송고 등).
    # 데스크가 읽는 목록에 같은 제목이 두 줄로 뜨는 건 잡음이라 제목으로도 접는다.
    seen_titles: set[str] = set()
    items: list[dict] = []

    for i in range(max_days):
        try:
            # 첫 요청만 date 없이 — 섹션의 기본 목록이 곧 최신이다.
            day_items = _fetch_day(None if i == 0 else day)
        except Exception:
            log.exception("fx news fetch failed (date=%s)", day)
            break

        for it in day_items:
            key = _article_key(it["url"])
            title = it["title"].strip()
            if key in seen or title in seen_titles:
                continue
            seen.add(key)
            seen_titles.add(title)
            items.append(it)

        if len(items) >= limit:
            break
        day -= timedelta(days=1)

    items.sort(key=lambda x: x["time"] or datetime.min.replace(tzinfo=KST), reverse=True)
    return items[:limit]
