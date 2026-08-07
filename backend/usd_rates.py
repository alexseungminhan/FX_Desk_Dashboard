"""USD 텀 금리 커브 (1M/3M/6M/1Y, ACT/360 단리) — FX-implied 계산의 필수 입력.

FX-implied 원화 금리는

    (1 + swap_point/spot) x (1 + usd_rate x d/360)

라서 **달러 금리가 없으면 아예 계산이 안 된다.** 그런데 이 값 하나가
basis 를 그대로 밀어 올리거나 내린다 — 소스를 바꾸면 basis 가 통째로
10~20bp 움직인다. 그래서 여기서는 어느 소스에서 받았는지를 값과 함께
반드시 들고 다니고, 화면에도 그대로 찍는다.

**정답은 CME Term SOFR 또는 USD OIS 커브**지만 둘 다 무료 공개 API 가
없다(Term SOFR 는 CME 라이선스 대상). 그래서 키 없이 받을 수 있는 두
가지를 나란히 받아 두고 기본값을 고른다:

  UST_CMT   미 재무부 Daily Treasury Par Yield Curve (1M/3M/6M/1Y).
            네 만기가 다 있고 **선도(오늘 시장이 보는 금리)** 라 스왑포인트와
            시점이 맞는다. 다만 국채는 OIS 보다 bill-OIS 스프레드만큼
            낮게 거래되므로 (보통 10~20bp) 여기서 나온 basis 는 그만큼
            **양(+)쪽으로 치우친다.**
  SOFR_AVG  뉴욕연준 SOFR Averages 30/90/180일 (ACT/360 복리 연율).
            무담보가 아닌 실제 SOFR 라 OIS 에 가깝지만 **후행(지난 30/90/180일
            평균)** 이라 금리 변곡점 근처에서는 크게 틀리고, 1Y 가 없다.

기본은 UST_CMT — 네 만기가 다 나오고 선도라서 스왑포인트와 시점이 맞는다.
SOFR_AVG 는 같이 받아서 화면에 스프레드로 보여준다. 그 간격이 곧
"basis 절대값을 믿지 말라"는 눈금이다.

**환산** — 두 소스 모두 ACT/360 단리로 맞춰 내보낸다:
  CMT 1M/3M/6M 은 bill 의 coupon-equivalent(ACT/365 단리) → x360/365.
  CMT 1Y 는 반기복리 par yield → 복리를 풀어 해당 일수의 단리로 환산.
  SOFR Average 는 정의상 (1 + avg x d/360) 이 기간 복리수익이라 이미 ACT/360
  단리다 — 환산 없이 그대로 쓴다.
"""
from __future__ import annotations

import csv
import html as html_mod
import io
import logging
import re
from datetime import date, datetime

import requests

log = logging.getLogger("usd_rates")

TENORS = ["1M", "3M", "6M", "1Y"]

DEFAULT_SOURCE = "TERM_SOFR"

_TIMEOUT = 10
_HEADERS = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                          "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36"}

_CMT_URL = ("https://home.treasury.gov/resource-center/data-chart-center/interest-rates/"
            "daily-treasury-rates.csv/{year}/all")
_CMT_PARAMS = {"type": "daily_treasury_yield_curve", "_format": "csv"}
# CSV 열 이름 → 우리 만기.
_CMT_COLS = {"1 Mo": "1M", "3 Mo": "3M", "6 Mo": "6M", "1 Yr": "1Y"}

_NYFED_URL = "https://markets.newyorkfed.org/api/rates/secured/sofr/last/1.json"
_NYFED_AVG_URL = "https://markets.newyorkfed.org/api/rates/all/latest.json"
_SOFR_AVG_COLS = {"average30day": "1M", "average90day": "3M", "average180day": "6M"}

_TERM_SOFR_URL = "https://www.global-rates.com/en/interest-rates/cme-term-sofr/"
# 표 왼쪽 라벨 → 우리 만기.
_TERM_SOFR_ROWS = {
    "1 month": "1M", "3 months": "3M", "6 months": "6M", "12 months": "1Y",
}

SOURCE_LABELS = {
    "TERM_SOFR": "CME Term SOFR",
    "UST_CMT": "미 재무부 국채 CMT",
    "SOFR_AVG": "뉴욕연준 SOFR 평균",
    "MANUAL": "수동 입력",
}


def _num(s: str | None) -> float | None:
    if s is None or not str(s).strip() or str(s).strip() in ("-", "N/A"):
        return None
    try:
        return float(str(s).strip())
    except ValueError:
        return None


# -- 환산 -------------------------------------------------------------------

def _be365_to_act360(rate_pct: float) -> float:
    """coupon-equivalent(ACT/365 단리) → ACT/360 단리. 소수로 반환."""
    return rate_pct / 100 * 360 / 365


