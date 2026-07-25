"""Range-selectable chart data (1D / 1W / 1M / 1Y) for the detail
popups. One module so every popup — KR/US stocks, FX, indices,
commodities, US rates, KR rates — shares the same payload shape:

    {"values": [floats], "axisL": str, "axisR": str,
     "label": str, "hiloText": str}

The frontend draws the path itself (fixed 640x132 viewBox), so only raw
values cross the wire. Sources match the rest of the app: KR stocks use
Naver's fchart bars, KR rates use the Naver 일별시세 pages, everything
else uses yfinance.
"""
from __future__ import annotations

import logging
from xml.etree import ElementTree as ET

import requests
import yfinance as yf

import kr_rates

log = logging.getLogger("chart_range")

RANGES = ("1D", "1W", "1M", "1Y")

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/120.0 Safari/537.36"
    ),
    "Referer": "https://m.stock.naver.com/",
}
_FCHART_URL = "https://fchart.stock.naver.com/sise.nhn?symbol={code}&timeframe={timeframe}&count={count}&requestType=0"

_RANGE_LABELS = {
    "1D": "당일 분봉 · Intraday",
    "1W": "1주 추이 · 1W",
    "1M": "1개월 추이 · 1M",
    "1Y": "1년 추이 · 1Y",
}


def _fmt(v: float, decimals: int, pct: bool = False) -> str:
    return f"{v:,.{decimals}f}" + ("%" if pct else "")


def _payload(values: list[float], axis_l: str, axis_r: str, label: str,
             decimals: int, pct: bool = False) -> dict | None:
    if len(values) < 2:
        return None
    hilo = f"고 {_fmt(max(values), decimals, pct)} · 저 {_fmt(min(values), decimals, pct)}"
    return {"values": values, "axisL": axis_l, "axisR": axis_r, "label": label, "hiloText": hilo}


def _decimals_for(kind: str, symbol: str, values: list[float]) -> tuple[int, bool]:
    """-> (decimals, show_percent_sign)"""
    if kind in ("rate", "krrate"):
        return 2, True
    if kind == "stock" and (symbol.endswith(".KS") or symbol.endswith(".KQ")):
        return 0, False
    if kind == "fx":
        return (4 if (values and values[-1] < 50) else 2), False
    return 2, False


# -- KR stocks: Naver fchart bars --------------------------------------

def _kr_stock_chart(code: str, rng: str) -> tuple[list[float], str, str] | None:
    timeframe, count = {
        "1D": ("minute", 420),
        "1W": ("day", 5),
        "1M": ("day", 22),
        "1Y": ("day", 250),
    }[rng]
    r = requests.get(_FCHART_URL.format(code=code, timeframe=timeframe, count=count),
                     headers=_HEADERS, timeout=8)
    r.raise_for_status()
    r.encoding = "euc-kr"
    root = ET.fromstring(r.text)
    rows = [it.get("data", "").split("|") for it in root.findall(".//item")]
    rows = [p for p in rows if len(p) >= 5 and p[4] != "null"]
    if not rows:
        return None
    if timeframe == "minute":
        last_date = rows[-1][0][:8]
        rows = [p for p in rows if p[0][:8] == last_date]
        fmt = lambda p: f"{p[0][8:10]}:{p[0][10:12]}"
    else:
        fmt = lambda p: f"{p[0][2:4]}.{p[0][4:6]}.{p[0][6:8]}"
    values = [float(p[4]) for p in rows]
    return values, fmt(rows[0]), fmt(rows[-1])


# -- everything Yahoo covers -------------------------------------------

def _yf_chart(symbol: str, rng: str) -> tuple[list[float], str, str] | None:
    period, interval = {
        "1D": ("1d", "5m"),
        "1W": ("5d", "30m"),
        "1M": ("1mo", "1d"),
        "1Y": ("1y", "1d"),
    }[rng]
    hist = yf.Ticker(symbol).history(period=period, interval=interval)
    if hist.empty and rng == "1D":
        # Weekend / market closed: fall back to the last session on file.
        hist = yf.Ticker(symbol).history(period="5d", interval="5m")
        if not hist.empty:
            last_day = hist.index[-1].date()
            hist = hist[[ts.date() == last_day for ts in hist.index]]
    closes = hist["Close"].dropna() if not hist.empty else None
    if closes is None or len(closes) < 2:
        return None
    intraday = rng in ("1D", "1W")
    fmt = "%H:%M" if rng == "1D" else ("%m/%d %H:%M" if intraday else "%y.%m.%d")
    axis_l = closes.index[0].strftime(fmt)
    axis_r = closes.index[-1].strftime("%H:%M" if rng == "1D" else fmt)
    return closes.tolist(), axis_l, axis_r


# -- KR rates: Naver daily fixings -------------------------------------

def _krrate_chart(code: str, rng: str) -> tuple[list[float], str, str] | None:
    count = {"1D": 5, "1W": 5, "1M": 22, "1Y": 250}[rng]
    rows = kr_rates.fetch_rate_history(code, count)
    if len(rows) < 2:
        return None
    values = [r["value"] for r in rows]
    return values, rows[0]["date"][2:], rows[-1]["date"][2:]


def get_chart(kind: str, symbol: str, rng: str) -> dict | None:
    if rng not in RANGES:
        return None
    try:
        if kind == "krrate":
            res = _krrate_chart(symbol, rng)
            label = "일별 고시 · Daily Fixing" if rng in ("1D", "1W") else _RANGE_LABELS[rng]
        elif kind == "stock" and (symbol.endswith(".KS") or symbol.endswith(".KQ")):
            res = _kr_stock_chart(symbol.partition(".")[0], rng)
            label = _RANGE_LABELS[rng]
        else:
            res = _yf_chart(symbol, rng)
            label = _RANGE_LABELS[rng]
    except Exception:
        log.exception("chart fetch failed: kind=%s symbol=%s range=%s", kind, symbol, rng)
        return None
    if res is None:
        return None
    values, axis_l, axis_r = res
    decimals, pct = _decimals_for(kind, symbol, values)
    return _payload(values, axis_l, axis_r, label, decimals, pct)
