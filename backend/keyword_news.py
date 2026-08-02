"""키워드 뉴스 — 외화채권 / M&A / 블록딜 / 배당. 데스크가 따로 보는 주제만
모아주는 패널용.

**두 가지 경로가 있고 환경변수로 갈린다.**

1. NAVER_CLIENT_ID/SECRET 이 있으면 네이버 검색 OpenAPI 를 쓴다. 공식
   경로라 IP 로 막히지 않고 한 번에 100건까지 받아 호출 수도 훨씬 적다.
   클라우드(Render 등)에 올릴 거면 이쪽이 사실상 필수다 —
   search.naver.com 은 데이터센터 IP 를 차단해서 전량 403 이 난다.
2. 키가 없으면 예전처럼 search.naver.com 화면을 긁는다. 로컬에서는 잘
   되지만 언제 막혀도 이상하지 않은 경로다.

어느 쪽이든 아래 형태로 통일해 돌려준다:
    {title, url, press, when, time}

네이버 증권 메인뉴스(naver_news.py)는 시황 헤드라인이라 이런 주제가 잘
안 걸린다. 그래서 여기만 일반 뉴스 검색을 쓴다.

**관련도순(sort=0)으로 받아 네이버가 준 순서를 그대로 쓴다.** 처음엔
최신순(sort=1)에 제목 키워드 필터를 걸었는데, 네이버가 본문 매칭으로 끌어온
최신 기사를 걸러내다 보니 결과가 특정 기사 한 건으로 쏠렸다. 관련도순은
그 정렬 자체가 주제 적합성이라 필터 없이도 사람이 네이버에서 직접 검색해
보는 목록과 같아진다. 그래서 시각순 재정렬도 하지 않는다 — 재정렬하면
관련도 순서가 깨진다.

검색결과 HTML의 CSS 클래스는 난독화된 해시(fender-ui_xxxxxxxx)라 수시로
바뀐다. 그나마 안 변하는 건 헤드라인 앵커의 data-heatmap-target 속성과
sds-comps-* 시맨틱 클래스라 그쪽을 잡는다.
"""
from __future__ import annotations

import html as html_mod
import logging
import os
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from urllib.parse import urlsplit

import requests
from bs4 import BeautifulSoup

log = logging.getLogger("keyword_news")

KST = timezone(timedelta(hours=9))

# 네이버 개발자센터에서 발급받는 검색 API 키. 둘 다 있어야 API 경로를 탄다.
_OPENAPI_URL = "https://openapi.naver.com/v1/search/news.json"
_CLIENT_ID = os.environ.get("NAVER_CLIENT_ID", "").strip()
_CLIENT_SECRET = os.environ.get("NAVER_CLIENT_SECRET", "").strip()
USE_OPENAPI = bool(_CLIENT_ID and _CLIENT_SECRET)

# OpenAPI 는 한 번에 100건까지 준다 — 화면이 쓰는 45건을 한 호출로 덮는다.
_OPENAPI_DISPLAY = 100

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/120.0 Safari/537.36"
    )
}
_URL = "https://search.naver.com/search.naver"
_PER_PAGE = 10

# 네이버 검색은 짧은 시간에 몰아치면 403을 뱉는다. 그룹·키워드·페이지를
# 마음대로 병렬로 던지면 절반이 403으로 죽으므로, 모든 요청이 이 게이트를
# 통과하게 해서 최소 간격을 강제한다.
_MIN_INTERVAL = 0.6
_rate_lock = threading.Lock()
_last_request = 0.0

# 화면의 탭 하나 = 그룹 하나. 한 그룹에 검색어가 여럿이면 관련도순 결과를
# 앞에서부터 번갈아 섞어, 어느 한 검색어가 목록을 독점하지 않게 한다.
GROUPS = [
    {"key": "fxbond", "label": "외화채권", "queries": ["외화채권"]},
    {"key": "ma", "label": "M&A", "queries": ["M&A", "인수합병"]},
    {"key": "blockdeal", "label": "블록딜", "queries": ["블록딜"]},
    {"key": "dividend", "label": "배당", "queries": ["배당"]},
]

# 검색어 1건당 7페이지 = 70건. 중복 사건을 접고 나면 그룹당 50건 안팎이
# 남아, 화면의 3페이지(45건)를 채우고도 여유가 있다.
_PAGES_PER_QUERY = 7