def _semiannual_to_act360(par_pct: float, days: int) -> float:
    """반기복리 par yield → 해당 일수의 ACT/360 단리.

    (1 + y/2)^(2 x d/365) 만큼 불어난 값을 단리로 되푼다. 1Y 4.0% 기준
    단순히 x360/365 만 한 값보다 약 4bp 높다 — basis 에 그대로 얹히는
    크기라 제대로 푼다."""
    y = par_pct / 100
    growth = (1 + y / 2) ** (2 * days / 365)
    return (growth - 1) * 360 / days


# -- 소스 -------------------------------------------------------------------

def _fetch_cmt() -> tuple[dict[str, float], str]:
    """{만기: par yield %}, 기준일. 연초에는 전년 CSV 로 한 번 더 물러난다."""
    today = date.today()
    for year in (today.year, today.year - 1):
        try:
            r = requests.get(_CMT_URL.format(year=year), params=_CMT_PARAMS,
                             headers=_HEADERS, timeout=_TIMEOUT)
            r.raise_for_status()
        except Exception:
            log.exception("UST CMT fetch failed for %s", year)
            continue
        rows = list(csv.DictReader(io.StringIO(r.text)))
        if not rows:
            continue
        # CSV 는 최신일이 맨 위. 네 만기가 다 찬 첫 행을 쓴다.
        for row in rows:
            vals = {t: _num(row.get(col)) for col, t in _CMT_COLS.items()}
            if all(v is not None for v in vals.values()):
                stamp = datetime.strptime(row["Date"], "%m/%d/%Y").date().isoformat()
                return vals, stamp   # type: ignore[return-value]
    return {}, ""


def _fetch_term_sofr() -> tuple[dict[str, float], str]:
    """{만기: Term SOFR %}, 기준일. global-rates.com 표를 긁는다.

    표는 "라벨 | 최신일 | D-1 | ..." 꼴이라 라벨 뒤 첫 숫자가 최신값이다.
    Term SOFR 는 이미 ACT/360 단리 머니마켓 금리라 환산이 필요 없다.

    HTML 스크래핑이라 상대가 판을 바꾸면 조용히 빈손으로 돌아온다 — 그때는
    호출부가 UST_CMT 로 내려간다."""
    r = requests.get(_TERM_SOFR_URL, headers=_HEADERS, timeout=_TIMEOUT)
    r.raise_for_status()

    # 태그를 구분자로 눌러 "라벨|값|값|..." 한 줄로 만든다.
    text = re.sub(r"<script.*?</script>", " ", r.text, flags=re.S | re.I)
    text = re.sub(r"<[^>]+>", "|", text)
    text = html_mod.unescape(re.sub(r"[ \t]+", " ", text))
    text = re.sub(r"(\|\s*)+", "|", text)

    out: dict[str, float] = {}
    for label, tenor in _TERM_SOFR_ROWS.items():
        m = re.search(rf"CME Term SOFR {re.escape(label)}\|\s*([\d.]+)\s*%", text)
        if m:
            v = _num(m.group(1))
            if v is not None:
                out[tenor] = v

    # 표 머리의 첫 날짜가 최신 기준일 (MM-DD-YYYY).
    as_of = ""
    d = re.search(r"\|(\d{2})-(\d{2})-(\d{4})\|", text)
    if d:
        as_of = f"{d.group(3)}-{d.group(1)}-{d.group(2)}"
    return out, as_of


def _fetch_sofr_avg() -> tuple[dict[str, float], str]:
    """{만기: 복합평균 %}, 기준일. 1Y 는 없다 (30/90/180일만 고시)."""
    r = requests.get(_NYFED_AVG_URL, headers=_HEADERS, timeout=_TIMEOUT)
    r.raise_for_status()
    for ref in r.json().get("refRates", []):
        if ref.get("type") != "SOFRAI":
            continue
        out = {t: _num(ref.get(col)) for col, t in _SOFR_AVG_COLS.items()}
        out = {k: v for k, v in out.items() if v is not None}
        if out:
            return out, ref.get("effectiveDate", "")   # type: ignore[return-value]
    return {}, ""


def fetch_sofr_on() -> dict | None:
    """오버나잇 SOFR — 커브에는 안 쓰고 화면 참고용."""
    try:
        r = requests.get(_NYFED_URL, headers=_HEADERS, timeout=_TIMEOUT)
        r.raise_for_status()
        refs = r.json().get("refRates", [])
        if refs:
            return {"rate": _num(refs[0].get("percentRate")),
                    "asOf": refs[0].get("effectiveDate", "")}
    except Exception:
        log.exception("SOFR overnight fetch failed")
    return None


# -- 커브 -------------------------------------------------------------------

