"""USD/KRW 스왑 밸류데이트 캘린더 — 스팟(T+2)과 만기별 밸류데이트.

스왑포인트를 연율로 바꾸거나 FX-implied 금리를 뽑을 때 분모로 들어가는 건
"30일/90일/180일/365일" 같은 관습 숫자가 아니라 **스팟일에서 밸류데이트까지의
실제 경과일수(ACT)** 다. 둘의 차이는 생각보다 크다 — 1M 을 30일로 놓았는데
실제가 33일이면 연율이 10% 틀어진다.

규칙:

  스팟      거래일 + 영업일 2일. USD/KRW 는 서울·뉴욕이 **둘 다** 열려야
            결제가 되므로 두 캘린더의 교집합으로 센다.
  밸류데이트 스팟 + n개월, modified following (주말·휴일이면 다음 영업일로
            밀되 달을 넘기면 앞으로 당긴다).
  월말규칙   스팟이 그 달의 마지막 영업일이면 밸류데이트도 해당 월의 마지막
            영업일로 맞춘다 (end-of-month rule).

**휴일표는 손으로 넣은 것이라 해마다 갱신이 필요하다.** COVERAGE 밖 날짜는
주말만 보고 넘어가며 경고를 남긴다 — 조용히 틀린 일수를 내놓느니 일수가
근사치임을 알리는 편이 낫다. 대체공휴일은 2023년 개정(토요일 포함) 기준.
"""
from __future__ import annotations

import calendar as _cal
import logging
from datetime import date, timedelta

log = logging.getLogger("fx_calendar")

# 만기 → 개월 수. 앞의 네 개가 데스크가 보는 구간이고, 9M 은 표에 뜨지는
# 않지만 6M IRS 를 보간할 때 필러 일수로 쓴다 (fx_implied.interpolate_6m).
TENOR_MONTHS = {"1M": 1, "3M": 3, "6M": 6, "9M": 9, "1Y": 12}

# 한국 — 공휴일 + 근로자의날(금융기관 휴무라 외환결제도 안 된다) + 대체공휴일.
_KRW_HOLIDAYS = {
    # 2026
    date(2026, 1, 1),                                        # 신정
    date(2026, 2, 16), date(2026, 2, 17), date(2026, 2, 18),  # 설 연휴
    date(2026, 3, 1), date(2026, 3, 2),                      # 삼일절 + 대체
    date(2026, 5, 1),                                        # 근로자의날
    date(2026, 5, 5),                                        # 어린이날
    date(2026, 5, 24), date(2026, 5, 25),                    # 부처님오신날 + 대체
    date(2026, 6, 3),                                        # 지방선거
    date(2026, 6, 6),                                        # 현충일 (대체 없음)
    date(2026, 8, 15), date(2026, 8, 17),                    # 광복절 + 대체
    date(2026, 9, 24), date(2026, 9, 25), date(2026, 9, 26),  # 추석 연휴
    date(2026, 9, 28),                                       # 추석 대체
    date(2026, 10, 3), date(2026, 10, 5),                    # 개천절 + 대체
    date(2026, 10, 9),                                       # 한글날
    date(2026, 12, 25),                                      # 성탄절
    # 2027
    date(2027, 1, 1),
    date(2027, 2, 6), date(2027, 2, 7), date(2027, 2, 8),     # 설 연휴
    date(2027, 2, 9), date(2027, 2, 10),                      # 설 대체 (토·일 겹침)
    date(2027, 3, 1),
    date(2027, 5, 1),                                        # 근로자의날 (대체 없음)
    date(2027, 5, 5),
    date(2027, 5, 13),                                       # 부처님오신날
    date(2027, 6, 6),                                        # 현충일 (대체 없음)
    date(2027, 8, 15), date(2027, 8, 16),                    # 광복절 + 대체
    date(2027, 9, 14), date(2027, 9, 15), date(2027, 9, 16),  # 추석 연휴
    date(2027, 10, 3), date(2027, 10, 4),                    # 개천절 + 대체
    date(2027, 10, 9), date(2027, 10, 11),                   # 한글날 + 대체
    date(2027, 12, 25), date(2027, 12, 27),                  # 성탄절 + 대체
}

# 미국 — 연방공휴일(관측일). Good Friday 는 연방공휴일이 아니고 달러 결제도
# 되므로 뺀다 (채권시장 SIFMA 휴장과는 다른 얘기다).
_USD_HOLIDAYS = {
    # 2026
    date(2026, 1, 1), date(2026, 1, 19), date(2026, 2, 16),
    date(2026, 5, 25), date(2026, 6, 19),
    date(2026, 7, 3),                                        # 7/4 토 → 금 관측
    date(2026, 9, 7), date(2026, 10, 12), date(2026, 11, 11),
    date(2026, 11, 26), date(2026, 12, 25),
    # 2027
    date(2027, 1, 1), date(2027, 1, 18), date(2027, 2, 15),
    date(2027, 5, 31),
    date(2027, 6, 18),                                       # 6/19 토 → 금 관측
    date(2027, 7, 5),                                        # 7/4 일 → 월 관측
    date(2027, 9, 6), date(2027, 10, 11), date(2027, 11, 11),
    date(2027, 11, 25),
    date(2027, 12, 24),                                      # 12/25 토 → 금 관측
}

