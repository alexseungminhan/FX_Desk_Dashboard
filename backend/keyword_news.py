"""키워드 뉴스 — 외화채권 / M&A / 블록딜 / 배당. 데스크가 따로 보는 주제만
모아주는 패널용으로, 네이버 뉴스 검색(search.naver.com)을 주제별로 훑는다.

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

import logging
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone

import requests
from bs4 import BeautifulSoup

log = logging.getLogger("keyword_news")

KST = timezone(timedelta(hours=9))

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


def _search(query: str) -> list[dict]:
    """한 키워드의 앞 _PAGES_PER_QUERY 페이지. 페이지 요청은 스로틀을 함께
    쓰므로 워커를 늘려도 실제 발사 간격은 _MIN_INTERVAL 로 유지된다."""
    starts = [1 + i * _PER_PAGE for i in range(_PAGES_PER_QUERY)]

    def one(start: int) -> list[dict]:
        try:
            return _fetch_page(query, start)
        except Exception:
            log.warning("news search failed (query=%s start=%s)", query, start)
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
