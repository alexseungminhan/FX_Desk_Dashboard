"""Real KOSPI/KOSDAQ top-gainers and top-losers, scraped from Naver
Finance's public ranking pages (finance.naver.com/sise/sise_rise.naver,
sise_fall.naver) — the same tables shown on the 네이버 증권 site.

Yahoo Finance has no free full-market screener for KRX, so a fixed
watchlist can never surface the actual day's biggest movers (small/mid
caps hitting limit-up, etc). Naver's ranking pages are the real,
market-wide "국내 등락 상위" and this is the only free source that
gives the same numbers a user sees on 네이버 증권.
"""
from __future__ import annotations

import logging
import re
import threading
import time

import requests
from bs4 import BeautifulSoup

log = logging.getLogger("kr_movers")

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/120.0 Safari/537.36"
    )
}

# sosok=0 -> KOSPI, sosok=1 -> KOSDAQ
_RISE_URL = "https://finance.naver.com/sise/sise_rise.naver?sosok={sosok}"
_FALL_URL = "https://finance.naver.com/sise/sise_fall.naver?sosok={sosok}"

_MARKET_SUFFIX = {"KOSPI": ".KS", "KOSDAQ": ".KQ"}
_CODE_RE = re.compile(r"code=(\d{6})")

# ---------------------------------------------------------------------------
# ETF / ETN classification
#
# Naver's ranking pages list ETFs and ETNs inline with ordinary equities
# (they trade like stocks and genuinely do top these tables — 거래대금
# 상위 on 네이버 증권 is roughly half ETFs on a typical day), so the
# rankings here carry them too and the board just labels which is which.
#
# ETFs are identified against Naver's own ETF universe endpoint rather
# than by name pattern — brand prefixes (KODEX/TIGER/ACE/RISE/…) change
# as issuers rebrand and would silently mislabel rows. ETNs are not in
# that list; they carry "ETN" in the product name by KRX naming rule.
# ---------------------------------------------------------------------------

_ETF_LIST_URL = "https://finance.naver.com/api/sise/etfItemList.nhn"
_ETF_CODES_TTL = 6 * 60 * 60  # issuer listings change at most daily

_etf_lock = threading.Lock()
_etf_codes: frozenset[str] = frozenset()
_etf_fetched_at: float = 0.0


def _fetch_etf_codes() -> frozenset[str]:
    r = requests.get(_ETF_LIST_URL, headers=_HEADERS, timeout=8)
    r.raise_for_status()
    items = r.json()["result"]["etfItemList"]
    return frozenset(str(i["itemcode"]) for i in items if i.get("itemcode"))


def etf_codes() -> frozenset[str]:
    """Cached set of every KRX ETF ticker code, from Naver's own ETF
    universe endpoint. On failure the last known set is kept (empty on a
    cold start), so a hiccup downgrades labelling — it never drops rows."""
    global _etf_codes, _etf_fetched_at
    with _etf_lock:
        fresh = time.time() - _etf_fetched_at < _ETF_CODES_TTL
        if fresh and _etf_codes:
            return _etf_codes
    try:
        codes = _fetch_etf_codes()
    except Exception:
        log.exception("naver ETF list fetch failed — keeping last known codes")
        return _etf_codes
    with _etf_lock:
        _etf_codes = codes
        _etf_fetched_at = time.time()
        return _etf_codes


def _kind_for(code: str, name: str, etfs: frozenset[str]) -> str:
    """"ETF" | "ETN" | "" (ordinary equity)."""
    if code in etfs:
        return "ETF"
    if "ETN" in name:
        return "ETN"
    return ""


def _fetch_rows(url: str, market: str, limit: int) -> list[dict]:
    r = requests.get(url, headers=_HEADERS, timeout=8)
    r.raise_for_status()
    r.encoding = "euc-kr"
    soup = BeautifulSoup(r.text, "lxml")
    table = soup.select_one("table.type_2")
    if table is None:
        return []

    etfs = etf_codes()
    out = []
    for tr in table.select("tr"):
        link = tr.select_one("a.tltle")
        if not link:
            continue
        name = link.get_text(strip=True)
        m = _CODE_RE.search(link.get("href", ""))
        if not m:
            continue
        tds = [td.get_text(strip=True) for td in tr.find_all("td")]
        # [rank, name, price, chg_text, pct, volume, bid, ask, bid_qty, ask_qty, PER, ROE]
        if len(tds) < 5:
            continue
        try:
            price = float(tds[2].replace(",", ""))
            pct = float(tds[4].replace("%", ""))
        except ValueError:
            continue
        code = m.group(1)
        out.append({
            "name": name,
            "symbol": code + _MARKET_SUFFIX[market],
            "price": price,
            "pct": pct,
            "market": market,
            "kind": _kind_for(code, name, etfs),
        })
        if len(out) >= limit:
            break
    return out


