"""원화 스왑 — FX 스왑포인트(연율 환산)와 IRS·CRS.

원화 자금·스왑 시장의 양대 중개사 고시를 나란히 받는다:

  SMBS (서울외국환중개, smbs.py)  스왑포인트 1M/3M/6M/1Y · IRS/CRS 1Y/3Y/5Y Mid
  KMB  (한국자금중개, kmb.py)     스왑포인트 1M/3M/6M/1Y · IRS/CRS 1Y~10Y Bid/Offer

같은 상품을 두 회사가 따로 고시하므로 호가 폭이 비교되고 Mid 는 서로
검증이 된다 — IRS/CRS Mid 는 두 회사가 bp 단위까지 일치함을 확인했다.
스왑포인트 Mid 도 거의 같으나(1M -45 vs -40) 호가 폭은 SMBS 가 두 배쯤
넓다.

**연율 환산** — 스왑포인트 고시 단위는 전(錢, 0.01원):

    연율(%) = 스왑포인트(원) / 현물환율 × 360/일수 × 100

FX 스왑 연율은 베이스 통화(USD) 머니마켓 관습을 따라 ACT/360 이다. 데스크
pricer 의 `Swap rate` 열은 같은 값을 ACT/365 로 적으므로 전 만기가 일률적으로
365/360 = 1.39% 만큼 다르게 보인다 — 나란히 볼 때 헷갈리지 않도록 화면 각주에
명시한다.

**스팟은 고시일 스냅샷을 쓴다.** 스왑포인트는 전영업일 고시분인데 스팟만
실시간을 붙이면 시점이 어긋난다 (실측 3.6원 = 1Y yield 0.23bp). 그래서
고시일자를 같이 파싱해 (1) 스팟일을 고시일 기준 T+2 로 잡고 (2) 스팟 레벨도
그 날 종가를 쓴다. 실시간 스팟은 참고로만 따로 들고 다닌다
(`spot` / `spotSource` / `spotAsOf` / `spotLive`).

**IRS·CRS 커브** 는 만기가 많은 KMB(7개)를 기본으로 쓰고, KMB 가 죽으면
SMBS(3개)로 내려간다. CRS-IRS 가 곧 통화베이시스다.
"""
from __future__ import annotations

import logging
import re
from datetime import date, datetime, timedelta, timezone

import requests

import fx_calendar
import fx_implied
import kmb
import smbs
import ttl_cache
import usd_rates

log = logging.getLogger("krw_swap")

KST = timezone(timedelta(hours=9))

# (표시명, SMBS arr_value 접두). KMB 는 표시명으로 조회.
# 고시는 ON/TN/1W/2M/9M 도 있지만 데스크가 보는 건 이 네 구간이라
# 표를 여기에 맞춘다 — SMBS 요청도 만기당 하나라 그만큼 준다.
# 연율 환산 일수는 관습값이 아니라 fx_calendar 가 캘린더로 센다.
TENORS = [
    ("1M", "1M"),
    ("3M", "3M"),
    ("6M", "6M"),
    ("1Y", "1Y"),
]

SWAP_TERMS = ["1Y", "2Y", "3Y", "4Y", "5Y", "7Y", "10Y"]
_SMBS_TERMS = ["1Y", "3Y", "5Y"]

_LOOKBACK_DAYS = 14   # 연휴를 넘겨 최근 고시일을 잡을 만큼

# 고시일 스팟(매매기준율) — 네이버 시장지표 일별 시세. 20행이면 연휴를 넘겨도
# 고시일이 안에 든다.
_NAVER_FX_URL = "https://api.stock.naver.com/marketindex/exchange/FX_USDKRW/prices"
_NAVER_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/120.0 Safari/537.36"
    ),
    "Referer": "https://m.stock.naver.com/",
}


def _range() -> str:
    end = datetime.now(tz=KST).date()
    start = end - timedelta(days=_LOOKBACK_DAYS)
    return f"{start.isoformat()}_{end.isoformat()}"


