"""외환보유액 단기 유출예정액 — IMF 특별공표기준(SDDS) 표 II (기재부 MODS).

보드에는 외환보유액의 **잔액**을 보여 주는 자리가 없었고, 잔액만으로는
그 돈이 이미 약정으로 묶여 있는지 알 수 없다. 표 II 는 잔존만기 1년 이내에
확정적으로 나갈(들어올) 외화를 만기구간별로 갈라 놓은 것이라, 보유액 대비
실제 여력을 재는 쪽에 가깝다.

그중 데스크가 보는 줄은 사실상 **2-(b) 선물환·통화선물 매수 포지션**이다.
당국이 선물환 시장에 얼마를 걸어 두었는지가 여기로 드러난다(현물 개입은
표 I 의 잔액에만 잡히고 이 표에는 안 나온다). 그래서 그 행만 강조한다.

출처는 IMF DSBB 로 나가는 기재부 공표 페이지 한 장(약 130KB)이다:
  https://mods.go.kr/imfDsbbArcPage.es?mid=a20302010300&page_code=EIDN010

**갱신은 월 1회**다 — 매월 말 기준치를 다음 달 중순에 올린다. 실시간이
아니므로 폴링은 주 1회면 넉넉하고, 그래야 새 달 수치가 늦어도 일주일 안에
잡힌다(main.py 의 RESERVE_DRAINS_POLL_SECONDS).

표 구조는 IMF 템플릿이라 고정이다. 행 라벨은 영문 원문 그대로 오므로
_LABELS 로 한글을 입히되, 못 찾으면 원문을 그대로 내보낸다 — 문구가 조금
바뀌어도 표가 통째로 비지는 않게 하려는 것이다.
"""
from __future__ import annotations

import logging
import re

import requests
from bs4 import BeautifulSoup

log = logging.getLogger("reserves_drains")

URL = "https://mods.go.kr/imfDsbbArcPage.es?mid=a20302010300&page_code=EIDN010"

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "ko-KR,ko;q=0.9",
}

# 표 II 를 고르는 기준. caption 이 로마숫자로 시작하는 표가 네 개 있다.
_SECTION = "II."

COLUMNS = ["합계", "1개월 이내", "1~3개월", "3개월~1년"]

# 공백만 지운 소문자 원문 -> (한글 라벨, 들여쓰기 단계).
# 키에 부호를 남겨 둔 건 "trade credit (-)" 와 "trade credit(+)" 가 공백을
# 빼면 부호로만 갈리기 때문이다.
_LABELS: dict[str, tuple[str, int]] = {
    "1.foreigncurrencyloans,securities,anddeposits": ("1. 외화 대출·증권·예치금", 0),
    "outflows(-)": ("유출 (−)", 1),
    "inflows(+)": ("유입 (+)", 1),
    "principal": ("원금", 1),
    "interest": ("이자", 1),
    "2.aggregateshortandlongpositionsinforwardsandfuturesinforeigncurrenciesvis-a-visthedomesticcurrency(includingtheforwardlegofcurrencyswaps)":
        ("2. 선물환·통화선물 순포지션", 0),
    "(a)shortpositions(-)": ("(a) 매도 포지션 (−)", 1),
    "(b)longpositions(+)": ("(b) 매수 포지션 (+)", 1),
    "3.other(specify)": ("3. 기타", 0),
    "outflowsrelatedtorepos(-)": ("RP 매도 관련 유출 (−)", 1),
    "inflowsrelatedtoreverserepos(+)": ("역RP 관련 유입 (+)", 1),
    "tradecredit(-)": ("무역신용 (−)", 1),
    "tradecredit(+)": ("무역신용 (+)", 1),
    "otheraccountspayable(-)": ("기타 지급계정 (−)", 1),
    "otheraccountsreceivable(+)": ("기타 수취계정 (+)", 1),
}

# 강조할 행 — 당국의 선물환 포지션. 여기 키가 _LABELS 와 어긋나면 강조만
# 사라지고 표는 멀쩡히 나온다.
_HIGHLIGHT = "(b)longpositions(+)"

# rowspan 으로 묶인 유출/유입 머리행. 이 아래 원금·이자 행은 라벨이 둘 다
# "Principal"/"Interest" 라 문맥 없이는 구분이 안 된다.
_GROUP_KEYS = {"outflows(-)", "inflows(+)"}

