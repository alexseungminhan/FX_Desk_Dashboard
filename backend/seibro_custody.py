"""국내 투자자 미국 주식 보관금액 (SEIBro 국제거래·시장별내역, 월별).

원래는 SEIBro 화면에서 조회한 값을 ``seibro_custody.json`` 에 손으로 넣어
서빙했는데, 같은 화면의 조회 서비스를 직접 부를 수 있어 라이브로 바꿨다.
받아온 값은 수동 입력본과 소수점까지 일치하는 것을 확인했다.

조회 조건은 화면이 보내는 것과 같다: 현황구분 S_TYPE=3(보관금액),
증권종류 S_TYPE2=1(주식), 조회기간 GIGAN=2(월별), 시장 S_COUNTRY=US.

**진행 중인 달은 값이 움직인다.** 외화증권 결제는 이연분이 늦게 반영돼
화면 안내도 "당일기준 2일전까지 조회 가능"이라고 적고 있다. 최근 달이
직전 조회보다 줄어 보이는 건 오류가 아니라 아직 안 찬 것이다.

수집이 실패하면 JSON 을 폴백으로 읽는다 — 스크래핑이 깨져도 패널이
비지는 않게 하려는 것.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path

import seibro

log = logging.getLogger("seibro_custody")

KST = timezone(timedelta(hours=9))

DATA_FILE = Path(__file__).resolve().parent / "seibro_custody.json"

_W2X = "/IPORTAL/user/ovsSec/BIP_CNTS10012V.xml"
_ACTION = "getNationsSettlremaList"
_TASK = "ksd.safe.bip.cnts.OvsSec.process.OvsSecIsinPTask"
_MENU_NO = "862"

# 차트가 소화할 만큼만. 너무 길면 월 라벨이 뭉개진다.
MONTHS = 24


def _fetch_live() -> list[dict] | None:
    """SEIBro 조회 서비스에서 월별 보관금액을 받아온다."""
    today = datetime.now(tz=KST).date()
    start = (today.replace(day=1) - timedelta(days=31 * MONTHS)).strftime("%Y%m01")
    try:
        rows = seibro.query(_W2X, _ACTION, _TASK, {
            "MENU_NO": _MENU_NO, "W2XPATH": _W2X,
            "PG_START": "1", "PG_END": str(MONTHS * 2),
            "ic_start": start, "ic_end": today.strftime("%Y%m%d"),
            "S_TYPE": "3",      # 보관금액
            "S_TYPE2": "1",     # 주식
            "GIGAN": "2",       # 월별
            "S_COUNTRY": "US",
        })
    except Exception:
        log.exception("seibro custody live fetch failed — falling back to file")
        return None

    points = []
    for r in rows:
        gigan, amount = r.get("GIGAN", ""), r.get("BB1")
        if len(gigan) != 6 or not amount:
            continue
        try:
            points.append({"month": f"{gigan[:4]}-{gigan[4:]}", "amount": float(amount)})
        except ValueError:
            continue
    points.sort(key=lambda p: p["month"])
    return points[-MONTHS:] or None


def _load_file() -> list[dict]:
    try:
        raw = json.loads(DATA_FILE.read_text(encoding="utf-8"))
        return [
            {"month": p["month"], "amount": float(p["amount"])}
            for p in raw.get("points", [])
            if p.get("amount") is not None
        ]
    except Exception:
        log.exception("seibro custody file load failed")
        return []


def build() -> dict:
    """차트 렌더에 필요한 데이터·통계를 묶어 반환한다."""
    points = _fetch_live() or _load_file()

    if not points:
        return {"points": [], "stats": None}

    amounts = [p["amount"] for p in points]
    latest = points[-1]
    prev_amount = points[-2]["amount"] if len(points) >= 2 else latest["amount"]
    change = latest["amount"] - prev_amount
    change_pct = (change / prev_amount * 100) if prev_amount else 0.0

    hi_i = max(range(len(amounts)), key=amounts.__getitem__)
    lo_i = min(range(len(amounts)), key=amounts.__getitem__)

    # 창의 첫 달 대비 증감 (추세 요약용)
    first_amount = amounts[0]
    yoy_change = latest["amount"] - first_amount
    yoy_pct = (yoy_change / first_amount * 100) if first_amount else 0.0

    return {
        "title": "국내 투자자 미국 주식 보관금액",
        "source": "SEIBro 국제거래 · 시장별내역",
        "unit": "USD",
        "note": "월별 보관금액 (미국 시장 주식)",
        "points": points,
        "stats": {
            "latest": latest,
            "change": change,
            "changePct": change_pct,
            "max": {"month": points[hi_i]["month"], "amount": amounts[hi_i]},
            "min": {"month": points[lo_i]["month"], "amount": amounts[lo_i]},
            "yoyChange": yoy_change,
            "yoyPct": yoy_pct,
        },
    }
