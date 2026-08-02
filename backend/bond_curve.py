"""채권 만기수익률 곡선 — SEIBro 채권만기수익률 (BIP_CNTS03030V).

국채·통안·특수채·금융채·회사채를 신용등급별로 3M/6M/9M/1Y/3Y/5Y/10Y/20Y
전 구간 제공한다. 네이버 국내시장금리(kr_rates.py)가 국고채 3년 하나만
주는 것에 비해 커브 전체를 그릴 수 있다.

일별 확정치라 하루 한 번만 바뀐다. 기준일을 안 넘기면 응답이 비므로 최근
영업일부터 하루씩 물러나며 값이 있는 날을 찾는다 (주말·공휴일 대응).
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

import seibro

log = logging.getLogger("bond_curve")

KST = timezone(timedelta(hours=9))

_W2X = "/IPORTAL/user/bond/BIP_CNTS03030V.xml"
_ACTION = "xpirPrateList"
_TASK = "ksd.safe.bip.cnts.bone.process.BondSecnPTask"

# 응답 필드 → 표시할 만기. 순서가 곧 커브의 x축이다.
TENORS = [
    ("MONS3_XPIR_PRATE", "3개월", 0.25),
    ("MONS6_XPIR_PRATE", "6개월", 0.5),
    ("MONS9_XPIR_PRATE", "9개월", 0.75),
    ("YY1_XPIR_PRATE", "1년", 1),
    ("YY3_XPIR_PRATE", "3년", 3),
    ("YY5_XPIR_PRATE", "5년", 5),
    ("YY10_XPIR_PRATE", "10년", 10),
    ("YY20_XPIR_PRATE", "20년", 20),
]

# 40개 종류를 다 보여주면 표가 못 읽을 것이 되므로, 데스크가 실제로 보는
# 벤치마크만 골라 이 순서로 세운다. KISP_BOND_SORT_NM 과 정확히 일치한다.
CURVES = [
    ("양곡, 외평, 재정", "국고채권"),
    ("통안증권", "통안증권"),
    ("산금채 AAA", "산금채 AAA"),
    ("무보증 공모 회사채 AAA", "회사채 AAA"),
    ("무보증 공모 회사채 AA0", "회사채 AA0"),
    ("무보증 공모 회사채 A0", "회사채 A0"),
    ("무보증 공모 회사채 BBB0", "회사채 BBB0"),
]

_MAX_LOOKBACK_DAYS = 10


def _fetch_day(std_dt: str) -> list[dict[str, str]]:
    return seibro.query(_W2X, _ACTION, _TASK, {"STD_DT": std_dt})


def fetch_bond_curve() -> dict | None:
    """{asOf, curves: [{key, label, points: [{label, years, yield}]}]}.

    값이 0인 만기는 '해당 구간 고시 없음'이라 곡선에서 뺀다 — 0%로 그리면
    커브가 바닥으로 꺾여 사실과 다른 그림이 된다."""
    day = datetime.now(tz=KST).date()
    rows: list[dict[str, str]] = []
    for _ in range(_MAX_LOOKBACK_DAYS):
        try:
            rows = _fetch_day(day.strftime("%Y%m%d"))
        except Exception:
            log.exception("bond curve fetch failed (%s)", day)
            return None
        if rows:
            break
        day -= timedelta(days=1)   # 주말·공휴일은 고시가 없다

    if not rows:
        log.warning("bond curve: no data in last %s days", _MAX_LOOKBACK_DAYS)
        return None

    by_name = {r.get("KISP_BOND_SORT_NM", ""): r for r in rows}

    curves = []
    for source_name, label in CURVES:
        row = by_name.get(source_name)
        if not row:
            continue
        points = []
        for field, tenor_label, years in TENORS:
            v = seibro.num(row.get(field))
            if v is None or v == 0:
                continue
            points.append({"label": tenor_label, "years": years, "yield": v})
        if points:
            curves.append({"key": label, "label": label, "points": points})

    if not curves:
        log.warning("bond curve: none of the tracked curves present — 종류명 바뀜?")
        return None

    return {"asOf": rows[0].get("STD_DT", day.strftime("%Y%m%d")), "curves": curves}
