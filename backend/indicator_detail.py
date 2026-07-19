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
from datetime import datetime, timezone

import requests
import yfinance as yf

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


def _spark(closes: list[float], w: int = 476, h: int = 92):
    closes = [c for c in closes if c is not None]
    if len(closes) < 2:
        return None, None, None
    lo, hi = min(closes), max(closes)
    rng = (hi - lo) or 1.0
    coords = [
        (i / (len(closes) - 1) * w, h - ((v - lo) / rng) * (h - 2) - 1)
        for i, v in enumerate(closes)
    ]
    line = " ".join(f"{'M' if i == 0 else 'L'}{x:.1f} {y:.1f}" for i, (x, y) in enumerate(coords))
    area = f"{line} L {w} {h} L 0 {h} Z"
    return {"line": line, "area": area}, lo, hi


def _intraday_chart(symbol: str):
    try:
        hist = yf.Ticker(symbol).history(period="1d", interval="5m")
        if hist.empty:
            hist = yf.Ticker(symbol).history(period="5d", interval="5m")
        return _spark(hist["Close"].dropna().tolist())
    except Exception:
        log.exception("intraday chart fetch failed for %s", symbol)
        return None, None, None


def _pct_color(pct, up, down, flat):
    color = up if (pct or 0) > 0 else down if (pct or 0) < 0 else flat
    arrow = "▲" if (pct or 0) > 0 else "▼" if (pct or 0) < 0 else "–"
    return color, arrow


def _base_result(title, subtitle, tag, price, chg, pct, color, arrow, decimals,
                  chart_label, chart, chart_lo, chart_hi, stats, news):
    return {
        "title": title,
        "subtitle": subtitle,
        "tag": tag,
        "price": _fmt(price, decimals),
        "chg": f"{chg:+,.{decimals}f}" if chg is not None else "—",
        "pct": f"{pct:+.2f}%" if pct is not None else "—",
        "color": color,
        "arrow": arrow,
        "chartLabel": chart_label,
        "chartRange": f"고 {_fmt(chart_hi, decimals)} / 저 {_fmt(chart_lo, decimals)}" if chart_hi is not None else "—",
        "chart": chart,
        "stats": stats,
        "news": news,
    }


# -- FX ----------------------------------------------------------------

def get_fx_detail(symbol: str, pair: str, name: str, up: str, down: str, flat: str) -> dict | None:
    try:
        info = yf.Ticker(symbol).get_info()
    except Exception:
        log.exception("fx info fetch failed for %s", symbol)
        return None

    price = info.get("regularMarketPrice") or info.get("bid")
    if price is None:
        return None
    prev = info.get("previousClose")
    chg = price - prev if prev else None
    pct = (chg / prev * 100) if chg is not None and prev else None
    color, arrow = _pct_color(pct, up, down, flat)
    decimals = 4 if price < 50 else 2

    chart, lo, hi = _intraday_chart(symbol)
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
                         "당일 틱 · Intraday", chart, lo, hi, stats, [])


# -- Index --------------------------------------------------------------

def get_index_detail(symbol: str, name: str, up: str, down: str, flat: str) -> dict | None:
    naver_code = _NAVER_INDEX_CODE.get(symbol)
    chart, lo, hi = _intraday_chart(symbol)

    if naver_code:
        try:
            basic = _get_naver_json(f"https://m.stock.naver.com/api/index/{naver_code}/basic")
            integ = _get_naver_json(f"https://m.stock.naver.com/api/index/{naver_code}/integration")
        except Exception:
            log.exception("naver index fetch failed for %s", symbol)
            return None

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
                             "당일 분봉 · Intraday", chart, lo, hi, stats, [])

    # International index — Yahoo only, no breadth/flow data available.
    try:
        info = yf.Ticker(symbol).get_info()
    except Exception:
        log.exception("index info fetch failed for %s", symbol)
        return None
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
                         "당일 분봉 · Intraday", chart, lo, hi, stats, [])


# -- Commodity ------------------------------------------------------------

def get_commodity_detail(symbol: str, name: str, contract: str, up: str, down: str, flat: str) -> dict | None:
    try:
        info = yf.Ticker(symbol).get_info()
    except Exception:
        log.exception("commodity info fetch failed for %s", symbol)
        return None
    price = info.get("regularMarketPrice")
    if price is None:
        return None
    prev = info.get("previousClose")
    chg = price - prev if prev else None
    pct = (chg / prev * 100) if chg is not None and prev else None
    color, arrow = _pct_color(pct, up, down, flat)

    chart, lo, hi = _intraday_chart(symbol)

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
                         "당일 · Intraday", chart, lo, hi, stats, [])


# -- Rate (our tracked UST yield-curve points) -----------------------------

def get_rate_detail(symbol: str, name: str, sub: str, up: str, down: str, flat: str) -> dict | None:
    try:
        t = yf.Ticker(symbol)
        info = t.get_info()
        hist = t.history(period="3mo", interval="1d")["Close"].dropna()
        hist_1y = t.history(period="1y", interval="1d")["Close"].dropna()
    except Exception:
        log.exception("rate info fetch failed for %s", symbol)
        return None

    price = info.get("regularMarketPrice")
    prev = info.get("previousClose")
    if price is None:
        return None
    chg = (price - prev) * 100 if prev else None  # yield pts -> bp
    pct = None
    color = up if (chg or 0) > 0 else down if (chg or 0) < 0 else flat
    arrow = "▲" if (chg or 0) > 0 else "▼" if (chg or 0) < 0 else "–"

    last30 = hist.tail(30).tolist()
    chart, lo, hi = _spark(last30)
    avg30 = sum(last30) / len(last30) if last30 else None
    hi52 = float(hist_1y.max()) if len(hist_1y) else None
    lo52 = float(hist_1y.min()) if len(hist_1y) else None

    stats = [
        {"label": "전일 수익률", "value": _fmt(prev) + "%"},
        {"label": "30일 평균", "value": _fmt(avg30) + "%" if avg30 is not None else "—"},
        {"label": "30일 최고", "value": _fmt(hi) + "%" if hi is not None else "—"},
        {"label": "30일 최저", "value": _fmt(lo) + "%" if lo is not None else "—"},
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
        "chartLabel": "최근 30영업일",
        "chartRange": f"범위 {_fmt(lo)} ~ {_fmt(hi)}" if hi is not None else "—",
        "chart": chart,
        "stats": stats,
        "news": [],
    }
