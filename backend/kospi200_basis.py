"""코스피200 현·선물 베이시스 (저평 / 고평).

네이버 증권의 코스피200 지수 페이지와 선물 페이지에서 각각 현물·선물
가격을 읽어와 베이시스와 괴리율을 계산한다. 둘 다 같은 sise_index.naver
페이지라 장 상태(장중/장마감)와 지연 표기도 같은 자리에서 나온다.

  베이시스   = 선물 - 현물
  이론가     = 현물 × (1 + (r - d) × 잔존일수/365)
  이론베이시스 = 이론가 - 현물
  괴리율     = (선물 - 이론가) / 이론가 × 100      → +면 고평, -면 저평

r 은 단기 조달금리로 CD(91일)를 쓴다 (kr_rates.py 가 이미 받아오는 값을
넘겨받는다). d 는 코스피200 배당수익률인데 무료로 실시간 제공하는 곳이
없어 상수 가정이며, 그래서 이론가·괴리율은 "가정에 기반한 참고치"로
표시된다. 베이시스 자체는 가정이 안 들어간 실측값이다.
"""
from __future__ import annotations

import calendar
import logging
import re
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta, timezone

import requests
from bs4 import BeautifulSoup

log = logging.getLogger("kospi200_basis")

KST = timezone(timedelta(hours=9))

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/120.0 Safari/537.36"
    )
}
_URL = "https://finance.naver.com/sise/sise_index.naver"

# 코스피200 배당수익률 가정치(연 %). 실시간 무료 소스가 없어 고정값이며,
# 이 값이 바뀌면 이론베이시스·괴리율만 움직이고 실측 베이시스는 그대로다.
DIVIDEND_YIELD = 1.8

# CD(91일)를 못 받아온 경우에만 쓰는 조달금리 대체값(연 %).
_FALLBACK_RATE = 3.0


def _num(s: str | None) -> float | None:
    if not s:
        return None
    try:
        return float(s.replace(",", "").strip())
    except ValueError:
        return None


def _fetch_quote(code: str) -> dict | None:
    r = requests.get(_URL, params={"code": code}, headers=_HEADERS, timeout=8)
    r.raise_for_status()
    r.encoding = "euc-kr"
    soup = BeautifulSoup(r.text, "lxml")

    price = _num(soup.select_one("#now_value").get_text(strip=True) if soup.select_one("#now_value") else None)
    if price is None:
        return None

    # "2026.07.31 장마감 | 20분 지연제공" 같은 표기가 페이지 상단에 있다.
    stamp_el = soup.select_one("#time")
    stamp = re.sub(r"\s+", " ", stamp_el.get_text(" ", strip=True)) if stamp_el else ""

    # 선물 페이지는 종목명에 결제월이 붙어 나온다: "선물(2609)".
    contract = None
    m = re.search(r"선물\s*\((\d{4})\)", soup.get_text(" ", strip=True))
    if m:
        contract = m.group(1)

    return {"price": price, "stamp": stamp, "contract": contract}


def _expiry_of(contract: str) -> date | None:
    """결제월 코드(YYMM) → 만기일. 코스피200 선물 만기는 결제월 두 번째 목요일."""
    try:
        year = 2000 + int(contract[:2])
        month = int(contract[2:])
    except (ValueError, IndexError):
        return None
    if not 1 <= month <= 12:
        return None
    thursdays = [
        d for week in calendar.monthcalendar(year, month)
        if (d := week[calendar.THURSDAY])
    ]
    return date(year, month, thursdays[1]) if len(thursdays) >= 2 else None


def fetch_basis(cd_rate: float | None = None) -> dict | None:
    """현·선물 베이시스 스냅샷. 한쪽이라도 못 받으면 None."""
    with ThreadPoolExecutor(max_workers=2) as pool:
        f_spot = pool.submit(_fetch_quote, "KPI200")
        f_fut = pool.submit(_fetch_quote, "FUT")
        try:
            spot = f_spot.result()
            fut = f_fut.result()
        except Exception:
            log.exception("kospi200 basis fetch failed")
            return None
    if not spot or not fut:
        return None

    basis = fut["price"] - spot["price"]

    expiry = _expiry_of(fut["contract"]) if fut["contract"] else None
    today = datetime.now(tz=KST).date()
    days = (expiry - today).days if expiry else None

    rate = cd_rate if cd_rate is not None else _FALLBACK_RATE
    theoretical = theo_basis = spread = None
    if days is not None and days > 0:
        theoretical = spot["price"] * (1 + (rate - DIVIDEND_YIELD) / 100 * days / 365)
        theo_basis = theoretical - spot["price"]
        spread = (fut["price"] - theoretical) / theoretical * 100

    return {
        "spot": spot["price"],
        "futures": fut["price"],
        "contract": fut["contract"],
        "expiry": expiry.isoformat() if expiry else None,
        "daysToExpiry": days,
        "basis": basis,
        # 콘탱고/백워데이션은 실측 베이시스의 부호만 보면 된다.
        "state": "콘탱고" if basis > 0 else "백워데이션" if basis < 0 else "동일",
        "theoretical": theoretical,
        "theoBasis": theo_basis,
        "spread": spread,
        "valuation": None if spread is None else ("고평" if spread > 0 else "저평" if spread < 0 else "적정"),
        "rate": rate,
        "rateIsFallback": cd_rate is None,
        "dividendYield": DIVIDEND_YIELD,
        "stamp": fut["stamp"] or spot["stamp"],
    }