def _parse_time(text: str) -> datetime | None:
    """네이버 검색결과의 상대·절대 시각 표기를 KST datetime 으로.
    "3시간 전" / "2일 전" / "2026.07.31." 형태가 섞여 나오고, 지면 표기
    ("A9면 2단")처럼 시각이 아예 없는 것도 있다."""
    now = datetime.now(tz=KST)
    text = text.strip()

    m = re.match(r"(\d+)\s*(분|시간|일|주|개월)\s*전", text)
    if m:
        n = int(m.group(1))
        unit = m.group(2)
        delta = {
            "분": timedelta(minutes=n), "시간": timedelta(hours=n), "일": timedelta(days=n),
            "주": timedelta(weeks=n), "개월": timedelta(days=30 * n),
        }[unit]
        return now - delta

    m = re.match(r"(\d{4})\.(\d{1,2})\.(\d{1,2})", text)
    if m:
        return datetime(int(m.group(1)), int(m.group(2)), int(m.group(3)), tzinfo=KST)
    return None


def _throttled_get(params: dict) -> requests.Response:
    global _last_request
    with _rate_lock:
        wait = _MIN_INTERVAL - (time.monotonic() - _last_request)
        if wait > 0:
            time.sleep(wait)
        _last_request = time.monotonic()
    return requests.get(_URL, params=params, headers=_HEADERS, timeout=10)


def _fetch_page(query: str, start: int) -> list[dict]:
    params = {
        "ssc": "tab.news.all", "where": "news", "query": query,
        "sort": 0,      # 관련도순 — 사람이 네이버에서 그냥 검색했을 때의 순서
        "start": start,
    }
    # 스로틀을 통과했는데도 403이면 네이버가 다른 스크래퍼(메인뉴스·환율뉴스·
    # 종목검색)까지 합친 순간 부하를 보고 막은 것이다. 점점 길게 물러섰다가
    # 두 번까지 다시 시도한다.
    r = _throttled_get(params)
    for backoff in (1.5, 4.0):
        if r.status_code != 403:
            break
        time.sleep(backoff)
        r = _throttled_get(params)
    r.raise_for_status()
    soup = BeautifulSoup(r.text, "lxml")

    out = []
    for a in soup.select('a[data-heatmap-target=".tit"]'):
        title = a.get_text(" ", strip=True).replace(" 새 창 열림", "").strip()
        url = a.get("href")
        if not title or not url:
            continue

        # 언론사·시각은 같은 아이템 박스 안의 Profile 블록에 들어있다.
        box = a
        for _ in range(6):
            box = box.parent
            if box is None:
                break
            if box.select_one('[data-sds-comp="Profile"]'):
                break
        press_el = box.select_one(".sds-comps-profile-info-title-text") if box else None
        sub_el = box.select_one(".sds-comps-profile-info-subtext") if box else None
        press = press_el.get_text(strip=True).replace("새 창 열림", "").strip() if press_el else ""
        when = sub_el.get_text(" ", strip=True) if sub_el else ""

        # when 원문("3시간 전")을 그대로 들고 간다. 상대표기를 절대시각으로
        # 바꿔 보여주면 분 단위가 조회 시각에 좌우돼 없는 정밀도가 생긴다.
        out.append({
            "title": title, "url": url, "press": press,
            "when": when, "time": _parse_time(when),
        })
    return out


