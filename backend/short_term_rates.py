"""단기금융시장 금리 — CP·전자단기사채 대표수익률 (KOFIA).

보드의 국내 단기금리가 CD 91일 하나뿐이라 원화 단기 조달을 볼 게 없었다.
여기는 CP 와 전단채를 **만기구간 5개**로 나눠 가중평균금리를 주므로 단기
조달 기간구조가 통째로 잡힌다.

응답 한 행이 (구분, 항목) 조합이다:
  구분 = 할인 / 매출 / 중개,  항목 = 가중평균금리 / 당일거래대금 / 거래량 / 금일잔액
금리만 쓰고 거래대금은 그 구간이 실제로 거래된 물량인지 가늠하는 용도로
합계만 같이 내린다 — 거래가 거의 없는 구간의 금리는 참고치이기 때문이다.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

import kofia

log = logging.getLogger("short_term_rates")

KST = timezone(timedelta(hours=9))

_DTO = "BISComDspDatDTO"

# (표시명, 서비스ID) — 두 시장이 같은 응답 형태를 쓴다.
MARKETS = [
    ("CP", "BISCPRepSrchSO"),
    ("전단채", "BISITShotPMnyRepROPSrchSO"),
]

# 발행 방식. 중개는 금리 고시가 없어 뺀다.
KINDS = ["할인", "매출"]

# val3~val7 이 만기구간, val8 이 합계.
TENORS = [
    ("val3", "59일 이하"),
    ("val4", "60~90일"),
    ("val5", "91~180일"),
    ("val6", "181~270일"),
    ("val7", "271일~1년"),
]
_TOTAL = "val8"

_RATE_ROW = "가중평균금리"
_AMOUNT_ROW = "당일거래대금"

_MAX_LOOKBACK_DAYS = 10


def _num(v: str | None) -> float | None:
    if not v or v.strip() in ("-", ""):
        return None
    try:
        return float(v.strip().replace(",", ""))
    except ValueError:
        return None


def _fetch(service: str, std_dt: str) -> dict[tuple[str, str], dict[str, str]]:
    rows = kofia.query(service, "listDay", _DTO, {"val1": std_dt})
    return {(r.get("val1", ""), r.get("val2", "")): r for r in rows}


def fetch_short_term_rates() -> dict | None:
    """{asOf, tenors, rows: [{label, rates: [값|None], amount}]}."""
    day = datetime.now(tz=KST).date()
    tables: dict[str, dict] = {}

    for _ in range(_MAX_LOOKBACK_DAYS):
        stamp = day.strftime("%Y%m%d")
        try:
            tables = {name: _fetch(svc, stamp) for name, svc in MARKETS}
        except Exception:
            log.exception("short-term rates fetch failed (%s)", day)
            return None
        if any(t for t in tables.values()):
            break
        day -= timedelta(days=1)

    if not any(tables.values()):
        log.warning("short-term rates: no data in last %s days", _MAX_LOOKBACK_DAYS)
        return None

    rows = []
    for market, _svc in MARKETS:
        table = tables.get(market, {})
        for kind in KINDS:
            rate_row = table.get((kind, _RATE_ROW))
            if not rate_row:
                continue
            amount_row = table.get((kind, _AMOUNT_ROW)) or {}
            rows.append({
                "label": f"{market} {kind}",
                "rates": [_num(rate_row.get(field)) for field, _ in TENORS],
                # 거래대금은 백만원 단위로 온다.
                "amount": _num(amount_row.get(_TOTAL)),
            })

    if not rows:
        log.warning("short-term rates: 금리 행을 못 찾음 — 구분/항목 라벨이 바뀌었나?")
        return None

    return {"asOf": day.strftime("%Y%m%d"), "tenors": [t[1] for t in TENORS], "rows": rows}