_DATE_SEP = re.compile(r"(\d{2,4})[.\-/](\d{1,2})[.\-/](\d{1,2})")
_DATE_COMPACT = re.compile(r"(\d{4})(\d{2})(\d{2})")


def parse_quote_date(as_of: str | None) -> date | None:
    """중개사 페이지의 기준일 문자열 → date.

    SMBS 는 차트 축 라벨이라 "26.08.10", KMB 는 날짜 input 이라 "2026-08-10"
    꼴로 온다. 구분자가 있는 꼴을 먼저 보고 없으면 20260810 로 읽으며, 두 자리
    연도는 2000년대로 편다. 이 날짜가 스팟일(T+2)과 스팟 레벨의 기준점이므로
    못 읽으면 조용히 today 로 떨어지지 않고 None 을 돌려 호출부가 그 사실을
    알게 한다."""
    if not as_of:
        return None
    text = as_of.strip()
    m = _DATE_SEP.search(text) or _DATE_COMPACT.search(text)
    if not m:
        return None
    y, mo, d = (int(g) for g in m.groups())
    if y < 100:
        y += 2000
    try:
        return date(y, mo, d)
    except ValueError:
        return None


def _quote_date_spot(quote_date: date) -> tuple[float, str] | None:
    """고시일 USD/KRW **매매기준율**과 그 날짜. 네이버 시장지표 일별 시세.

    야후 일봉 종가로도 해 봤지만 USDKRW=X 는 일봉 경계가 어긋나 고시일 값이
    10원 넘게 튀는 날이 있다 — 실시간 스팟보다 오히려 나빠진다. 네이버가
    주는 건 서울 매매기준율이고, 검증일(2026-08-10) 기준 1,419.50 으로
    pricer 역산치(1418.9)와 0.6원 안에 든다.

    하루에 한 번만 바뀌는 값이라 캐시를 넉넉히 잡는다."""
    def _fetch():
        r = requests.get(_NAVER_FX_URL, params={"page": 1, "pageSize": 20},
                         headers=_NAVER_HEADERS, timeout=10)
        r.raise_for_status()
        # 최신일이 앞. 고시일 이하 첫 행이 그 날(또는 직전 영업일) 기준율이다.
        for row in r.json():
            stamp = row.get("localTradedAt", "")
            try:
                d = date.fromisoformat(stamp)
            except ValueError:
                continue
            if d > quote_date:
                continue
            px = float(str(row.get("closePrice", "")).replace(",", ""))
            return (px, stamp)
        return None

    try:
        return ttl_cache.get_or_fetch(f"usdkrw-mar:{quote_date}", 3600, _fetch)
    except Exception:
        log.exception("quote-date USD/KRW 매매기준율 fetch failed")
        return None


def _annualized(point_jeon: float | None, spot: float | None, days: int | None) -> float | None:
    """스왑포인트(전) → 연율 %. 전은 0.01원이라 100 으로 나눠 원으로 바꾼다.

    분모는 ACT/360 — FX 스왑 연율은 베이스 통화(USD) 머니마켓 관습을 따른다.
    일수도 관습값(30/90/180/365)이 아니라 스팟 → 밸류데이트 실제 경과일수를
    쓴다 (fx_calendar). 1M 을 30일로 놓으면 실제 31~33일일 때 연율이 3~10%
    틀어지고, 365 를 쓰면 전 만기가 일률적으로 1.4% 작게 나온다."""
    if point_jeon is None or not spot or not days:
        return None
    return (point_jeon / 100) / spot * (360 / days) * 100


def _smbs_points() -> tuple[dict[str, dict], str]:
    """{만기: {bid, offer, mid}}, 기준일. 만기별로 요청이 따로 나간다."""
    window = _range()
    out: dict[str, dict] = {}
    as_of = ""
    for label, key in TENORS:
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