# 휴일표가 실제로 채워진 구간. 밸류데이트가 여기를 벗어나면 일수는 근사치다.
COVERAGE_START = date(2026, 1, 1)
COVERAGE_END = date(2027, 12, 31)

_HOLIDAYS = _KRW_HOLIDAYS | _USD_HOLIDAYS


def covered(d: date) -> bool:
    return COVERAGE_START <= d <= COVERAGE_END


def is_business_day(d: date) -> bool:
    """서울·뉴욕이 모두 열린 날인가. 커버 범위 밖이면 주말만 본다."""
    return d.weekday() < 5 and d not in _HOLIDAYS


def _next_business_day(d: date) -> date:
    while not is_business_day(d):
        d += timedelta(days=1)
    return d


def _prev_business_day(d: date) -> date:
    while not is_business_day(d):
        d -= timedelta(days=1)
    return d


def _modified_following(d: date) -> date:
    """다음 영업일로 밀되, 달을 넘기면 거꾸로 당긴다."""
    nxt = _next_business_day(d)
    if nxt.month != d.month:
        return _prev_business_day(d)
    return nxt


def _last_business_day_of_month(y: int, m: int) -> date:
    return _prev_business_day(date(y, m, _cal.monthrange(y, m)[1]))


def spot_date(quote_date: date) -> date:
    """**고시일** → 스팟(T+2). 양 캘린더 공통 영업일로 두 칸 민다.

    인자는 실행 시각이 아니라 **스왑포인트 고시일**이다. 스왑포인트는 전영업일
    고시분을 받아 쓰므로 실행일에서 T+2 를 잡으면 스팟이 하루 밀리고, 그
    하루가 1M basis 를 1.5bp 움직인다 (1Y 는 0.24bp). 호출부는 스크래핑에서
    파싱한 고시일자를 넘겨야 한다."""
    d = quote_date
    for _ in range(2):
        d = _next_business_day(d + timedelta(days=1))
    return d


def _add_months(d: date, months: int) -> date:
    m = d.month - 1 + months
    y = d.year + m // 12
    m = m % 12 + 1
    return date(y, m, min(d.day, _cal.monthrange(y, m)[1]))


def value_date(spot: date, months: int) -> date:
    """스팟 + n개월. 월말규칙 우선, 아니면 modified following."""
    if spot == _last_business_day_of_month(spot.year, spot.month):
        tgt = _add_months(spot, months)
        return _last_business_day_of_month(tgt.year, tgt.month)
    return _modified_following(_add_months(spot, months))


def quarterly_schedule(spot: date, n_quarters: int = 4) -> list[dict]:
    """스팟에서 3개월 간격 지급일 [{'value', 'days'}, ...].

    par swap rate 의 annuity 에 들어갈 날짜다. KRW IRS 지급 관습이 분기이고
    날짜 규칙(월말 우선 · modified following)은 밸류데이트와 같으므로
    `value_date` 를 그대로 3·6·9·12개월에 태운다 — 그래야 3M/6M/1Y 그리드
    점이 같은 만기의 FX 밸류데이트와 정확히 일치한다."""
    return [{"value": v, "days": (v - spot).days}
            for v in (value_date(spot, 3 * i) for i in range(1, n_quarters + 1))]


def tenor_schedule(quote_date: date | None = None) -> dict:
    """{'spot': date, 'tenors': {'1M': {'value': date, 'days': int}, ...},
    'quarters': [{'value','days'}, ...], 'approx': bool}.
    `days` 는 스팟 → 밸류데이트 실제 경과일수(ACT).

    인자는 **고시일**이다 (spot_date 참고). 기본값 today 는 고시일을 못 구한
    경우의 최후 수단일 뿐이고, 정상 경로에서는 호출부가 파싱한 고시일을 준다.

    `quarters` 는 par rate annuity 용 분기 그리드(3M·6M·9M·1Y).

    approx=True 면 밸류데이트 하나 이상이 휴일표 밖이라 일수가 주말만 반영한
    근사치라는 뜻이다 — 호출부는 이걸 화면에 표시해야 한다."""
    quote_date = quote_date or date.today()
    spot = spot_date(quote_date)

    out: dict[str, dict] = {}
    approx = not covered(spot)
    for label, months in TENOR_MONTHS.items():
        v = value_date(spot, months)
        if not covered(v):
            approx = True
        out[label] = {"value": v, "days": (v - spot).days}

    quarters = quarterly_schedule(spot, 4)

    if approx:
        log.warning(
            "value date outside holiday table %s..%s — ACT day counts are approximate",
            COVERAGE_START, COVERAGE_END,
        )
    return {"spot": spot, "tenors": out, "quarters": quarters, "approx": approx}


if __name__ == "__main__":   # python fx_calendar.py [YYYY-MM-DD]
    import sys

    td = date.fromisoformat(sys.argv[1]) if len(sys.argv) > 1 else date.today()
    sch = tenor_schedule(td)
    print(f"고시일 {td}  스팟 {sch['spot']}"
          f"{'  (일수 근사)' if sch['approx'] else ''}")
    for k, v in sch["tenors"].items():
        print(f"  {k:>3}  밸류 {v['value']}  ACT {v['days']:>3}일")
    print("  분기 그리드:", "  ".join(
        f"{q['value']} ({q['days']}일)" for q in sch["quarters"]))
