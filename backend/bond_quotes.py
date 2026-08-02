"""지표종목 최종호가수익률 (KOFIA).

채권 수익률 곡선(bond_curve.py, SEIBro)이 종류×만기 레벨을 주는 대신 방향이
없다. 여기는 지표종목 18개의 **전일대비**와 연중 최고/최저를 준다 — 데스크가
"국고채 3년 3.758, 전일 -7.3bp" 로 읽는 그 값이다. 두 소스를 한 패널에서
쓰려고 따로 받는다.

CD(91일)·CP(91일)도 같은 표에 들어있어 단기금리까지 한 번에 잡힌다.

값이 없는 칸은 "-" 로 오고, 오전/오후 두 번 고시된다 (오후가 최신).
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

import kofia

log = logging.getLogger("bond_quotes")

KST = timezone(timedelta(hours=9))

_SERVICE = "BISLastAskPrcROPSrchSO"
_FN = "listDay"
_DTO = "BISComDspDatDTO"

# 화면 컬럼과 대조해 확정한 매핑 (오전/오후 · 전일대비 · 전일 · 연중최고/최저).
_NAME, _TERM, _AM, _PM, _CHG, _PREV, _HIGH, _LOW = (
    "val1", "val2", "val3", "val4", "val5", "val6", "val7", "val8",
)

# 18개 전부 늘어놓으면 패널이 표 하나로 끝나므로 데스크가 보는 것만 세운다.
TRACKED = [
    "국고채권(1년)", "국고채권(3년)", "국고채권(5년)", "국고채권(10년)", "국고채권(30년)",
    "통안증권(91일)", "통안증권(1년)", "통안증권(2년)",
    "회사채(무보증3년)AA-", "회사채(무보증3년)BBB-",
    "CD수익률(91일)", "CP(91일)",
]

_MAX_LOOKBACK_DAYS = 10


def _num(v: str | None) -> float | None:
    if not v or v.strip() in ("-", ""):
        return None
    try:
        return float(v.strip().replace(",", ""))
    except ValueError:
        return None


def fetch_bond_quotes() -> dict | None:
    """{asOf, rows: [{label, term, yield, changeBp, high, low}]}."""
    day = datetime.now(tz=KST).date()
    rows: list[dict[str, str]] = []
    for _ in range(_MAX_LOOKBACK_DAYS):
        try:
            rows = kofia.query(_SERVICE, _FN, _DTO, {"val1": day.strftime("%Y%m%d")})
        except Exception:
            log.exception("bond quotes fetch failed (%s)", day)
            return None
        if rows:
            break
        day -= timedelta(days=1)   # 주말·공휴일엔 고시가 없다

    if not rows:
        log.warning("bond quotes: no data in last %s days", _MAX_LOOKBACK_DAYS)
        return None

    by_name = {r.get(_NAME, "").strip(): r for r in rows}

    out = []
    for name in TRACKED:
        r = by_name.get(name)
        if not r:
            continue
        # 오후 고시가 최신이고, 아직 안 나왔으면 오전으로 떨어뜨린다.
        level = _num(r.get(_PM)) if _num(r.get(_PM)) is not None else _num(r.get(_AM))
        if level is None:
            continue
        chg = _num(r.get(_CHG))
        out.append({
            "label": name,
            "term": r.get(_TERM, "").strip(),
            "yield": level,
            # 고시는 %p 라 bp 로 바꿔 보여준다 (-0.073 -> -7.3bp).
            "changeBp": None if chg is None else round(chg * 100, 1),
            "prev": _num(r.get(_PREV)),
            "high": _num(r.get(_HIGH)),
            "low": _num(r.get(_LOW)),
        })

    if not out:
        log.warning("bond quotes: none of the tracked 종목 present — 라벨이 바뀌었나?")
        return None

    return {"asOf": day.strftime("%Y%m%d"), "rows": out}