def fetch_swap_points(spot_live: float | None) -> dict | None:
    """양사 스왑포인트. 한쪽이 죽어도 남은 쪽으로 표를 만든다.

    spot_live 는 보드가 들고 있는 실시간 USD/KRW 다. 연율 환산과 implied 계산에
    쓰는 스팟은 **고시일 종가**이고, 실시간 값은 그걸 못 구했을 때의 대체이자
    참고 열(spotLive)이다."""
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

    # 고시일이 모든 날짜의 기준점이다 — 스팟일(T+2)도, 스팟 레벨도. 못 읽으면
    # 실행일로 떨어지되 그 사실을 화면까지 들고 간다.
    quote_date = parse_quote_date(as_of)
    quote_parsed = quote_date is not None
    if not quote_parsed:
        quote_date = datetime.now(tz=KST).date()
        log.warning("quote date unparsed from %r — falling back to today", as_of)

    sched = fx_calendar.tenor_schedule(quote_date)

    close = _quote_date_spot(quote_date) if quote_parsed else None
    if close:
        spot, spot_source, spot_as_of = close[0], "고시일 매매기준율", close[1]
    else:
        spot, spot_source, spot_as_of = spot_live, "실시간", ""
        if quote_parsed:
            log.warning("quote-date close unavailable — using live spot %s", spot_live)

    rows = []
    for label, _key in TENORS:
        s = smbs_pts.get(label)
        k = kmb_pts.get(label)
        if not s and not k:
            continue
        leg = sched["tenors"].get(label, {})
        days = leg.get("days")
        rows.append({
            "label": label,
            "days": days,
            "valueDate": leg["value"].isoformat() if leg.get("value") else None,
            "smbs": {
                **(s or {"bid": None, "offer": None, "mid": None}),
                "annualized": _annualized(s["mid"] if s else None, spot, days),
            },
            "kmb": {
                **(k or {"bid": None, "offer": None, "mid": None}),
                "annualized": _annualized(k["mid"] if k else None, spot, days),
            },
        })

    if not rows:
        return None
    return {
        "asOf": as_of,
        "quoteDate": quote_date.isoformat(),
        "quoteDateParsed": quote_parsed,
        "spot": spot,
        "spotSource": spot_source,
        "spotAsOf": spot_as_of,
        "spotLive": spot_live,
        "rows": rows,
        "spotDate": sched["spot"].isoformat(),
        # par rate annuity 용 분기 그리드 (3M·6M·9M·1Y).
        "quarterDays": [q["days"] for q in sched["quarters"]],
        "daysApprox": sched["approx"],
    }


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