def fetch_usd_curve(days: dict[str, int],
                    source: str = DEFAULT_SOURCE,
                    manual: dict[str, float] | None = None) -> dict | None:
    """만기별 USD 텀 금리(ACT/360 단리, 소수)와 그 출처.

    days   만기별 실제 경과일수. 반기복리를 푸는 데 쓴다 (fx_calendar).
    source "UST_CMT" | "SOFR_AVG" | "MANUAL".
    manual source="MANUAL" 일 때 {만기: 금리 %}. 데스크가 Term SOFR 호가를
           직접 넣고 싶을 때 쓰는 문.

    반환: {"rates": {만기: 소수}, "source", "sourceLabel", "asOf",
           "alt": {만기: 소수} | None, "altSource", "note", "missing": [...]}
    """
    if source == "MANUAL":
        if not manual:
            log.error("source=MANUAL but no rates given")
            return None
        rates = {t: v / 100 for t, v in manual.items() if v is not None}
        return {
            "rates": rates,
            "source": "MANUAL", "sourceLabel": SOURCE_LABELS["MANUAL"],
            "asOf": date.today().isoformat(),
            "alt": None, "altSource": "",
            "note": "데스크 수동 입력",
            "missing": [t for t in TENORS if t not in rates],
        }

    term: dict[str, float] = {}
    term_as_of = ""
    cmt: dict[str, float] = {}
    cmt_as_of = ""
    avg: dict[str, float] = {}
    avg_as_of = ""
    try:
        term, term_as_of = _fetch_term_sofr()
    except Exception:
        log.exception("CME Term SOFR curve failed")
    try:
        cmt, cmt_as_of = _fetch_cmt()
    except Exception:
        log.exception("UST CMT curve failed")
    try:
        avg, avg_as_of = _fetch_sofr_avg()
    except Exception:
        log.exception("SOFR average curve failed")

    # ACT/360 단리로 환산.
    cmt_360 = {}
    for t, pct in cmt.items():
        d = days.get(t)
        if d is None:
            continue
        cmt_360[t] = _semiannual_to_act360(pct, d) if t == "1Y" else _be365_to_act360(pct)
    avg_360 = {t: pct / 100 for t, pct in avg.items()}
    # Term SOFR 는 이미 ACT/360 단리라 그대로 소수화만 한다.
    term_360 = {t: pct / 100 for t, pct in term.items()}

    if source == "SOFR_AVG":
        primary, p_as_of = avg_360, avg_as_of
        alt, alt_src, alt_as_of = cmt_360, "UST_CMT", cmt_as_of
        note = ("SOFR 30/90/180일 후행 복합평균 — 금리 변곡점 근처에서는 "
                "선도 텀금리와 크게 벌어진다. 1Y 고시 없음.")
    elif source == "UST_CMT":
        primary, p_as_of = cmt_360, cmt_as_of
        alt, alt_src, alt_as_of = term_360, "TERM_SOFR", term_as_of
        note = ("국채는 OIS 와 bill-OIS 스프레드만큼 벌어져 거래되므로 여기서 "
                "나온 basis 에는 그만큼의 치우침이 남는다.")
    else:
        primary, p_as_of = term_360, term_as_of
        alt, alt_src, alt_as_of = cmt_360, "UST_CMT", cmt_as_of
        note = ("FX-implied 의 정석 조달금리. 다만 CME 라이선스 데이터를 "
                "재배포 사이트에서 긁어 오는 것이라 지연·중단 가능성이 있고, "
                "정식 화면에는 데스크 단말 고시를 쓰는 게 맞다.")

    # Term SOFR 가 막히면(스크래핑이라 언제든 그럴 수 있다) 국채 CMT 로 내려간다.
    if not primary and source == "TERM_SOFR" and cmt_360:
        log.warning("CME Term SOFR unavailable — falling back to UST CMT")
        source, primary, p_as_of = "UST_CMT", cmt_360, cmt_as_of
        alt, alt_src, alt_as_of = avg_360, "SOFR_AVG", avg_as_of
        note = ("Term SOFR 를 못 받아 국채 CMT 로 대체했다 — basis 에 "
                "bill-OIS 만큼의 치우침이 섞인다.")

    if not primary:
        log.warning("no USD term rates from %s", source)
        return None

    return {
        "rates": primary,
        "source": source,
        "sourceLabel": SOURCE_LABELS.get(source, source),
        "asOf": p_as_of,
        "alt": alt or None,
        "altSource": alt_src if alt else "",
        "altAsOf": alt_as_of if alt else "",
        "note": note,
        "missing": [t for t in TENORS if t not in primary],
    }


if __name__ == "__main__":   # python usd_rates.py
    import fx_calendar

    sch = fx_calendar.tenor_schedule()
    d = {k: v["days"] for k, v in sch["tenors"].items()}
    for src in ("TERM_SOFR", "UST_CMT", "SOFR_AVG"):
        c = fetch_usd_curve(d, source=src)
        if not c:
            print(f"{src}: 실패")
            continue
        print(f"\n{src} ({c['sourceLabel']}) {c['asOf']}  ACT/360 단리")
        for t in TENORS:
            r = c["rates"].get(t)
            a = (c["alt"] or {}).get(t)
            gap = f"  vs {c['altSource']} {a * 100:.4f}%  ({(r - a) * 10000:+.1f}bp)" if r and a else ""
            print(f"  {t:>3}  {r * 100:.4f}%" if r else f"  {t:>3}  —", gap)
        print(f"  주의: {c['note']}")
    print("\nO/N SOFR:", fetch_sofr_on())
