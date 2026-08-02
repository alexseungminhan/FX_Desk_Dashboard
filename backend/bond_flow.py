"""채권 수급 — 장외채권 투자자별 거래현황 (KOFIA).

주식 수급(kr_investor_flow.py)의 채권판. 매매유형(총거래/매도/매수) ×
채권종류 × 투자자 11주체로 오고, 순매수는 매수-매도로 직접 계산한다.

주식 쪽과 달리 서버가 기간 합산을 해주므로(조회일 from~to) 구간별로 한 번씩
부르면 된다. 대신 "최근 영업일"을 알려주는 API가 따로 없어, 총거래가 잡히는
날이 나올 때까지 하루씩 거슬러 올라가며 기준일을 찾는다.

val5='8'(억원), val6='C'(거래대금)은 화면 셀렉트박스의 코드값이다. 숫자로
넣으면 에러 없이 값만 전부 0으로 오므로 바꾸지 말 것 — kofia.py 참고.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

import kofia

log = logging.getLogger("bond_flow")

KST = timezone(timedelta(hours=9))

_SERVICE = "BISIvtrTrdSrchSO"
_FN = "list"
_DTO = "BISComDspDatDTO"
_UNIT_EOK = "8"       # 단위선택: 억원
_KIND_AMOUNT = "C"    # 조회구분: 거래대금

# 응답 필드 → 투자자. 화면을 가로 스크롤해 11개 주체를 눈으로 대조한 값이다.
# 선물(val15)이 보험(val8) 앞에 표시되지만 필드 번호는 뒤라 순서를 믿으면 안 된다.
INVESTORS = [
    ("val11", "외국인"),
    ("val5", "은행"),
    ("val6", "자산운용(공모)"),
    ("val7", "자산운용(사모)"),
    ("val8", "보험"),
    ("val10", "기금공제"),
    ("val14", "개인"),
    ("val9", "종금상호"),
    ("val12", "국가지자체"),
    ("val13", "기타법인"),
    ("val15", "선물"),
]

# 화면의 채권종류 행 라벨. 합계를 맨 앞에 세워 기본 선택으로 쓴다.
BOND_TYPES = ["합계", "국채", "통안증권", "은행채", "기타금융채", "회사채", "특수채", "지방채", "ABS"]

PERIODS = [
    {"key": "1d", "label": "1일", "days": 0},
    {"key": "1w", "label": "1주", "days": 6},
    {"key": "1m", "label": "1개월", "days": 29},
    {"key": "3m", "label": "3개월", "days": 89},
]

_MAX_LOOKBACK_DAYS = 10


def _fetch(start: str, end: str) -> dict[tuple[str, str], dict[str, str]]:
    """(매매유형, 채권종류) → 필드맵."""
    rows = kofia.query(_SERVICE, _FN, _DTO, {
        "val1": start, "val2": end,
        "val3": "", "val4": "",       # 잔존기간 미지정
        "val5": _UNIT_EOK, "val6": _KIND_AMOUNT,
    })
    return {(r.get("val1", ""), r.get("val2", "")): r for r in rows}


def _has_data(table: dict) -> bool:
    total = table.get(("총거래", "합계"))
    return bool(total) and kofia.num(total.get("val3")) > 0


def _latest_business_day() -> str | None:
    """총거래가 잡히는 가장 최근 날짜. 주말·공휴일에는 값이 비어서 온다."""
    day = datetime.now(tz=KST).date()
    for _ in range(_MAX_LOOKBACK_DAYS):
        stamp = day.strftime("%Y%m%d")
        try:
            if _has_data(_fetch(stamp, stamp)):
                return stamp
        except Exception:
            log.exception("bond flow probe failed (%s)", day)
            return None
        day -= timedelta(days=1)
    return None


def fetch_bond_flow() -> dict | None:
    """{asOf, bondTypes, periods: {1d: {합계: [{key,label,value}], ...}}}.

    value 는 억원 단위 순매수(매수-매도)."""
    as_of = _latest_business_day()
    if not as_of:
        log.warning("bond flow: no business day with data in last %s days", _MAX_LOOKBACK_DAYS)
        return None

    end = datetime.strptime(as_of, "%Y%m%d").date()
    periods: dict[str, dict[str, list[dict]]] = {}

    for p in PERIODS:
        start = (end - timedelta(days=p["days"])).strftime("%Y%m%d")
        try:
            table = _fetch(start, as_of)
        except Exception:
            log.exception("bond flow fetch failed (%s~%s)", start, as_of)
            return None

        by_type: dict[str, list[dict]] = {}
        for bond_type in BOND_TYPES:
            buy = table.get(("매수", bond_type))
            sell = table.get(("매도", bond_type))
            if not buy or not sell:
                continue
            by_type[bond_type] = [
                {
                    "key": field,
                    "label": label,
                    "value": kofia.num(buy.get(field)) - kofia.num(sell.get(field)),
                }
                for field, label in INVESTORS
            ]
        periods[p["key"]] = by_type

    types_present = [t for t in BOND_TYPES if t in periods[PERIODS[0]["key"]]]
    if not types_present:
        log.warning("bond flow: no bond types parsed — 화면 라벨이 바뀌었나?")
        return None

    return {"asOf": as_of, "bondTypes": types_present, "periods": periods}
