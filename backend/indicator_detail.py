"""Detail lookup for the FX / index / commodity / rate popups
(FX Market Popups.dc.html). Each kind sources from whichever provider
actually has the real number:

- FX: Yahoo Finance has real bid/ask/52wk range for spot pairs.
- KR indices (KOSPI/KOSDAQ/KOSPI200): Naver's index API has real market
  breadth (상승/하락 종목수) and investor-flow (외국인/기관/개인
  순매수) — Yahoo has neither. International indices fall back to
  Yahoo (no breadth/flow data available for those either way).
- Commodities: Yahoo futures info has real open interest + expiry.
- Rates (our UST yield-curve points): Yahoo daily history, framed as
  the "최근 30영업일" the prototype asked for.

Every stat list below only include fields with a real backing number.
The original prototype's SOFR-panel percentiles, FX swap points/NDF,
and commodity term-structure fields have no free real-time source, so
those are dropped rather than faked — the same policy applied
throughout this app.
"""
from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

import requests
import yfinance as yf

import chart_range
import kr_rates

log = logging.getLogger("indicator_detail")

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/120.0 Safari/537.36"
    ),
    "Referer": "https://m.stock.naver.com/",
}

_NAVER_INDEX_CODE = {"^KS11": "KOSPI", "^KQ11": "KOSDAQ", "^KS200": "KPI200"}


def _fmt(v, decimals: int = 2) -> str:
    if v is None:
        return "—"
    return f"{v:,.{decimals}f}"


def _get_naver_json(url: str) -> dict:
    r = requests.get(url, headers=_HEADERS, timeout=8)
    r.raise_for_status()
    return r.json()


def _pct_color(pct, up, down, flat):
    color = up if (pct or 0) > 0 else down if (pct or 0) < 0 else flat
    arrow = "▲" if (pct or 0) > 0 else "▼" if (pct or 0) < 0 else "–"
    return color, arrow


def _base_result(title, subtitle, tag, price, chg, pct, color, arrow, decimals,
                  chart, stats, news):
    return {
        "title": title,
        "subtitle": subtitle,
        "tag": tag,
        "price": _fmt(price, decimals),
        "chg": f"{chg:+,.{decimals}f}" if chg is not None else "—",
        "pct": f"{pct:+.2f}%" if pct is not None else "—",
        "color": color,
        "arrow": arrow,
        "chart": chart,
        "stats": stats,
        "news": news,
    }


# -- FX ----------------------------------------------------------------

def get_fx_detail(symbol: str, pair: str, name: str, up: str, down: str, flat: str) -> dict | None:
    with ThreadPoolExecutor(max_workers=2) as pool:
        f_info = pool.submit(lambda: yf.Ticker(symbol).get_info())
        f_chart = pool.submit(chart_range.get_chart, "fx", symbol, "1D")
        try:
            info = f_info.result()
        except Exception:
            log.exception("fx info fetch failed for %s", symbol)
            return None
        chart = f_chart.result()

    price = info.get("regularMarketPrice") or info.get("bid")
    if price is None:
        return None
    prev = info.get("previousClose")
    chg = price - prev if prev else None
    pct = (chg / prev * 100) if chg is not None and prev else None
    color, arrow = _pct_color(pct, up, down, flat)
    decimals = 4 if price < 50 else 2

    bid, ask = info.get("bid"), info.get("ask")

    stats = [
        {"label": "매수 Bid", "value": _fmt(bid, decimals)},
        {"label": "매도 Ask", "value": _fmt(ask, decimals)},
        {"label": "스프레드", "value": _fmt(ask - bid, decimals) if bid and ask else "—"},
        {"label": "전일 종가", "value": _fmt(prev, decimals)},
        {"label": "시가", "value": _fmt(info.get("open"), decimals)},
        {"label": "52주 최고", "value": _fmt(info.get("fiftyTwoWeekHigh"), decimals)},
        {"label": "52주 최저", "value": _fmt(info.get("fiftyTwoWeekLow"), decimals)},
    ]

    return _base_result(pair, name, "SPOT", price, chg, pct, color, arrow, decimals,
                         chart, stats, [])