def fetch_implied(swap_points: dict | None, irs_crs: dict | None,
                  cd_rate_pct: float | None) -> dict | None:
    """FX-implied 원화 금리·CCS 베이시스 — fx_implied.compute 의 입력 조립.

    보드가 이미 들고 있는 것들을 그러모은다:
      스팟·스왑포인트  fetch_swap_points (전 단위 → 원으로 나눠 넘긴다)
      일수            fx_calendar (스팟 → 밸류데이트 ACT)
      USD 텀금리      usd_rates (외부, 소스 라벨을 그대로 들고 온다)
      CD 91일         kr_rates 의 IRR_CD91 을 호출부가 %로 넘겨준다
      IRS 1Y          fetch_irs_crs 의 1Y

    9M IRS 는 두 중개사 모두 고시가 없어 항상 None 이다 — 6M 은 보간이고,
    그 사실이 결과에 INTERPOLATED 로 따라붙는다.
    """
    if not swap_points or not swap_points.get("spot") or not swap_points.get("rows"):
        return None
    if cd_rate_pct is None:
        log.warning("CD 91D missing — implied basis needs it as the 1M·3M IRS proxy")
        return None

    irs_1y_pct = None
    if irs_crs:
        irs_1y_pct = next((r["irs"] for r in irs_crs["rows"]
                           if r["label"] == "1Y" and r.get("irs") is not None), None)
    if irs_1y_pct is None:
        log.warning("IRS 1Y missing — cannot anchor the long end")
        return None

    # 고시 단위는 전(0.01원). 계산기는 원 단위를 받는다.
    # **KMB 고시를 쓴다** — IRS 1Y 도 KMB 라(fetch_irs_crs 가 KMB 우선) basis 의
    # 양변이 같은 중개사 커브에서 나와야 어긋나지 않는다. KMB 가 빈 만기만
    # SMBS 로 메우고, 그때는 어느 만기가 섞였는지 pointSource 에 남긴다.
    points: dict[str, float] = {}
    point_src: dict[str, str] = {}
    for r in swap_points["rows"]:
        if r["kmb"].get("mid") is not None:
            points[r["label"]] = r["kmb"]["mid"] / 100
            point_src[r["label"]] = "KMB"
        elif r["smbs"].get("mid") is not None:
            points[r["label"]] = r["smbs"]["mid"] / 100
            point_src[r["label"]] = "SMBS"
    if not points:
        return None

    days = {r["label"]: r["days"] for r in swap_points["rows"] if r.get("days")}
    if not days:
        return None

    # 분기 그리드와 9M 일수는 **같은 고시일 스케줄**에서 나와야 한다 — 여기서
    # 캘린더를 today 로 다시 부르면 스팟이 하루 어긋나 앞의 수정이 무의미해진다.
    quarter_days = swap_points.get("quarterDays") or []
    if quarter_days:
        # 9M 은 표에 없지만 6M IRS 보간 필러로 일수가 필요하다 (3번째 분기).
        days.setdefault("9M", quarter_days[2] if len(quarter_days) > 2 else None)
        days = {k: v for k, v in days.items() if v}

    usd = usd_rates.fetch_usd_curve(days)
    if not usd:
        log.warning("USD term curve unavailable — yield/basis cannot be computed")
        return None

    try:
        res = fx_implied.compute(
            spot_mid=swap_points["spot"],
            swap_points=points,
            days=days,
            usd_rate=usd["rates"],
            cd_rate=cd_rate_pct / 100,
            irs_1y=irs_1y_pct / 100,
            irs_9m=None,
            quarter_days=quarter_days or None,
        )
    except Exception:
        log.exception("fx_implied.compute failed")
        return None

    # 입력이 어긋났을 때만 뜨는 경고 — 상시 표시하는 6M 보간 안내와 달리 화면에
    # 올라오면 그 자체가 이상신호라 따로 담는다.
    data_warnings: list[str] = []
    if swap_points.get("daysApprox"):
        data_warnings.append("밸류데이트가 휴일표 범위 밖이라 일수가 근사치다.")
    if not swap_points.get("quoteDateParsed", True):
        data_warnings.append(
            "고시일자를 못 읽어 실행일 기준 T+2 로 스팟일을 잡았다 — "
            "고시가 전영업일자면 일수가 하루씩 밀려 1M basis 가 1.5bp 움직인다.")
    if swap_points.get("spotSource") == "실시간":
        data_warnings.append(
            "고시일 종가를 못 구해 실시간 스팟을 썼다 — 스왑포인트(전영업일 고시)와 "
            "시점이 어긋난다 (1Y yield 기준 3~4원당 0.2bp).")
    res["warnings"] = data_warnings + res["warnings"]
    res["dataWarnings"] = data_warnings

    res["usd"] = usd
    # 스왑포인트 출처. 전 만기 KMB 면 "KMB", 섞이면 그 사실을 그대로 내보낸다.
    srcs = set(point_src.values())
    res["pointSource"] = "KMB" if srcs == {"KMB"} else " · ".join(
        f"{t} {s}" for t, s in point_src.items())
    res["spotDate"] = swap_points.get("spotDate")
    res["quoteDate"] = swap_points.get("quoteDate", "")
    res["spotSource"] = swap_points.get("spotSource", "")
    res["spotAsOf"] = swap_points.get("spotAsOf", "")
    res["asOf"] = swap_points.get("asOf", "")
    res["valueDates"] = {r["label"]: r.get("valueDate") for r in swap_points["rows"]}
    return res
