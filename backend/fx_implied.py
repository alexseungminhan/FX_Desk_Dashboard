"""KRW FX-implied yield · swap rate · CCS basis 계산기 (1M/3M/6M/1Y).

스왑포인트는 그 자체로는 "원 몇 전"일 뿐이라 금리로 바꿔 봐야 원화 조달이
싼지 비싼지가 보인다. 여기서 세 가지를 낸다:

  swap rate     스왑포인트를 연율로 편 것.        (STEP 1)
  implied yield 스왑에 내재된 원화 금리.           (STEP 2)
  CCS basis     그 금리와 원화 IRS 의 차이.        (STEP 4)

**교차항을 버리지 않는다.** yield ~= usd_rate + swap_rate 라는 근사가 흔히
쓰이지만, 버려지는 교차항 sr x usd x d/360 이 1Y 에서 3~4bp 다. basis 를
bp 단위로 보는 화면에서 3~4bp 는 무시할 수 없어서 곱셈식을 그대로 푼다.

**컨벤션은 애초에 안 섞는다.** growth factor 는 컨벤션이 없는 순수한 수이고
연율화만 360/365 로 갈린다. 그래서 원화 금리는 원화 관습(ACT/365)으로 펴
둔다 — KRW IRS·CD 와 분모가 같아지므로 basis 는 그냥 빼면 되고 보정 열이
필요 없다. 시장 CRS-IRS 고시도 ACT/365 라 바로 대조된다.

(ACT/360 으로 펴고 IRS 에 x360/365 를 먹이는 길도 있다. 결과는 스프레드
단위만큼(x360/365) 달라 1Y 에서 0.3bp 차이다. convention_adjust 를 켜면
그 값도 basis360Bp 로 같이 내지만 화면에는 안 쓴다 — 검증용이다.)

**6M IRS 는 고시가 없어 보간한다.** 그래서 6M basis 만큼은 숫자가 아니라
구간으로 읽어야 한다 — 보간 방식만 바꿔도 몇 bp 가 움직이고, basis 가 0
근처면 부호까지 뒤집힌다. 세 가지 보간을 다 계산해서 폭을 같이 내보낸다.
"""
from __future__ import annotations

import logging
import math

log = logging.getLogger("fx_implied")

TENORS = ["1M", "3M", "6M", "1Y"]

# 6M basis 를 "0 근처"로 볼 기준. 보간 방식 간 폭이 이보다 크거나 basis 가
# 이 안에 들면 부호를 믿지 말라는 경고를 붙인다.
INTERP_UNCERTAINTY_BP = 5.0


# -- STEP 1 -----------------------------------------------------------------

def swap_rate(points: float, spot: float, days: int) -> float:
    """스왑포인트 → 연율 (ACT/360 단리, 소수). points 는 원 단위, 부호 그대로."""
    return (points / spot) * 360 / days


# -- STEP 2 -----------------------------------------------------------------

def growth_factor(points: float, spot: float, days: int, usd_rate: float) -> float:
    """d일 동안 원화가 실제로 불어난 비율. **컨벤션이 없는 순수한 수다.**

    무재정 조건을 그대로 푼다: 달러를 굴려 (1 + usd x d/360) 을 만드는 것과
    스왑으로 원화를 받아 굴리는 것이 같아야 한다.

        (1 + points/spot) x (1 + usd x d/360) - 1

    usd x d/360 의 360 은 달러 다리의 제 컨벤션이라 그대로 둔다 — 여기까지는
    "얼마가 됐나"를 세는 것이고 아직 연율이 아니다."""
    return (1 + points / spot) * (1 + usd_rate * days / 360) - 1


def implied_yield(points: float, spot: float, days: int, usd_rate: float,
                  year: int = 365) -> float:
    """FX-implied 원화 금리 (단리, 소수). year 로 연율 컨벤션을 고른다.

    **ACT/365 가 기본이다.** 위 growth factor 는 컨벤션이 없고 연율화만
    360/365 로 갈리므로, 원화 금리를 원화 관습(ACT/365)으로 펴 두면 KRW
    IRS·CD 와 분모가 같아져 그냥 빼도 된다 — 별도 보정 열이 필요 없다.
    ACT/360 으로 펴고 IRS 에 x360/365 를 먹이는 방식과는 스프레드 자체의
    단위만큼(x360/365, 약 1.4%) 다르다. 0.3bp 수준이라 방향은 안 바뀌지만,
    시장 CRS-IRS 고시가 ACT/365 라 그쪽에 맞추는 게 대조하기 좋다.

    근사식(usd + sr)을 쓰지 않는 이유는 모듈 설명 참고."""
    return growth_factor(points, spot, days, usd_rate) * year / days