def fetch_movers(per_market_limit: int = 30, top_n: int = 10) -> tuple[list[dict], list[dict]]:
    """Return (gainers, losers), each up to `top_n` real rows sorted by
    |pct change|, merged across KOSPI + KOSDAQ. Each row carries
    market: "KOSPI" | "KOSDAQ" and a Yahoo-style `symbol` (e.g.
    "073240.KS") so the frontend can open the stock detail popup."""
    gainers: list[dict] = []
    losers: list[dict] = []
    for sosok, market in ((0, "KOSPI"), (1, "KOSDAQ")):
        try:
            gainers += _fetch_rows(_RISE_URL.format(sosok=sosok), market, per_market_limit)
        except Exception:
            log.exception("naver rise fetch failed (sosok=%s)", sosok)
        try:
            losers += _fetch_rows(_FALL_URL.format(sosok=sosok), market, per_market_limit)
        except Exception:
            log.exception("naver fall fetch failed (sosok=%s)", sosok)

    gainers.sort(key=lambda r: r["pct"], reverse=True)
    losers.sort(key=lambda r: r["pct"])
    return gainers[:top_n], losers[:top_n]


# ---------------------------------------------------------------------------
# 국내 거래 상위 (거래대금 순) — sise_quant.naver lists the whole market
# ranked by 거래량 (share volume), but its table also carries a 거래대금
# (KRW value) column per row, so the real "거래대금 상위" ranking is
# obtained by re-sorting those rows locally rather than trusting the
# page's own (volume) order.
# ---------------------------------------------------------------------------

_QUANT_URL = "https://finance.naver.com/sise/sise_quant.naver?sosok={sosok}"


def _fetch_quant_rows(sosok: int, market: str, limit: int) -> list[dict]:
    r = requests.get(_QUANT_URL.format(sosok=sosok), headers=_HEADERS, timeout=8)
    r.raise_for_status()
    r.encoding = "euc-kr"
    soup = BeautifulSoup(r.text, "lxml")
    table = soup.select_one("table.type_2")
    if table is None:
        return []

    etfs = etf_codes()
    out = []
    for tr in table.select("tr"):
        link = tr.select_one("a.tltle")
        if not link:
            continue
        name = link.get_text(strip=True)
        m = _CODE_RE.search(link.get("href", ""))
        if not m:
            continue
        tds = [td.get_text(strip=True) for td in tr.find_all("td")]
        # [rank, name, price, chg_text, pct, volume(거래량), value(거래대금 · 백만원), bid, ask, mktcap, PER, ROE]
        if len(tds) < 7:
            continue
        try:
            price = float(tds[2].replace(",", ""))
            pct = float(tds[4].replace("%", ""))
            trading_value_mm = float(tds[6].replace(",", ""))  # 백만원 단위
        except ValueError:
            continue
        code = m.group(1)
        out.append({
            "name": name,
            "symbol": code + _MARKET_SUFFIX[market],
            "price": price,
            "pct": pct,
            "tradingValueMm": trading_value_mm,
            "market": market,
            "kind": _kind_for(code, name, etfs),
        })
        if len(out) >= limit:
            break
    return out


def fetch_most_traded(per_market_limit: int = 100, top_n: int = 10) -> list[dict]:
    """Return up to `top_n` real rows sorted by 거래대금 (KRW value),
    merged across KOSPI + KOSDAQ — the "국내 거래 상위" ranking."""
    rows: list[dict] = []
    for sosok, market in ((0, "KOSPI"), (1, "KOSDAQ")):
        try:
            rows += _fetch_quant_rows(sosok, market, per_market_limit)
        except Exception:
            log.exception("naver quant fetch failed (sosok=%s)", sosok)

    rows.sort(key=lambda r: r["tradingValueMm"], reverse=True)
    return rows[:top_n]
