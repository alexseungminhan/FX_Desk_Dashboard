"""한국자금중개(KMB, kmbco.com) 고시 데이터 클라이언트.

서울외국환중개(smbs.py)와 함께 원화 자금·스왑 시장의 양대 중개사다.
같은 상품을 두 회사가 따로 고시하므로 나란히 보면 호가 폭이 비교되고,
Mid 는 서로 검증이 된다 — 실제로 IRS/CRS Mid 가 SMBS 고시와 bp 단위까지
일치하는 것을 확인했다.

SMBS 와 달리 보호장치가 전혀 없고 HTTPS 도 정상이다. 데이터는 평범한
HTML 표라 BeautifulSoup 으로 읽는다.

  swap_rate.do            FX 스왑포인트 1M/2M/3M/6M/1Y (Bid/Offer, 단위 전)
  deri_rate.do?d_type=IRS 이자율스왑 1Y~10Y 7개 만기 (Bid/Offer, %)
  deri_rate.do?d_type=CRS 통화스왑   1Y~10Y 7개 만기 (Bid/Offer, %)

표 구조는 셋 다 같다: 첫 행이 만기 헤더, BID 행, OFFER 행.
"""
from __future__ import annotations

import logging
import threading

import requests
from bs4 import BeautifulSoup

log = logging.getLogger("kmb")

_BASE = "https://www.kmbco.com"
_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "ko-KR,ko;q=0.9",
}

_lock = threading.Lock()
_session: requests.Session | None = None


def _get_session() -> requests.Session:
    global _session
    with _lock:
        if _session is None:
            s = requests.Session()
            s.headers.update(_HEADERS)
            _session = s
        return _session


def _num(v: str) -> float | None:
    v = v.replace(",", "").strip()
    if not v or v == "-":
        return None
    try:
        return float(v)
    except ValueError:
        return None


def _bid_offer_table(path: str, params: dict | None = None) -> tuple[dict[str, dict], str]:
    """구분/BID/OFFER 3행짜리 표 → {만기: {bid, offer, mid}} 와 기준일.

    기준일은 페이지의 날짜 input(d_date_sh 등) 값에서 읽는다."""
    r = _get_session().get(f"{_BASE}{path}", params=params or {}, timeout=15)
    r.raise_for_status()
    soup = BeautifulSoup(r.text, "lxml")

    as_of = ""
    date_input = soup.select_one('input[name$="date_sh"], input[name="d_date_sh"]')
    if date_input and date_input.get("value"):
        as_of = date_input["value"]

    for table in soup.select("table"):
        rows = [
            [c.get_text(" ", strip=True) for c in tr.find_all(["th", "td"])]
            for tr in table.select("tr")
        ]
        rows = [r_ for r_ in rows if any(r_)]
        if len(rows) < 3 or rows[0][0] != "구분":
            continue
        tenors = rows[0][1:]
        by_label = {r_[0].upper(): r_[1:] for r_ in rows[1:]}
        bids, offers = by_label.get("BID", []), by_label.get("OFFER", [])

        out: dict[str, dict] = {}
        for i, tenor in enumerate(tenors):
            bid = _num(bids[i]) if i < len(bids) else None
            offer = _num(offers[i]) if i < len(offers) else None
            if bid is None and offer is None:
                continue
            mid = None
            if bid is not None and offer is not None:
                mid = (bid + offer) / 2
            out[tenor] = {"bid": bid, "offer": offer, "mid": mid if mid is not None else (bid if bid is not None else offer)}
        if out:
            return out, as_of

    return {}, as_of


def fetch_swap_points() -> tuple[dict[str, dict], str]:
    """FX 스왑포인트 {1M: {bid, offer, mid}, ...} (단위 전)."""
    return _bid_offer_table("/kor/rate/swap_rate.do")


def fetch_swap_curve(kind: str) -> tuple[dict[str, dict], str]:
    """IRS 또는 CRS 커브 {1Y: {bid, offer, mid}, ...} (%). kind: 'IRS'|'CRS'."""
    return _bid_offer_table("/kor/rate/deri_rate.do", {"d_type": kind})