def cross_term_bp(points: float, spot: float, days: int, usd_rate: float) -> float:
    """근사식이 버리는 교차항의 크기 (bp). 근사를 쓰면 안 되는 근거를
    화면에 숫자로 보여주려고 따로 뽑는다. 스왑레이트와 같은 ACT/360 위에서
    잰다 — 근사식이 쓰이는 자리가 거기라서."""
    exact = implied_yield(points, spot, days, usd_rate, year=360)
    approx = usd_rate + swap_rate(points, spot, days)
    return (exact - approx) * 10000


# -- STEP 3-1: 6M 보간 -------------------------------------------------------

def interp_linear_days(d_lo: int, r_lo: float, d_hi: int, r_hi: float, d: int) -> float:
    """(a) 일수 선형보간. 금리를 그냥 직선으로 잇는다."""
    return r_lo + (r_hi - r_lo) * (d - d_lo) / (d_hi - d_lo)


def interp_log_df(d_lo: int, r_lo: float, d_hi: int, r_hi: float, d: int) -> float:
    """(b) log-DF 선형보간 — 구간 안에서 forward 가 일정하다고 본다.

    DF(t) = 1/(1 + r x t/365) 로 할인계수를 만들고 -lnDF 를 일수로 선형보간한
    뒤 다시 단리로 되푼다. (a) 보다 대개 몇 bp 낮게 나오고, 실측 호가는
    보통 (a) 와 (b) 사이에 있다."""
    z_lo = -math.log(1 / (1 + r_lo * d_lo / 365))
    z_hi = -math.log(1 / (1 + r_hi * d_hi / 365))
    z = z_lo + (z_hi - z_lo) * (d - d_lo) / (d_hi - d_lo)
    return (math.exp(z) - 1) * 365 / d


def interp_pchip(xs: list[float], ys: list[float], x: float) -> float:
    """단조 3차 에르미트 보간 (Fritsch-Carlson). 필러가 3개 이상일 때.

    보통의 3차 스플라인은 짧은 구간에서 출렁여 커브에 없던 봉우리를 만든다.
    PCHIP 은 기울기를 눌러 단조성을 지키므로 금리 커브에 쓸 수 있다."""
    n = len(xs)
    if n < 2:
        raise ValueError("보간에 점이 최소 둘 필요하다")
    if n == 2:
        return interp_linear_days(int(xs[0]), ys[0], int(xs[1]), ys[1], int(x))

    h = [xs[i + 1] - xs[i] for i in range(n - 1)]
    delta = [(ys[i + 1] - ys[i]) / h[i] for i in range(n - 1)]

    # 각 점의 기울기 — 내부 점은 조화평균, 부호가 바뀌면 0 으로 눌러 단조 유지.
    m = [0.0] * n
    m[0] = delta[0]
    m[-1] = delta[-1]
    for i in range(1, n - 1):
        if delta[i - 1] * delta[i] <= 0:
            m[i] = 0.0
        else:
            w1, w2 = 2 * h[i] + h[i - 1], h[i] + 2 * h[i - 1]
            m[i] = (w1 + w2) / (w1 / delta[i - 1] + w2 / delta[i])

    # 끝점 기울기도 구간 기울기의 3배를 넘지 않게 눌러 준다.
    for i, d_ in ((0, delta[0]), (n - 1, delta[-1])):
        if d_ == 0:
            m[i] = 0.0
        elif m[i] / d_ > 3:
            m[i] = 3 * d_

    # x 가 든 구간. 범위를 벗어나면 끝 구간의 3차식을 그대로 연장한다.
    k = n - 2
    for i in range(n - 1):
        if x <= xs[i + 1]:
            k = i
            break

    t = (x - xs[k]) / h[k]
    t2, t3 = t * t, t * t * t
    return (ys[k] * (2 * t3 - 3 * t2 + 1) + h[k] * m[k] * (t3 - 2 * t2 + t)
            + ys[k + 1] * (-2 * t3 + 3 * t2) + h[k] * m[k + 1] * (t3 - t2))


