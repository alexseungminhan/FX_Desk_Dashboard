"""외화표시채 발행 내역 — SEIBro 외화표시채 종목검색 (BIP_CNTS03021V).

키워드 뉴스의 "외화채권" 탭이 기사로 짐작하던 걸 실제 발행 데이터로 받는다.
발행일·통화·발행액·쿠폰·만기가 다 들어있어, 누가 언제 얼마를 무슨 금리로
조달했는지 그대로 보인다.

발행이 뜸한 구간이 있어 최근 N개월을 훑고 발행일 내림차순으로 자른다.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

import seibro

log = logging.getLogger("fx_bond_issue")

KST = timezone(timedelta(hours=9))

_W2X = "/IPORTAL/user/bond/BIP_CNTS03021V.xml"
_ACTION = "issuSecnPListEL1"
_TASK = "ksd.safe.bip.cnts.bone.process.FrbondIssuSecnPTask"

_LOOKBACK_DAYS = 365
_FETCH_ROWS = 60


def _fmt_amount(amount: float | None, currency: str) -> str:
    """발행액은 통화 원단위로 온다(400000000 = 4억 CNY). 억/백만 단위로 접는다."""
    if amount is None:
        return "—"
    if abs(amount) >= 100_000_000:
        return f"{amount / 100_000_000:,.2f}억 {currency}"
    if abs(amount) >= 1_000_000:
        return f"{amount / 1_000_000:,.1f}백만 {currency}"
    return f"{amount:,.0f} {currency}"


def _fmt_date(v: str | None) -> str:
    if not v or len(v) != 8:
        return "—"
    return f"{v[2:4]}.{v[4:6]}.{v[6:8]}"


def fetch_fx_bond_issues(limit: int = 20) -> list[dict] | None:
    """최근 외화표시채 발행 내역, 발행일 최신순."""
    today = datetime.now(tz=KST).date()
    start = today - timedelta(days=_LOOKBACK_DAYS)

    try:
        rows = seibro.query(_W2X, _ACTION, _TASK, {
            "ISSU_DT_START": start.strftime("%Y%m%d"),
            "ISSU_DT_END": today.strftime("%Y%m%d"),
            "XPIR_DT_START": "", "XPIR_DT_END": "",
            "START_PAGE": "1", "END_PAGE": str(_FETCH_ROWS),
            "PAGE_NUM": "1", "PAGE_ON_CNT": str(_FETCH_ROWS),
        })
    except Exception:
        log.exception("fx bond issuance fetch failed")
        return None

    out = []
    for r in rows:
        issu_dt = r.get("ISSU_DT", "")
        currency = r.get("ISSU_CUR_CD", "")
        coupon = seibro.num(r.get("COUPON_RATE"))
        out.append({
            "issueDate": _fmt_date(issu_dt),
            "sortKey": issu_dt,
            "issuer": r.get("REP_SECN_NM", ""),
            "name": r.get("KOR_SECN_NM", ""),
            "kind": r.get("SECN_DTAIL", ""),
            "currency": currency,
            "amount": _fmt_amount(seibro.num(r.get("FIRST_ISSU_AMT")), currency),
            "coupon": f"{coupon:.2f}%" if coupon is not None else "—",
            "maturity": _fmt_date(r.get("XPIR_DT")),
            "rating": r.get("KIS_APLI_CREDIT_GRD_CD_NM", ""),
        })

    out.sort(key=lambda x: x["sortKey"], reverse=True)
    return out[:limit]