# -- Index --------------------------------------------------------------

def get_index_detail(symbol: str, name: str, up: str, down: str, flat: str) -> dict | None:
    naver_code = _NAVER_INDEX_CODE.get(symbol)

    if naver_code:
        with ThreadPoolExecutor(max_workers=3) as pool:
            f_chart = pool.submit(chart_range.get_chart, "index", symbol, "1D")
            f_basic = pool.submit(_get_naver_json, f"https://m.stock.naver.com/api/index/{naver_code}/basic")
            f_integ = pool.submit(_get_naver_json, f"https://m.stock.naver.com/api/index/{naver_code}/integration")
            try:
                basic = f_basic.result()
                integ = f_integ.result()
            except Exception:
                log.exception("naver index fetch failed for %s", symbol)
                return None
            chart = f_chart.result()

        price = float(basic["closePrice"].replace(",", ""))
        chg = float(basic["compareToPreviousClosePrice"].replace(",", "").replace("+", ""))
        pct = float(basic["fluctuationsRatio"])
        if basic.get("compareToPreviousPrice", {}).get("code") == "5":
            chg, pct = -abs(chg), -abs(pct)
        color, arrow = _pct_color(pct, up, down, flat)

        stat_map = {row["code"]: row.get("value") for row in integ.get("totalInfos", [])}
        updown = integ.get("upDownStockInfo", {})
        flow = integ.get("dealTrendInfo", {})

        stats = [
            {"label": "전일 종가", "value": stat_map.get("lastClosePrice", "—")},
            {"label": "시가", "value": stat_map.get("openPrice", "—")},
            {"label": "거래대금", "value": stat_map.get("accumulatedTradingValue", "—")},
            {"label": "거래량", "value": stat_map.get("accumulatedTradingVolume", "—")},
            {"label": "상승 종목", "value": updown.get("riseCount", "—"), "color": up},
            {"label": "하락 종목", "value": updown.get("fallCount", "—"), "color": down},
            {"label": "외국인", "value": (flow.get("foreignValue") or "—") + "억", "color": up if (flow.get("foreignValue") or "").startswith("+") else down},
            {"label": "기관", "value": (flow.get("institutionalValue") or "—") + "억", "color": up if (flow.get("institutionalValue") or "").startswith("+") else down},
            {"label": "개인", "value": (flow.get("personalValue") or "—") + "억", "color": up if (flow.get("personalValue") or "").startswith("+") else down},
            {"label": "52주 최고", "value": stat_map.get("highPriceOf52Weeks", "—")},
            {"label": "52주 최저", "value": stat_map.get("lowPriceOf52Weeks", "—")},
        ]
        eng_name = basic.get("stockExchangeType", {}).get("nameEng", naver_code)
        return _base_result(basic.get("stockName", name), eng_name, "KRX 지수", price, chg, pct, color, arrow, 2,
                             chart, stats, [])

    # International index — Yahoo only, no breadth/flow data available.
    with ThreadPoolExecutor(max_workers=2) as pool:
        f_info = pool.submit(lambda: yf.Ticker(symbol).get_info())
        f_chart = pool.submit(chart_range.get_chart, "index", symbol, "1D")
        try:
            info = f_info.result()
        except Exception:
            log.exception("index info fetch failed for %s", symbol)
            return None
        chart = f_chart.result()
    price = info.get("regularMarketPrice")
    if price is None:
        return None
    prev = info.get("previousClose")
    chg = price - prev if prev else None
    pct = (chg / prev * 100) if chg is not None and prev else None
    color, arrow = _pct_color(pct, up, down, flat)
    stats = [
        {"label": "전일 종가", "value": _fmt(prev)},
        {"label": "시가", "value": _fmt(info.get("open"))},
        {"label": "고가", "value": _fmt(info.get("dayHigh"))},
        {"label": "저가", "value": _fmt(info.get("dayLow"))},
        {"label": "52주 최고", "value": _fmt(info.get("fiftyTwoWeekHigh"))},
        {"label": "52주 최저", "value": _fmt(info.get("fiftyTwoWeekLow"))},
    ]
    eng_name = info.get("shortName") or info.get("longName") or symbol
    return _base_result(name, eng_name, "지수", price, chg, pct, color, arrow, 2,
                         chart, stats, [])


