"""원화 스왑 — FX 스왑포인트(연율 환산)와 IRS·CRS.

원화 자금·스왑 시장의 양대 중개사 고시를 나란히 받는다:

  SMBS (서울외국환중개, smbs.py)  스왑포인트 1M/3M/6M/1Y · IRS/CRS 1Y/3Y/5Y Mid
  KMB  (한국자금중개, kmb.py)     스왑포인트 1M/3M/6M/1Y · IRS/CRS 1Y~10Y Bid/Offer

같은 상품을 두 회사가 따로 고시하므로 호가 폭이 비교되고 Mid 는 서로
검증이 된다 — IRS/CRS Mid 는 두 회사가 bp 단위까지 일치함을 확인했다.
스왑포인트 Mid 도 거의 같으나(1M -45 vs -40) 호가 폭은 SMBS 가 두 배쯤
넓다.

**연율 환산** — 스왑포인트 고시 단위는 전(錢, 0.01원):

    연율(%) = 스왑포인트(원) / 현물환율 × 365/일수 × 100

현물 USD/KRW 는 보드가 이미 야후에서 받고 있어 그 값을 넘겨받고, 각
중개사 Mid 로 각각 환산한다.

**IRS·CRS 커브** 는 만기가 많은 KMB(7개)를 기본으로 쓰고, KMB 가 죽으면
SMBS(3개)로 내려간다. CRS-IRS 가 곧 통화베이시스다.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

import kmb
import smbs

log = logging.getLogger("krw_swap")

KST = timezone(timedelta(hours=9))

# (표시명, SMBS arr_value 접두, 연율 환산 일수). KMB 는 표시명으로 조회.
# 고시는 ON/TN/1W/2M/9M 도 있지만 데스크가 보는 건 이 네 구간이라
# 표를 여기에 맞춘다 — SMBS 요청도 만기당 하나라 그만큼 준다.
TENORS = [
    ("1M", "1M", 30),
    ("3M", "3M", 90),
    ("6M", "6M", 180),
    ("1Y", "1Y", 365),
]

SWAP_TERMS = ["1Y", "2Y", "3Y", "4Y", "5Y", "7Y", "10Y"]
_SMBS_TERMS = ["1Y", "3Y", "5Y"]

_LOOKBACK_DAYS = 14   # 연휴를 넘겨 최근 고시일을 잡을 만큼


def _range() -> str:
    end = datetime.now(tz=KST).date()
    start = end - timedelta(days=_LOOKBACK_DAYS)
    return f"{start.isoformat()}_{end.isoformat()}"


def _annualized(point_jeon: float | None, spot: float | None, days: int | None) -> float | None:
    """스왑포인트(전) → 연율 %. 전은 0.01원이라 100 으로 나눠 원으로 바꾼다."""
    if point_jeon is None or not spot or not days:
        return None
    return (point_jeon / 100) / spot * (365 / days) * 100


def _smbs_points() -> tuple[dict[str, dict], str]:
    """{만기: {bid, offer, mid}}, 기준일. 만기별로 요청이 따로 나간다."""
    window = _range()
    out: dict[str, dict] = {}
    as_of = ""
    for label, key, _days in TENORS:
        series = smbs.fetch_series("FxSwap_xml.jsp", f"{key}_{window}", "FxSwap.jsp")
        bid = smbs.last_valid(series.get("Bid", []))
        offer = smbs.last_valid(series.get("Offer", []))
        if not bid and not offer:
            continue
        as_of = as_of or (bid or offer)[0]
        mid = None
        if bid and offer:
            mid = (bid[1] + offer[1]) / 2
        out[label] = {
            "bid": bid[1] if bid else None,
            "offer": offer[1] if offer else None,
            "mid": mid if mid is not None else (bid or offer)[1],
        }
    return out, as_of


def fetch_swap_points(spot_usdkrw: float | None) -> dict | None:
    """양사 스왑포인트. 한쪽이 죽어도 남은 쪽으로 표를 만든다."""
    smbs_pts: dict[str, dict] = {}
    kmb_pts: dict[str, dict] = {}
    as_of = ""

    try:
        smbs_pts, as_of = _smbs_points()
    except Exception:
        log.exception("SMBS swap point fetch failed")
    try:
        kmb_pts, kmb_date = kmb.fetch_swap_points()
        as_of = as_of or kmb_date
    except Exception:
        log.exception("KMB swap point fetch failed")

    if not smbs_pts and not kmb_pts:
        return None

    rows = []
    for label, _key, days in TENORS:
        s = smbs_pts.get(label)
        k = kmb_pts.get(label)
        if not s and not k:
            continue
        rows.append({
            "label": label,
            "smbs": {
                **(s or {"bid": None, "offer": None, "mid": None}),
                "annualized": _annualized(s["mid"] if s else None, spot_usdkrw, days),
            },
            "kmb": {
                **(k or {"bid": None, "offer": None, "mid": None}),
                "annualized": _annualized(k["mid"] if k else None, spot_usdkrw, days),
            },
        })

    if not rows:
        return None
    return {"asOf": as_of, "spot": spot_usdkrw, "rows": rows}


def _smbs_curves() -> dict[str, dict[str, float]]:
    """{'IRS': {만기: mid}, 'CRS': {...}} — SMBS 는 1Y/3Y/5Y Mid 만 고시."""
    out: dict[str, dict[str, float]] = {}
    for kind, endpoint, page in [("IRS", "IRS_xml.jsp", "IRS.jsp"), ("CRS", "CRS_xml.jsp", "CRS.jsp")]:
        series = smbs.fetch_series(endpoint, _range(), page)
        curve = {}
        for term in _SMBS_TERMS:
            last = smbs.last_valid(series.get(f"{term}_Mid", []))
            if last:
                curve[term] = last[1]
        if curve:
            out[kind] = curve
    return out


def fetch_irs_crs() -> dict | None:
    """IRS·CRS 커브와 통화베이시스(CRS-IRS, bp). KMB 7개 만기 기본,
    KMB 실패 시 SMBS 3개 만기로 강등."""
    irs: dict[str, float] = {}
    crs: dict[str, float] = {}
    as_of = ""
    source = ""

    try:
        kmb_irs, d1 = kmb.fetch_swap_curve("IRS")
        kmb_crs, _d2 = kmb.fetch_swap_curve("CRS")
        irs = {t: v["mid"] for t, v in kmb_irs.items() if v["mid"] is not None}
        crs = {t: v["mid"] for t, v in kmb_crs.items() if v["mid"] is not None}
        as_of, source = d1, "KMB"
    except Exception:
        log.exception("KMB IRS/CRS fetch failed — falling back to SMBS")

    if not irs and not crs:
        try:
            curves = _smbs_curves()
            irs, crs = curves.get("IRS", {}), curves.get("CRS", {})
            source = "SMBS"
        except Exception:
            log.exception("SMBS IRS/CRS fetch failed")
            return None

    rows = []
    for term in SWAP_TERMS:
        i, c = irs.get(term), crs.get(term)
        if i is None and c is None:
            continue
        rows.append({
            "label": term,
            "irs": i,
            "crs": c,
            # CRS - IRS. 음수면 원화 조달이 스왑시장에서 더 싸다는 뜻.
            "basisBp": round((c - i) * 100) if i is not None and c is not None else None,
        })

    if not rows:
        return None
    return {"asOf": as_of, "source": source, "rows": rows}