def interpolate_6m(days: dict[str, int], cd_rate: float, irs_1y: float,
                   irs_9m: float | None = None) -> dict:
    """6M KRW IRS 보간. 방식 셋을 모두 계산하고 그중 하나를 고른다.

    반환: {"value", "method", "variants": {"linear_days", "log_df", "pchip"?},
           "spreadBp", "interpolated": True}
    """
    d3, d6, d1y = days["3M"], days["6M"], days["1Y"]

    variants = {
        "linear_days": interp_linear_days(d3, cd_rate, d1y, irs_1y, d6),
        "log_df": interp_log_df(d3, cd_rate, d1y, irs_1y, d6),
    }

    if irs_9m is not None:
        # 9M 호가가 있으면 3M(CD)-9M-1Y 를 필러로 단조 3차. 9M 은 6M 바로
        # 옆이라 이게 있고 없고가 정확도를 크게 가른다. 9M 일수도 캘린더가
        # 세 준 값을 쓰고, 없으면 1Y 의 3/4 로 근사한다.
        d9 = days.get("9M") or round(d1y * 0.75)
        variants["pchip"] = interp_pchip([d3, d9, d1y], [cd_rate, irs_9m, irs_1y], d6)
        chosen, method = variants["pchip"], "PCHIP (3M·9M·1Y)"
    else:
        chosen, method = variants["linear_days"], "일수 선형보간 (3M·1Y)"

    vals = list(variants.values())
    return {
        "value": chosen,
        "method": method,
        "variants": variants,
        "spreadBp": (max(vals) - min(vals)) * 10000,
        "interpolated": True,
    }


# -- 전체 계산 ---------------------------------------------------------------

def compute(spot_mid: float,
            swap_points: dict[str, float],
            days: dict[str, int],
            usd_rate: dict[str, float],
            cd_rate: float,
            irs_1y: float,
            irs_9m: float | None = None,
            convention_adjust: bool = True) -> dict:
    """STEP 1~4 를 만기별로 푼다.

    spot_mid     USD/KRW 스팟 미드 (원)
    swap_points  {만기: 스왑포인트 미드}, **원 단위**, 부호 그대로 (1Y=-12.70)
    days         {만기: 스팟 → 밸류데이트 실제 경과일수}, fx_calendar 산출
    usd_rate     {만기: USD 텀 금리}, ACT/360 단리 **소수** (0.0405)
    cd_rate      CD 91일물, ACT/365 소수 — 1M·3M IRS 프록시
    irs_1y       KRW IRS 1Y 미드, ACT/365 소수
    irs_9m       있으면 6M 보간 정확도가 크게 오른다
    convention_adjust  IRS 에 x360/365 를 먹인 조정 basis 를 함께 낼지

    반환: {"rows": [...], "warnings": [...], "sixMonth": {...}, "meta": {...}}
    """
    warnings: list[str] = []

    if not spot_mid or spot_mid <= 0:
        raise ValueError("spot_mid 가 필요하다")

    six = interpolate_6m(days, cd_rate, irs_1y, irs_9m) if all(
        k in days for k in ("3M", "6M", "1Y")) else None

    # STEP 3 — 만기별 원화 IRS.
    irs_by_tenor: dict[str, tuple[float, str]] = {}
    if cd_rate is not None:
        irs_by_tenor["1M"] = (cd_rate, "CD 91D")
        irs_by_tenor["3M"] = (cd_rate, "CD 91D")
    if irs_1y is not None:
        irs_by_tenor["1Y"] = (irs_1y, "IRS 1Y")
    if six:
        irs_by_tenor["6M"] = (six["value"], "INTERPOLATED")

    rows = []
    for t in TENORS:
        pt, d = swap_points.get(t), days.get(t)
        if pt is None or not d:
            continue

        sr = swap_rate(pt, spot_mid, d)
        u = usd_rate.get(t)
        # yield 는 ACT/365 — IRS·CD 와 같은 분모라 그냥 뺄 수 있다.
        y = implied_yield(pt, spot_mid, d, u) if u is not None else None
        y360 = implied_yield(pt, spot_mid, d, u, year=360) if u is not None else None
        cross = cross_term_bp(pt, spot_mid, d, u) if u is not None else None

        irs, irs_src = irs_by_tenor.get(t, (None, ""))
        basis = (y - irs) * 10000 if y is not None and irs is not None else None
        # 참고용: ACT/360 위에서 IRS 를 환산해 뺀 값. 위 basis 와 x360/365 만
        # 다르다 (스프레드 단위 차이). 화면에는 안 쓰고 검증용으로만 남긴다.
        basis_360 = ((y360 - irs * 360 / 365) * 10000
                     if convention_adjust and y360 is not None and irs is not None else None)

        rows.append({
            "label": t,
            "days": d,
            "points": pt,
            "swapRate": sr,
            "usdRate": u,
            "impliedYield": y,
            "impliedYield360": y360,
            "crossTermBp": cross,
            "irs": irs,
            "irsSource": irs_src,
            "basisBp": basis,
            "basis360Bp": basis_360,
            "interpolated": t == "6M" and six is not None,
        })

    missing_usd = [t for t in TENORS if t in swap_points and usd_rate.get(t) is None]
    if missing_usd:
        warnings.append(
            f"USD 텀금리가 없어 {'·'.join(missing_usd)} 의 yield·basis 를 계산하지 못했다.")

    if six:
        six_row = next((r for r in rows if r["label"] == "6M"), None)
        # 불확실성 폭은 실제로 계산된 보간 편차다. 기본 ±5bp 는 하한일 뿐이고,
        # 커브가 가파르면(CD-IRS 간격이 벌어지면) 훨씬 커진다 — 그때 ±5bp 라고
        # 적으면 화면이 거짓말을 한다.
        band = max(INTERP_UNCERTAINTY_BP, six["spreadBp"])
        warnings.append(
            f"6M IRS 는 고시가 없어 보간값이다 ({six['method']}). "
            f"보간 방식 간 폭 {six['spreadBp']:.1f}bp — "
            f"6M basis 는 ±{band:.1f}bp 의 보간 불확실성을 안고 읽을 것."
            + (" 9M 호가를 넣으면 이 폭이 크게 줄어든다."
               if irs_9m is None else ""))
        if six["spreadBp"] > INTERP_UNCERTAINTY_BP * 2:
            warnings.append(
                f"3M(CD)-1Y(IRS) 구간 기울기가 가팔라 보간 편차가 "
                f"{six['spreadBp']:.1f}bp 로 벌어졌다 — 6M 은 수치가 아니라 "
                f"구간으로 볼 것.")
        if six_row and six_row["basisBp"] is not None:
            b = six_row["basisBp"]
            if abs(b) <= band:
                warnings.append(
                    f"6M basis {b:+.2f}bp 는 보간 불확실성 안에 있다 — "
                    f"보간 방식만 바꿔도 부호가 뒤집힌다. 방향으로 읽지 말 것.")

    warnings.append(
        "yield 와 KRW IRS·CD 를 모두 ACT/365 로 맞춰 뺐다 — 컨벤션 왜곡은 없다. "
        "다만 USD 텀금리 소스가 basis 를 통째로 밀어 올리거나 내리므로 절대값은 "
        "소스와 함께 읽을 것.")

    return {
        "rows": rows,
        "warnings": warnings,
        "sixMonth": six,
        "meta": {
            "spot": spot_mid,
            "cdRate": cd_rate,
            "irs1y": irs_1y,
            "irs9m": irs_9m,
            "conventionAdjust": convention_adjust,
        },
    }