# -- Commodity ------------------------------------------------------------

def get_commodity_detail(symbol: str, name: str, contract: str, up: str, down: str, flat: str) -> dict | None:
    with ThreadPoolExecutor(max_workers=2) as pool:
        f_info = pool.submit(lambda: yf.Ticker(symbol).get_info())
        f_chart = pool.submit(chart_range.get_chart, "commodity", symbol, "1D")
        try:
            info = f_info.result()
        except Exception:
            log.exception("commodity info fetch failed for %s", symbol)
            return None
        chart = f_chart.result()
    price = info.get("regularMarketPrice")
    if price is None:
        return None
    prev = info.get("previousClose")
    chg = price - prev if prev else None
    pct = (chg / prev * 100) if chg is not None and prev else None
    color, arrow = _pct_color(pct, up, down, flat)

    expiry = info.get("expireDate")
    expiry_str = datetime.fromtimestamp(expiry, tz=timezone.utc).strftime("%Y-%m") if expiry else "—"

    stats = [
        {"label": "만기 (Expiry)", "value": expiry_str},
        {"label": "전일 정산", "value": _fmt(prev)},
        {"label": "시가", "value": _fmt(info.get("open"))},
        {"label": "고가", "value": _fmt(info.get("dayHigh"))},
        {"label": "저가", "value": _fmt(info.get("dayLow"))},
        {"label": "거래량", "value": f"{info['volume']:,}" if info.get("volume") else "—"},
        {"label": "미결제약정", "value": f"{info['openInterest']:,}" if info.get("openInterest") else "—"},
        {"label": "52주 최고", "value": _fmt(info.get("fiftyTwoWeekHigh"))},
        {"label": "52주 최저", "value": _fmt(info.get("fiftyTwoWeekLow"))},
    ]
    return _base_result(name, contract, "근월 선물", price, chg, pct, color, arrow, 2,
                         chart, stats, [])


# -- Rate (our tracked UST yield-curve points) -----------------------------

def get_rate_detail(symbol: str, name: str, sub: str, up: str, down: str, flat: str) -> dict | None:
    with ThreadPoolExecutor(max_workers=4) as pool:
        f_info = pool.submit(lambda: yf.Ticker(symbol).get_info())
        f_hist = pool.submit(lambda: yf.Ticker(symbol).history(period="3mo", interval="1d")["Close"].dropna())
        f_hist_1y = pool.submit(lambda: yf.Ticker(symbol).history(period="1y", interval="1d")["Close"].dropna())
        f_chart = pool.submit(chart_range.get_chart, "rate", symbol, "1D")
        try:
            info = f_info.result()
            hist = f_hist.result()
            hist_1y = f_hist_1y.result()
        except Exception:
            log.exception("rate info fetch failed for %s", symbol)
            return None
        chart = f_chart.result()

    price = info.get("regularMarketPrice")
    prev = info.get("previousClose")
    if price is None:
        return None
    chg = (price - prev) * 100 if prev else None  # yield pts -> bp
    pct = None
    color = up if (chg or 0) > 0 else down if (chg or 0) < 0 else flat
    arrow = "▲" if (chg or 0) > 0 else "▼" if (chg or 0) < 0 else "–"

    last30 = hist.tail(30).tolist()
    avg30 = sum(last30) / len(last30) if last30 else None
    hi30 = max(last30) if last30 else None
    lo30 = min(last30) if last30 else None
    hi52 = float(hist_1y.max()) if len(hist_1y) else None
    lo52 = float(hist_1y.min()) if len(hist_1y) else None

    stats = [
        {"label": "전일 수익률", "value": _fmt(prev) + "%"},
        {"label": "30일 평균", "value": _fmt(avg30) + "%" if avg30 is not None else "—"},
        {"label": "30일 최고", "value": _fmt(hi30) + "%" if hi30 is not None else "—"},
        {"label": "30일 최저", "value": _fmt(lo30) + "%" if lo30 is not None else "—"},
        {"label": "52주 최고", "value": _fmt(hi52) + "%" if hi52 is not None else "—"},
        {"label": "52주 최저", "value": _fmt(lo52) + "%" if lo52 is not None else "—"},
    ]

    return {
        "title": name,
        "subtitle": sub,
        "tag": "US Treasury",
        "price": _fmt(price) + "%",
        "chg": f"{chg:+.0f}bp" if chg is not None else "—",
        "pct": f"전일 {_fmt(prev)}%" if prev is not None else "—",
        "color": color,
        "arrow": arrow,
        "chart": chart,
        "stats": stats,
        "news": [],
    }


