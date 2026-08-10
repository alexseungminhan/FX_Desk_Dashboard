"""국내 지수 실시간 시세 — 코스피 / 코스닥 / 코스피200.

Yahoo 의 KRX 지수(^KS11 · ^KQ11 · ^KS200)는 quote 응답이 스스로 밝히듯
20분 지연된다 (exchangeDataDelayedBy=20). 장중 내내 상단 티커와 지수
패널이 20분 전 값을 들고 있어서, 같은 행을 눌러 뜨는 팝업(네이버 실시간)
과 눈에 띄게 어긋났다. 그 세 종목만 여기서 네이버 지수 API 로 받아
poll_prices 가 Yahoo 값 위에 덮어쓴다 — 팝업이 이미 쓰는 엔드포인트라
(indicator_detail.get_index_detail) 두 소스가 하나로 모인다.

네이버는 등락폭·등락률을 부호 없는 크기로 주고 방향은
compareToPreviousPrice.code 에 따로 싣는다 (2=상승, 5=하락). 전일 종가는
현재가에서 부호 붙인 등락폭을 뺀 값 — 스냅샷의 % 는 전부 prev_close 로
계산되므로 여기서 같이 맞춰 돌려준다.
"""
from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor

import requests

log = logging.getLogger("kr_index")

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/120.0 Safari/537.36"
    ),
    "Referer": "https://m.stock.naver.com/",
}
_BASIC_URL = "https://m.stock.naver.com/api/index/{code}/basic"

# Yahoo 심볼 -> 네이버 지수 코드. indicator_detail._NAVER_INDEX_CODE 와
# 같은 짝이다 — 팝업과 보드가 같은 지수를 같은 곳에서 읽는다.
NAVER_INDEX_CODE = {"^KS11": "KOSPI", "^KQ11": "KOSDAQ", "^KS200": "KPI200"}

_FALLING = "5"  # compareToPreviousPrice.code


def _num(s) -> float | None:
    try:
        return float(str(s).replace(",", "").replace("+", "").strip())
    except (ValueError, AttributeError, TypeError):
        return None


def _fetch_one(symbol: str, code: str) -> tuple[str, float, float | None] | None:
    r = requests.get(_BASIC_URL.format(code=code), headers=_HEADERS, timeout=8)
    r.raise_for_status()
    basic = r.json()

    price = _num(basic.get("closePrice"))
    if price is None:
        return None
    chg = _num(basic.get("compareToPreviousClosePrice"))
    if chg is not None and basic.get("compareToPreviousPrice", {}).get("code") == _FALLING:
        chg = -abs(chg)
    return symbol, price, (price - chg if chg is not None else None)


def fetch_prices() -> dict[str, tuple[float, float | None]]:
    """{Yahoo 심볼: (현재가, 전일 종가)}. 실패한 지수는 결과에서 빠지고
    (부분 성공은 그대로 살린다) 호출부는 그 자리만 Yahoo 값을 유지한다."""
    out: dict[str, tuple[float, float | None]] = {}
    with ThreadPoolExecutor(max_workers=len(NAVER_INDEX_CODE)) as pool:
        futures = [
            pool.submit(_fetch_one, symbol, code)
            for symbol, code in NAVER_INDEX_CODE.items()
        ]
        for f in futures:
            try:
                row = f.result()
            except Exception:
                log.exception("naver index quote failed")
                continue
            if row:
                out[row[0]] = (row[1], row[2])
    return out
