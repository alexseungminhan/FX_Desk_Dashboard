"""KRW FX-implied yield · swap rate · CCS basis 계산기 (1M/3M/6M/1Y).

스왑포인트는 그 자체로는 "원 몇 전"일 뿐이라 금리로 바꿔 봐야 원화 조달이
싼지 비싼지가 보인다. 여기서 네 가지를 낸다:

  swap rate     스왑포인트를 연율로 편 것.               (STEP 1)
  growth · DF   스왑에 내재된 증가율과 할인계수.          (STEP 2 · 2.5)
  par rate      그 할인커브를 IRS 관습으로 다시 읽은 값.  (STEP 3)
  CCS basis     par rate 와 원화 IRS 의 차이.            (STEP 4)

**교차항을 버리지 않는다.** yield ~= usd_rate + swap_rate 라는 근사가 흔히
쓰이지만, 버려지는 교차항 sr x usd x d/360 이 1Y 에서 3~4bp 다. basis 를
bp 단위로 보는 화면에서 3~4bp 는 무시할 수 없어서 곱셈식을 그대로 푼다.

**컨벤션은 애초에 안 섞는다.** growth factor 는 컨벤션이 없는 순수한 수이고
연율화만 360/365 로 갈린다. 원화 금리는 원화 관습(ACT/365)으로 편다.

**다만 일수만 맞춰서는 부족하다 — 복리·지급주기까지 맞춰야 한다.** 빼줄
상대인 KRW IRS 고시 자체가 분기지급 par swap rate 이므로, 단리 zero 를
그대로 빼면 만기가 길수록 구조적으로 벌어진다 (6M -1.2bp, 1Y -3.9bp,
2Y -10bp). 그래서 DF 를 세우고 분기 그리드 위에서 par rate 를 다시 뽑아
**par - par** 로 뺀다. 단리 zero(`yieldSimple`)는 데스크 pricer 의 `KRW Zero`
열과 직접 대조되는 유일한 지점이라 검증열로 남긴다.

**FX 스왑에 중간 현금흐름이 생기는 게 아니다.** par 로 바꾸는 것은 표기
변환이고, 실제 현금흐름은 스팟 한 번·만기 한 번 그대로다. 우리가 하는 일은
FX-implied **할인커브**를 IRS 고시 관습으로 다시 읽는 것뿐이다. 그 대신
1Y par 은 1Y 스왑포인트 하나로 못 만든다 — annuity 에 3M·6M·9M DF 가 전부
들어가므로 계산이 만기별 독립 루프가 아니라 **커브 단위 2-pass** 다.

**9M 은 양사 모두 고시가 없어 log-linear DF 보간**으로 채운다. annuity
가중치로만 들어가 오차가 크게 희석되므로(9M zero ±20bp -> 1Y par ±0.11bp)
경고 배지는 붙이지 않고 `pillarSource` 에만 남긴다.

**6M IRS 도 고시가 없어 보간한다.** 이쪽은 basis 에 1:1 로 전가되므로 6M
basis 만큼은 숫자가 아니라 구간으로 읽어야 한다 — 보간 방식만 바꿔도 몇 bp 가
움직이고, basis 가 0 근처면 부호까지 뒤집힌다. 방식별 값을 다 계산해서 폭을
같이 내보낸다.
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
    """FX-implied 원화 금리 (단리 zero, 소수). year 로 연율 컨벤션을 고른다.

    **ACT/365 가 기본이다.** 위 growth factor 는 컨벤션이 없고 연율화만
    360/365 로 갈리므로, 원화 금리를 원화 관습(ACT/365)으로 편다.

    **이 값은 중간 산출물이다.** 화면과 basis 에 쓰는 건 par rate(STEP 3)이고,
    이 단리 zero 는 `yieldSimple` 검증열로만 남는다 — pricer 의 `KRW Zero` 열과
    소수 4자리까지 일치하는 자리라 회귀 테스트의 기준점이 된다. 만기 <= 3M
    에서는 par 와 값이 같아진다 (par_rate 설명 참고).

    근사식(usd + sr)을 쓰지 않는 이유는 모듈 설명 참고."""
    return growth_factor(points, spot, days, usd_rate) * year / days


def cross_term_bp(points: float, spot: float, days: int, usd_rate: float) -> float:
    """근사식이 버리는 교차항의 크기 (bp). 근사를 쓰면 안 되는 근거를
    화면에 숫자로 보여주려고 따로 뽑는다. 스왑레이트와 같은 ACT/360 위에서
    잰다 — 근사식이 쓰이는 자리가 거기라서."""
    exact = implied_yield(points, spot, days, usd_rate, year=360)
    approx = usd_rate + swap_rate(points, spot, days)
    return (exact - approx) * 10000


# -- STEP 2.5: 할인계수와 분기 그리드 ---------------------------------------

def df_from_growth(growth: float) -> float:
    """증가율 → 할인계수. **연율화 이전의 G 에서 바로 만든다.**

    G 는 컨벤션이 없는 순수 증가율이라 여기서 일수/365 를 다시 나누면 안 된다.
    d일 뒤 1원의 현재가치가 곧 1/(1+G) 다."""
    return 1.0 / (1.0 + growth)


def interp_df_loglinear(t: float, pillars: list[tuple[float, float]]) -> float:
    """log-linear DF 보간. pillars 는 [(일수, DF), ...], 스팟 (0, 1.0) 은 자동.

        ln DF(t) = ln DF_lo + (ln DF_hi - ln DF_lo) x (t - t_lo)/(t_hi - t_lo)

    구간 안에서 연속복리 선도금리가 일정하다고 보는 것과 같다 — 금리 보간과
    달리 음(-)의 선도금리가 구조적으로 나올 수 없어서 커브 채우기에 안전하다.
    9M 처럼 고시가 없는 지점을 annuity 에 넣을 때 쓴다.

    필러 범위를 벗어나면 끝 구간의 기울기를 그대로 연장한다."""
    grid = [(0.0, 1.0)] + sorted((float(d), df) for d, df in pillars if d > 0)
    if len(grid) < 2:
        raise ValueError("DF 보간에 필러가 최소 하나 필요하다")
    if t <= 0:
        return 1.0

    lo, hi = grid[0], grid[1]
    for i in range(len(grid) - 1):
        lo, hi = grid[i], grid[i + 1]
        if t <= hi[0]:
            break   # 마지막까지 못 찾으면 끝 구간으로 외삽

    ln_lo, ln_hi = math.log(lo[1]), math.log(hi[1])
    w = (t - lo[0]) / (hi[0] - lo[0])
    return math.exp(ln_lo + (ln_hi - ln_lo) * w)


def payment_grid(tenor_days: int, quarter_days: list[int]) -> list[int]:
    """만기까지의 분기 지급일 일수 목록. 마지막 항이 항상 만기 자신이다.

    3M 이하는 지급이 한 번뿐이라 [만기] 하나만 나온다 — 그때 par 식은 자동으로
    단리와 같아지므로 특수 분기를 둘 필요가 없다 (par_rate 설명 참고)."""
    return [q for q in sorted(quarter_days) if 0 < q < tenor_days] + [tenor_days]


# -- STEP 3: par swap rate ---------------------------------------------------

def par_rate(grid: list[int], df_at, year: int = 365) -> float:
    """분기 그리드 위의 par swap rate (소수).

        par = (1 - DF_N) / sum(DF_i x tau_i),   tau_i = (t_i - t_{i-1})/365

    고정·변동 양다리 모두 분기·ACT/365 (KRW IRS 관습)이고, 변동다리 PV 를
    1 - DF_N 으로 두는 단일커브 가정이다.

    **왜 par 인가** — 빼줄 상대인 KRW IRS 고시가 par swap rate 라서다.
    par - par 이어야 같은 물건끼리 빼는 것이고, zero - par 은 만기에 비례해
    벌어진다.

    **만기 <= 3M 은 자동으로 단리와 같아진다.** 지급이 한 번뿐이면
    DF_N = 1/(1+G) 를 넣어 par = (1-DF_N)/(DF_N x tau) = G/tau = G x 365/d
    로 정확히 단리 zero 가 된다 — 분기 처리가 필요 없는 이유다."""
    if not grid:
        raise ValueError("지급 그리드가 비었다")
    annuity = 0.0
    prev = 0
    for t in grid:
        annuity += df_at(t) * (t - prev) / year
        prev = t
    return (1 - df_at(grid[-1])) / annuity


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
    # (a)/(b) 대조 로그. 실측 6M 호가가 잡히는 날 이 줄과 나란히 놓으면 실측이
    # (a)-(b) 사이 어디에 앉는지 분포가 쌓인다 — 관측이 5~10개 모이기 전에는
    # 가중치를 고정하지 않고 (a) 를 그대로 쓴다. 실측 1건(2026-08-10, 3.1527%)
    # 은 (a) 에서 (b) 쪽으로 27% 지점이었다.
    log.info("6M IRS interp: linear_days=%.4f%% log_df=%.4f%% chosen=%.4f%% (%s) spread=%.1fbp",
             variants["linear_days"] * 100, variants["log_df"] * 100,
             chosen * 100, method, (max(vals) - min(vals)) * 10000)

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
            quarter_days: list[int] | None = None) -> dict:
    """STEP 1~4 를 **커브 단위 2-pass** 로 푼다.

    pass 1  만기별 growth factor G 와 DF = 1/(1+G) — 여기까지는 만기 독립.
    pass 2  분기 그리드에 DF 를 채우고(없는 점은 log-linear 보간) 만기별
            par rate 를 뽑는다 — 1Y par 에 3M·6M·9M DF 가 들어가므로 커브가
            먼저 서야 한다.

    spot_mid     USD/KRW 스팟 미드 (원). **고시일과 같은 스냅샷이어야 한다** —
                 실시간 스팟을 전영업일 스왑포인트에 붙이면 시점이 어긋난다.
    swap_points  {만기: 스왑포인트 미드}, **원 단위**, 부호 그대로 (1Y=-12.20)
    days         {만기: 스팟 → 밸류데이트 실제 경과일수}, fx_calendar 산출
    usd_rate     {만기: USD 텀 금리}, ACT/360 단리 **소수** (0.0405)
    cd_rate      CD 91일물, ACT/365 소수 — 1M·3M IRS 프록시
    irs_1y       KRW IRS 1Y 미드, ACT/365 소수
    irs_9m       있으면 6M 보간 정확도가 크게 오른다
    quarter_days 분기 지급일 일수 [92, 184, 273, 365] (fx_calendar.quarterly_
                 schedule). 없으면 days 의 3M/6M/9M/1Y 로 만든다.

    반환: {"rows": [...], "warnings": [...], "sixMonth": {...}, "meta": {...}}
    """
    warnings: list[str] = []

    if not spot_mid or spot_mid <= 0:
        raise ValueError("spot_mid 가 필요하다")

    six = interpolate_6m(days, cd_rate, irs_1y, irs_9m) if all(
        k in days for k in ("3M", "6M", "1Y")) else None

    # 만기별 원화 IRS — basis 의 상대편.
    irs_by_tenor: dict[str, tuple[float, str]] = {}
    if cd_rate is not None:
        irs_by_tenor["1M"] = (cd_rate, "CD 91D")
        irs_by_tenor["3M"] = (cd_rate, "CD 91D")
    if irs_1y is not None:
        irs_by_tenor["1Y"] = (irs_1y, "IRS 1Y")
    if six:
        irs_by_tenor["6M"] = (six["value"], "INTERPOLATED")

    # -- pass 1: 만기별 G · DF --------------------------------------------
    legs: list[dict] = []
    pillars: dict[int, float] = {}   # 일수 → DF (고시로 직접 나온 점)
    for t in TENORS:
        pt, d = swap_points.get(t), days.get(t)
        if pt is None or not d:
            continue

        u = usd_rate.get(t)
        g = growth_factor(pt, spot_mid, d, u) if u is not None else None
        leg = {
            "label": t,
            "days": d,
            "points": pt,
            "swapRate": swap_rate(pt, spot_mid, d),
            "usdRate": u,
            "growth": g,
            "df": df_from_growth(g) if g is not None else None,
            # 단리 zero — 이제 최종값이 아니라 pricer `KRW Zero` 대조용이다.
            "yieldSimple": implied_yield(pt, spot_mid, d, u) if u is not None else None,
            "crossTermBp": cross_term_bp(pt, spot_mid, d, u) if u is not None else None,
        }
        if leg["df"] is not None:
            pillars[d] = leg["df"]
        legs.append(leg)

    # -- pass 2: 분기 그리드 → par rate ------------------------------------
    if quarter_days:
        grid_days = sorted({int(q) for q in quarter_days if q and q > 0})
    else:
        grid_days = sorted({days[k] for k in ("3M", "6M", "9M", "1Y") if days.get(k)})

    pillar_list = sorted(pillars.items())

    def df_at(t: int) -> float:
        df = pillars.get(t)
        return df if df is not None else interp_df_loglinear(t, pillar_list)

    rows = []
    for leg in legs:
        d = leg["days"]
        par = None
        grid: list[int] = []
        interp_points: list[int] = []
        # 자기 만기의 DF 가 없으면(그 만기 USD 금리 결측) par 를 만들지 않는다 —
        # 보간으로 채운 DF_N 은 그 만기 스왑포인트를 안 쓴 값이라 남의 숫자다.
        if pillars and leg["df"] is not None:
            grid = payment_grid(d, grid_days)
            interp_points = [q for q in grid if q not in pillars]
            try:
                par = par_rate(grid, df_at)
            except (ValueError, ZeroDivisionError):
                log.exception("par rate failed for %s", leg["label"])

        irs, irs_src = irs_by_tenor.get(leg["label"], (None, ""))
        # STEP 4 — par - par. 양변이 분기·ACT/365 라 그냥 빼면 된다.
        basis = (par - irs) * 10000 if par is not None and irs is not None else None

        rows.append({
            **leg,
            "parRate": par,
            "payGrid": grid,
            # 그리드 점이 고시에서 나왔는지 보간인지. 9M 이 여기 걸린다.
            "pillarSource": ("고시" if not interp_points
                             else "보간 " + "·".join(f"{q}일" for q in interp_points)),
            "irs": irs,
            "irsSource": irs_src,
            "basisBp": basis,
            "interpolated": leg["label"] == "6M" and six is not None,
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
        "basis 는 par - par 이다 — FX-implied 커브를 KRW IRS 관습(분기·ACT/365)의 "
        "par swap rate 로 다시 읽어 뺐으므로 복리·지급주기 왜곡이 없다. 단리 zero "
        "(yieldSimple)는 pricer 대조용 검증열이다. 다만 USD 텀금리 소스가 basis 를 "
        "통째로 밀어 올리거나 내리므로 절대값은 소스와 함께 읽을 것.")

    return {
        "rows": rows,
        "warnings": warnings,
        "sixMonth": six,
        "meta": {
            "spot": spot_mid,
            "cdRate": cd_rate,
            "irs1y": irs_1y,
            "irs9m": irs_9m,
            "quarterDays": grid_days,
        },
    }


if __name__ == "__main__":   # python fx_implied.py — REV2 §9 기준값으로 회귀검증
    from datetime import date

    import fx_calendar

    # 개정 2판 §9 — 고시일 2026-08-10 / 스팟 2026-08-12 / 스팟 1418.90 /
    # USD Term SOFR. 데스크 pricer 와 0.02~0.08bp 로 붙는 것이 확인된 조합이다.
    QUOTE_DATE = date(2026, 8, 10)
    SPOT = 1418.90
    PTS = {"1M": -0.65, "3M": -2.35, "6M": -6.00, "1Y": -12.20}
    USD = {"1M": 0.036399, "3M": 0.037386, "6M": 0.038590, "1Y": 0.040170}
    CD, IRS_1Y = 0.029299, 0.034473
    # (일수, DF, yieldSimple, par) — 문서 §9 표.
    EXPECT = {
        "1M": (33, 0.997131, 0.031821, 0.031821),
        "3M": (92, 0.992179, 0.031272, 0.031272),
        "6M": (184, 0.984822, 0.030572, 0.030458),
        "1Y": (365, 0.969199, 0.031780, 0.031401),
    }

    sch = fx_calendar.tenor_schedule(QUOTE_DATE)
    d = {k: v["days"] for k, v in sch["tenors"].items()}
    qd = [q["days"] for q in sch["quarters"]]
    res = compute(SPOT, PTS, d, USD, cd_rate=CD, irs_1y=IRS_1Y, quarter_days=qd)

    print(f"고시일 {QUOTE_DATE}  스팟 {sch['spot']}  USD/KRW {SPOT}")
    print(f"분기 그리드 {qd}\n")
    print(f"{'만기':<5}{'일수':>5}{'포인트':>8}{'swap':>9}{'USD':>9}"
          f"{'DF':>11}{'단리zero':>11}{'par':>10}{'IRS':>9}{'basis':>10}"
          f"{'교차항':>9}  그리드")
    fails = []
    for r in res["rows"]:
        print(f"{r['label']:<5}{r['days']:>5}{r['points']:>8.2f}"
              f"{r['swapRate'] * 100:>8.2f}%{r['usdRate'] * 100:>8.4f}%"
              f"{r['df']:>11.6f}{r['yieldSimple'] * 100:>10.4f}%"
              f"{r['parRate'] * 100:>9.4f}%{r['irs'] * 100:>8.4f}%"
              f"{r['basisBp']:>9.2f}bp{r['crossTermBp']:>8.2f}bp"
              f"  {r['pillarSource']}")

        exp = EXPECT.get(r["label"])
        if not exp:
            continue
        e_days, e_df, e_simple, e_par = exp
        # par 은 0.02bp 를 준다 — §9 표의 1Y par(3.1401)은 실제 9M 포인트로
        # 만든 값이고 우리는 9M 을 log-linear 로 채우므로 문서가 예고한 대로
        # 3.1402 가 나온다 (§5.3, 0.01bp). 나머지는 0.005bp 안이다.
        for name, got, want, tol in (
            ("일수", r["days"], e_days, 0),
            ("DF", r["df"], e_df, 5e-7),
            ("단리zero", r["yieldSimple"], e_simple, 5e-7),   # 0.005bp
            ("par", r["parRate"], e_par, 2e-6),
        ):
            if abs(got - want) > tol:
                fails.append(f"{r['label']} {name}: {got} != {want}")
        # 식의 자기일관성 — 지급이 한 번뿐인 만기는 par 가 단리와 같아야 한다.
        if len(r["payGrid"]) == 1 and abs(r["parRate"] - r["yieldSimple"]) > 1e-12:
            fails.append(f"{r['label']} par != yieldSimple (단일지급인데 어긋남)")

    if res["sixMonth"]:
        six = res["sixMonth"]
        print("\n6M IRS 보간:", six["method"], "— 채택값 대비 실측(pricer) 3.1527%")
        for k, v in six["variants"].items():
            print(f"   {k:<12}{v * 100:.4f}%   실측차 {(v - 0.031527) * 10000:+.2f}bp")
        print(f"   폭 {six['spreadBp']:.1f}bp")

    print()
    for w in res["warnings"]:
        print("!", w)

    print("\n회귀검증:", "통과 (§9 기준값 일치)" if not fails else "실패")
    for f in fails:
        print("  x", f)