if __name__ == "__main__":   # python fx_implied.py — 사양서 예시로 자기검증
    import fx_calendar

    sch = fx_calendar.tenor_schedule()
    d = {k: v["days"] for k, v in sch["tenors"].items()}

    spot = 1385.00
    pts = {"1M": -0.95, "3M": -3.10, "6M": -6.70, "1Y": -12.70}
    usd = {"1M": 0.0432, "3M": 0.0428, "6M": 0.0420, "1Y": 0.0405}
    res = compute(spot, pts, d, usd, cd_rate=0.0271, irs_1y=0.0262)

    print(f"스팟 {sch['spot']}  USD/KRW {spot}\n")
    print(f"{'만기':<5}{'일수':>5}{'포인트':>9}{'swap rate':>11}"
          f"{'USD':>9}{'yield365':>11}{'IRS':>9}{'basis':>10}"
          f"{'[360위]':>11}{'교차항':>9}")
    for r in res["rows"]:
        print(f"{r['label']:<5}{r['days']:>5}{r['points']:>9.2f}"
              f"{r['swapRate'] * 100:>10.2f}%"
              f"{r['usdRate'] * 100:>8.2f}%"
              f"{r['impliedYield'] * 100:>10.4f}%"
              f"{r['irs'] * 100:>8.3f}%"
              f"{r['basisBp']:>9.2f}bp"
              f"{r['basis360Bp']:>10.2f}bp"
              f"{r['crossTermBp']:>8.2f}bp")
    if res["sixMonth"]:
        print("\n6M IRS 보간:", res["sixMonth"]["method"])
        for k, v in res["sixMonth"]["variants"].items():
            print(f"   {k:<12}{v * 100:.4f}%")
        print(f"   폭 {res['sixMonth']['spreadBp']:.1f}bp")
    print()
    for w in res["warnings"]:
        print("!", w)
