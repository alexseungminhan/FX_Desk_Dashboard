"""투자자별 매매동향 (주식 수급) — 코스피 · 코스닥 · 코스피200 선물.

네이버 증권의 "투자자별 매매동향" 페이지(sise_trans_style.naver)는 껍데기고
실제 표는 iframe 안의 investorDealTrendDay.naver 가 그린다. 그래서 여기서는
부모 페이지에서 최신 영업일(bizdate)만 읽어오고, 데이터는 iframe URL에서
직접 페이지를 넘겨가며 가져온다.

일별 행만 제공되므로 1일/1주/1개월/3개월 구간 합계는 이쪽에서 직접 누적한다
(네이버의 기간 탭은 이미지 차트일 뿐 표 데이터가 아니다).

값은 네이버가 주는 원본 단위 그대로다. 페이지 하단 표기를 그대로 확인했다:
코스피·코스닥은 **억원**, 선물은 **계약**. 조 단위 환산만 표시하는 쪽에서
한다. 어느 쪽이든 개인 + 외국인 + 기관계 + 기타법인 = 0 이 성립하는
순매수 값이다.
"""
from __future__ import annotations

import logging
import re
from concurrent.futures import ThreadPoolExecutor

import requests
from bs4 import BeautifulSoup

log = logging.getLogger("kr_investor_flow")

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/120.0 Safari/537.36"
    )
}
_PARENT_URL = "https://finance.naver.com/sise/sise_trans_style.naver"
_DAY_URL = "https://finance.naver.com/sise/investorDealTrendDay.naver"
_ROWS_PER_PAGE = 10

# sosok 코드 → 표시 이름/단위. 03은 코스피200 선물이며 개별주식·통화·
# 국채 선물은 네이버가 제공하지 않는다 (KRX 전용).
MARKETS = {
    "01": {"key": "kospi", "label": "코스피", "unit": "eok"},
    "02": {"key": "kosdaq", "label": "코스닥", "unit": "eok"},
    "03": {"key": "futures", "label": "코스피200 선물", "unit": "contract"},
}

# 표의 날짜 뒤 10개 숫자 컬럼 순서 (2행짜리 헤더를 펼친 것):
# 개인 · 외국인 · 기관계 · [금융투자 보험 투신 은행 기타금융 연기금] · 기타법인
_COLUMNS = [
    "individual", "foreign", "institution",
    "financial", "insurance", "trust", "bank", "otherFinance", "pension",
    "corporate",
]

# 구간별 영업일 수. 네이버는 일별 행만 주므로 여기서 누적한다.
PERIODS = [
    {"key": "1d", "label": "1일", "days": 1},
    {"key": "1w", "label": "1주", "days": 5},
    {"key": "1m", "label": "1개월", "days": 20},
    {"key": "3m", "label": "3개월", "days": 60},
]

_MAX_DAYS = max(p["days"] for p in PERIODS)
_PAGES = -(-_MAX_DAYS // _ROWS_PER_PAGE)  # 3개월치를 덮는 최소 페이지 수


def _num(s: str) -> float | None:
    try:
        return float(s.replace(",", "").strip())
    except (ValueError, AttributeError):
        return None


def _latest_bizdate(sosok: str) -> str | None:
    """부모 페이지의 iframe src에 최신 영업일이 박혀 있다. bizdate 없이
    iframe을 직접 부르면 표가 비어 나오므로 반드시 먼저 읽어야 한다."""
    r = requests.get(_PARENT_URL, params={"sosok": sosok}, headers=_HEADERS, timeout=8)
    r.raise_for_status()
    r.encoding = "euc-kr"
    m = re.search(r"investorDealTrendDay\.naver\?bizdate=(\d{8})", r.text)
    return m.group(1) if m else None


def _fetch_page(sosok: str, bizdate: str, page: int) -> list[dict]:
    r = requests.get(
        _DAY_URL,
        params={"bizdate": bizdate, "sosok": sosok, "page": page},
        headers=_HEADERS,
        timeout=8,
    )
    r.raise_for_status()
    r.encoding = "euc-kr"
    soup = BeautifulSoup(r.text, "lxml")

    rows = []
    for tr in soup.select("table tr"):
        tds = [td.get_text(" ", strip=True) for td in tr.find_all("td")]
        # 날짜 행만 데이터 행 ("26.07.31" 형태), 나머지는 헤더·페이징이다.
        if len(tds) < len(_COLUMNS) + 1 or not re.fullmatch(r"\d{2}\.\d{2}\.\d{2}", tds[0]):
            continue
        values = [_num(v) for v in tds[1:len(_COLUMNS) + 1]]
        if any(v is None for v in values):
            continue
        rows.append({"date": tds[0], **dict(zip(_COLUMNS, values))})
    return rows


def _fetch_daily(sosok: str, bizdate: str) -> list[dict]:
    """3개월을 덮는 일별 행을 최신순으로. 페이지는 서로 독립이라 동시에 받는다."""
    with ThreadPoolExecutor(max_workers=_PAGES) as pool:
        pages = list(pool.map(lambda p: _fetch_page(sosok, bizdate, p), range(1, _PAGES + 1)))
    rows: list[dict] = []
    for page_rows in pages:
        rows.extend(page_rows)
    return rows


def _sum_over(rows: list[dict], days: int) -> dict:
    window = rows[:days]
    return {c: sum(r[c] for r in window) for c in _COLUMNS}


def fetch_investor_flow() -> dict:
    """시장별 · 구간별 투자자 순매수 합계.

    반환: {kospi: {label, unit, asOf, periods: {1d: {individual, ...}, ...},
                   daily: [...]}, kosdaq: {...}, futures: {...}}
    한 시장이 실패해도 나머지는 살린다 — 세 시장은 서로 독립된 요청이다.
    """
    def one(sosok: str) -> tuple[str, dict] | None:
        meta = MARKETS[sosok]
        try:
            bizdate = _latest_bizdate(sosok)
            if not bizdate:
                log.warning("bizdate not found for sosok=%s — page layout changed?", sosok)
                return None
            rows = _fetch_daily(sosok, bizdate)
        except Exception:
            log.exception("investor flow fetch failed (sosok=%s)", sosok)
            return None
        if not rows:
            return None
        return meta["key"], {
            "label": meta["label"],
            "unit": meta["unit"],
            "asOf": rows[0]["date"],
            "periods": {p["key"]: _sum_over(rows, p["days"]) for p in PERIODS},
            # 실제로 받아온 영업일 수 — 60일에 못 미치면 3개월 합계가
            # 그만큼 짧다는 뜻이라 화면에서 구분할 수 있게 같이 내린다.
            "days": len(rows),
        }

    with ThreadPoolExecutor(max_workers=len(MARKETS)) as pool:
        results = pool.map(one, MARKETS)
    return {key: data for r in results if r for key, data in [r]}
