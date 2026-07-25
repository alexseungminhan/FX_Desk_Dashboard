"""국내 투자자 미국 주식 보관금액 (SEIBro 국제거래·시장별내역, 월별).

월 1회 갱신되는 저속 데이터라 실시간 폴링 대신 로컬 JSON을 읽어 서빙한다.
값은 ``seibro_custody.json`` 에서 관리하며, SEIBro에서 조회한 월별 보관금액으로
교체하면 된다.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path

log = logging.getLogger("fx-desk-board")

DATA_FILE = Path(__file__).resolve().parent / "seibro_custody.json"


def build() -> dict:
    """차트 렌더에 필요한 데이터·통계를 묶어 반환한다."""
    try:
        raw = json.loads(DATA_FILE.read_text(encoding="utf-8"))
        points = [
            {"month": p["month"], "amount": float(p["amount"])}
            for p in raw.get("points", [])
            if p.get("amount") is not None
        ]
    except Exception:
        log.exception("seibro custody data load failed")
        return {"points": [], "stats": None}

    if not points:
        return {"points": [], "stats": None}

    amounts = [p["amount"] for p in points]
    latest = points[-1]
    prev_amount = points[-2]["amount"] if len(points) >= 2 else latest["amount"]
    change = latest["amount"] - prev_amount
    change_pct = (change / prev_amount * 100) if prev_amount else 0.0

    hi_i = max(range(len(amounts)), key=amounts.__getitem__)
    lo_i = min(range(len(amounts)), key=amounts.__getitem__)

    # 첫 달 대비 1년 증감 (추세 요약용)
    first_amount = amounts[0]
    yoy_change = latest["amount"] - first_amount
    yoy_pct = (yoy_change / first_amount * 100) if first_amount else 0.0

    return {
        "title": raw.get("title", "국내 투자자 미국 주식 보관금액"),
        "source": raw.get("source", "SEIBro 국제거래 · 시장별내역"),
        "unit": raw.get("unit", "USD"),
        "note": raw.get("note", ""),
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