# 새 절(1./2./3./(a)/(b))이 시작되면 위 문맥을 끊는다.
_SECTION_START = re.compile(r"^\s*(\d+\.|\([a-z]\))")

_MONTHS = {m: i for i, m in enumerate(
    ["Jan", "Feb", "Mar", "Apr", "May", "Jun",
     "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"], 1)}


def _key(label: str) -> str:
    return re.sub(r"\s+", "", label).lower()


def _num(v: str) -> float | None:
    v = v.replace(",", "").strip()
    if not v or v in ("-", "–", "—"):
        return None
    try:
        return float(v)
    except ValueError:
        return None


def _date(raw: str) -> str:
    """'30/Jun/2026' -> '2026.06.30'. 형식이 다르면 원문을 그대로 둔다."""
    m = re.search(r"(\d{1,2})/([A-Za-z]{3})/(\d{4})", raw)
    if not m:
        return raw.strip()
    day, mon, year = m.group(1), _MONTHS.get(m.group(2).title()), m.group(3)
    if not mon:
        return raw.strip()
    return f"{year}.{mon:02d}.{int(day):02d}"


def _stamp(soup: BeautifulSoup, prefix: str) -> str:
    """페이지 머리의 'Date of last update : …' / 'Period of Latest Data : …'."""
    for p in soup.select(".imfAnchor p"):
        text = p.get_text(" ", strip=True)
        if text.lower().startswith(prefix.lower()):
            return _date(text.split(":", 1)[1] if ":" in text else text)
    return ""


def _section_table(soup: BeautifulSoup):
    for table in soup.select("table"):
        cap = table.find("caption")
        if cap and cap.get_text(" ", strip=True).startswith(_SECTION):
            return table
    return None


def fetch_reserve_drains() -> dict | None:
    """{asOf, updated, columns, rows:[{label, level, highlight, values}]}.

    values 는 열 4개짜리 리스트이고 빈 칸은 None 이다 — 원본에서 비어 있는
    칸은 0 이 아니라 '보고 없음'이라 0.0 과 섞으면 안 된다.
    """
    try:
        r = requests.get(URL, headers=_HEADERS, timeout=20)
        r.raise_for_status()
    except Exception:
        log.exception("MODS IMF 공표 페이지를 못 받음")
        return None

    soup = BeautifulSoup(r.text, "lxml")
    table = _section_table(soup)
    if table is None:
        log.warning("표 II 를 못 찾음 — caption 문구가 바뀌었나?")
        return None

    rows: list[dict] = []
    group: tuple[str, int] | None = None      # 직전에 본 유출/유입 머리행

    for tr in table.select("tr"):
        cells = [c.get_text(" ", strip=True) for c in tr.find_all(["th", "td"])]
        if not cells:
            continue

        # 마지막 4칸이 만기구간 값이고 그 앞이 라벨이다. rowspan 이 걸린
        # 행은 라벨 칸이 둘(유출/유입 + 원금)이라 6칸으로 들어온다.
        if len(cells) >= 5:
            labels, values = cells[:-4], cells[-4:]
        elif len(cells) == 1:
            labels, values = cells, ["", "", "", ""]
        else:
            continue

        # 머리행(만기구간 헤더 두 줄)은 값 자리에 글자가 들어 있어 걸러진다.
        if any(v and _num(v) is None for v in values):
            continue

        if len(labels) >= 2 and _key(labels[0]) in _GROUP_KEYS:
            group = _LABELS.get(_key(labels[0]), (labels[0], 1))
            label_raw = labels[-1]
        else:
            label_raw = labels[-1]
            if _SECTION_START.match(label_raw):
                group = None

        key = _key(label_raw)
        name, level = _LABELS.get(key, (label_raw, 1))
        # 원금/이자는 그 자체로는 어느 방향인지 말해 주지 않는다.
        if group and key in ("principal", "interest"):
            name = f"{group[0]} {name}"

        rows.append({
            "label": name,
            "level": level,
            "highlight": key == _HIGHLIGHT,
            "values": [_num(v) for v in values],
        })

    if not rows:
        log.warning("표 II 에서 행을 하나도 못 읽음")
        return None

    return {
        "asOf": _stamp(soup, "Period of Latest Data"),
        "updated": _stamp(soup, "Date of last update"),
        "columns": COLUMNS,
        "rows": rows,
    }