def _relative_label(ts: datetime | None) -> str:
    """OpenAPI 는 정확한 발행시각을 주는데, 화면은 스크래핑 경로와 같은
    "3시간 전" 표기를 쓰므로 여기서 맞춰준다."""
    if ts is None:
        return ""
    delta = datetime.now(tz=KST) - ts
    mins = int(delta.total_seconds() // 60)
    if mins < 1:
        return "방금 전"
    if mins < 60:
        return f"{mins}분 전"
    hours = mins // 60
    if hours < 24:
        return f"{hours}시간 전"
    days = hours // 24
    if days < 7:
        return f"{days}일 전"
    if days < 30:
        return f"{days // 7}주 전"
    return ts.strftime("%Y.%m.%d.")


def _strip_tags(s: str) -> str:
    """검색어가 <b>로 감싸여 오고 엔티티도 섞여 있다."""
    return html_mod.unescape(re.sub(r"<[^>]+>", "", s or "")).strip()


def _search_openapi(query: str) -> list[dict]:
    r = requests.get(
        _OPENAPI_URL,
        params={"query": query, "display": _OPENAPI_DISPLAY, "start": 1, "sort": "sim"},
        headers={
            "X-Naver-Client-Id": _CLIENT_ID,
            "X-Naver-Client-Secret": _CLIENT_SECRET,
        },
        timeout=10,
    )
    r.raise_for_status()

    out = []
    for it in r.json().get("items", []):
        title = _strip_tags(it.get("title", ""))
        # 네이버 뉴스 링크가 있으면 그쪽, 없으면 언론사 원문.
        url = it.get("link") or it.get("originallink")
        if not title or not url:
            continue
        try:
            ts = parsedate_to_datetime(it["pubDate"]).astimezone(KST)
        except Exception:
            ts = None
        # API 는 언론사명을 안 준다. 원문 도메인이 그 자리를 대신한다.
        origin = it.get("originallink") or ""
        press = urlsplit(origin).netloc.replace("www.", "") if origin else ""
        out.append({
            "title": title, "url": url, "press": press,
            "when": _relative_label(ts), "time": ts,
        })
    return out


def _search(query: str) -> list[dict]:
    if USE_OPENAPI:
        try:
            return _search_openapi(query)
        except Exception as e:
            code = getattr(getattr(e, "response", None), "status_code", type(e).__name__)
            log.warning("naver openapi failed (query=%s %s)", query, code)
            return []
    return _search_scrape(query)


def _search_scrape(query: str) -> list[dict]:
    """한 키워드의 앞 _PAGES_PER_QUERY 페이지. 페이지 요청은 스로틀을 함께
    쓰므로 워커를 늘려도 실제 발사 간격은 _MIN_INTERVAL 로 유지된다."""
    starts = [1 + i * _PER_PAGE for i in range(_PAGES_PER_QUERY)]

    def one(start: int) -> list[dict]:
        try:
            return _fetch_page(query, start)
        except requests.HTTPError as e:
            # 상태코드를 남긴다 — 403이 계속 찍히면 네이버가 이 서버 IP를
            # 막은 것이고(클라우드에서 흔하다), 그건 스로틀로 못 푼다.
            code = e.response.status_code if e.response is not None else "?"
            log.warning("news search failed (query=%s start=%s http=%s)", query, start, code)
            return []
        except Exception as e:
            log.warning("news search failed (query=%s start=%s %s)", query, start, type(e).__name__)
            return []

    with ThreadPoolExecutor(max_workers=2) as pool:
        return [item for page in pool.map(one, starts) for item in page]


_WORD = re.compile(r"[0-9A-Za-z가-힣]{2,}")

# 같은 사건을 여러 매체가 받아쓴 기사끼리의 제목 어절 겹침 비율. 이 이상이면
# 한 건만 남긴다. 0.5는 "하나은행, 포모사본드 3억달러 발행" 계열 6~7건을
# 하나로 접으면서 다른 사건은 붙이지 않는 선.
_DUP_RATIO = 0.5


def _is_near_duplicate(title: str, kept: list[set[str]]) -> bool:
    """제목 어절 집합의 자카드 유사도로 같은 사건 기사를 걸러낸다. 관련도순
    상위가 한 사건의 받아쓰기로 도배되는 걸 막는 게 목적이다."""
    words = set(_WORD.findall(title))
    if not words:
        return False
    for prev in kept:
        overlap = len(words & prev)
        if overlap and overlap / min(len(words), len(prev)) >= _DUP_RATIO:
            return True
    kept.append(words)
    return False


def fetch_group(group: dict) -> list[dict]:
    """그룹의 검색어별 관련도순 결과를 라운드로빈으로 합친다. 정렬을 다시
    하지는 않는다 — 관련도 순서가 곧 결과이기 때문이다."""
    per_query = [_search(q) for q in group["queries"]]

    seen: set[str] = set()
    kept_titles: list[set[str]] = []
    items: list[dict] = []
    for rank in range(max((len(r) for r in per_query), default=0)):
        for query, results in zip(group["queries"], per_query):
            if rank >= len(results):
                continue
            it = results[rank]
            if it["url"] in seen or _is_near_duplicate(it["title"], kept_titles):
                continue
            seen.add(it["url"])
            items.append({**it, "hit": query})
    return items


def fetch_keyword_news() -> dict:
    """{fxbond: {label, items: [...]}, deal: {...}} — 그룹별 키워드 뉴스."""
    def one(group: dict) -> tuple[str, dict]:
        try:
            items = fetch_group(group)
        except Exception:
            log.exception("keyword news group failed (%s)", group["key"])
            items = []
        return group["key"], {"label": group["label"], "items": items}

    with ThreadPoolExecutor(max_workers=len(GROUPS)) as pool:
        return dict(pool.map(one, GROUPS))