# -- KR rate (네이버 국내시장금리 daily fixings) ---------------------------

def get_krrate_detail(code: str, name: str, up: str, down: str, flat: str) -> dict | None:
    with ThreadPoolExecutor(max_workers=2) as pool:
        f_rows = pool.submit(kr_rates.fetch_kr_rates)
        f_hist = pool.submit(kr_rates.fetch_rate_history, code, 22)
        try:
            rows = f_rows.result()
        except Exception:
            log.exception("kr rates fetch failed for %s", code)
            return None
        try:
            hist = f_hist.result()
        except Exception:
            log.exception("kr rate history fetch failed for %s", code)
            hist = []
    row = next((r for r in rows if r["code"] == code), None)
    if row is None:
        return None

    value, change = row["value"], row["change"]
    color = up if change > 0 else down if change < 0 else flat
    arrow = "▲" if change > 0 else "▼" if change < 0 else "–"
    vals = [h["value"] for h in hist]
    avg30 = sum(vals) / len(vals) if vals else None

    # 1D 차트(최근 5영업일)는 이미 받아둔 이력에서 바로 만든다 —
    # chart_range를 다시 부르면 같은 페이지를 한 번 더 긁게 된다.
    chart = None
    last5 = hist[-5:]
    if len(last5) >= 2:
        vals5 = [h["value"] for h in last5]
        chart = {
            "values": vals5,
            "axisL": last5[0]["date"][2:],
            "axisR": last5[-1]["date"][2:],
            "label": "일별 고시 · Daily Fixing",
            "hiloText": f"고 {max(vals5):,.2f}% · 저 {min(vals5):,.2f}%",
        }

    stats = [
        {"label": "전일 고시", "value": _fmt(vals[-2]) + "%" if len(vals) >= 2 else "—"},
        {"label": "1개월 평균", "value": _fmt(avg30) + "%" if avg30 is not None else "—"},
        {"label": "1개월 최고", "value": _fmt(max(vals)) + "%" if vals else "—"},
        {"label": "1개월 최저", "value": _fmt(min(vals)) + "%" if vals else "—"},
        {"label": "1개월 변화", "value": f"{(value - vals[0]) * 100:+.0f}bp" if vals else "—"},
        {"label": "기준일", "value": hist[-1]["date"] if hist else "—"},
    ]

    return {
        "title": row["name"] if not name else name,
        "subtitle": "국내 시장금리 · 일별 고시",
        "tag": "KR 금리",
        "price": _fmt(value) + "%",
        "chg": f"{change * 100:+.0f}bp",
        "pct": f"{change:+.2f}%p" if change else "보합",
        "color": color,
        "arrow": arrow,
        "chart": chart,
        "stats": stats,
        "news": [],
    }
