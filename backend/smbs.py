"""서울외국환중개(SMBS) 고시 데이터 공용 클라이언트.

원화 FX 스왑포인트·IRS·CRS 의 원천이다. investing.com 의 USD/KRW forward
값이 여기 숫자와 소수점까지 같은 것을 확인했다 — 즉 재배포본이 아니라
원본을 직접 받는 셈이다.

**HTTPS 로는 못 붙는다.** www.smbs.biz 의 인증서가 m.smbs.biz 용이라
호스트명이 안 맞고, 검증을 끄면 403 이 온다. http:// 로 브라우저와 같은
헤더를 붙이면 정상 응답한다. 평문이지만 공개 고시 데이터라 실익이 없는
정보이고, 다른 경로가 없다.

차트 데이터는 FusionCharts XML 로 온다:

    <categories><category label='26.07.31'/>...</categories>
    <dataset seriesName='Bid'><set value='-145'/>...</dataset>
    <dataset seriesName='Offer'><set value='55'/>...</dataset>

날짜축(categories)과 시리즈(dataset)가 분리돼 있어 인덱스로 맞춰야 한다.
"""
from __future__ import annotations

import logging
import re
import threading

import requests
import urllib3

log = logging.getLogger("smbs")

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

_BASE = "http://www.smbs.biz"
_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "ko-KR,ko;q=0.9,en-US;q=0.8",
    "Upgrade-Insecure-Requests": "1",
}

_CATEGORY = re.compile(r"<category label='([^']*)'")
_DATASET = re.compile(r"<dataset([^>]*)>(.*?)</dataset>", re.S)
_ATTR = re.compile(r"(\w+)='([^']*)'")
_VALUE = re.compile(r"value='([^']*)'")

_lock = threading.Lock()
_session: requests.Session | None = None


def _get_session() -> requests.Session:
    global _session
    with _lock:
        if _session is None:
            s = requests.Session()
            s.headers.update(_HEADERS)
            s.verify = False   # 위 docstring 참고 — 인증서 호스트명 불일치
            _session = s
        return _session


def fetch_series(endpoint: str, arr_value: str, referer_page: str) -> dict[str, list[tuple[str, float | None]]]:
    """FusionCharts XML 하나를 받아 {시리즈명: [(날짜, 값), ...]} 로 편다.

    값이 비어 오는 날은 None 으로 남긴다 — 0 으로 바꾸면 고시가 없는 날이
    진짜 0 인 것처럼 보인다."""
    sess = _get_session()
    r = sess.get(
        f"{_BASE}/Exchange/{endpoint}",
        params={"arr_value": arr_value},
        headers={"Referer": f"{_BASE}/Exchange/{referer_page}"},
        timeout=15,
    )
    r.raise_for_status()
    text = r.content.decode("euc-kr", "replace")

    dates = _CATEGORY.findall(text)
    out: dict[str, list[tuple[str, float | None]]] = {}
    for m in _DATASET.finditer(text):
        name = dict(_ATTR.findall(m.group(1))).get("seriesName", "")
        values = _VALUE.findall(m.group(2))
        series = []
        for i, date in enumerate(dates):
            raw = values[i] if i < len(values) else ""
            try:
                series.append((date, float(raw)))
            except ValueError:
                series.append((date, None))
        out[name] = series
    return out


def last_valid(series: list[tuple[str, float | None]]) -> tuple[str, float] | None:
    """가장 최근의 값 있는 날. 주말·공휴일은 빈 값으로 온다."""
    for date, value in reversed(series):
        if value is not None:
            return date, value
    return None
