"""SEIBro(한국예탁결제원) 조회 서비스 공용 클라이언트.

seibro.or.kr 은 WebSquare 로 만들어져 있어 화면 XML은 껍데기고, 데이터는
전부 하나의 서블릿(callServletService.jsp)에 XML을 POST 해서 받는다.
화면 스크립트가 만드는 요청과 같은 모양을 그대로 보낸다:

    <reqParam action="xpirPrateList" task="ksd...BondSecnPTask">
      <STD_DT value="20260731"/>
    </reqParam>

응답은 <vector><data><result><FIELD value="..."/>...</result></data>... 형태라
필드명→값 dict 리스트로 펴서 돌려준다.

세션 쿠키가 없으면 서버가 거부하므로, 호출 전에 해당 화면을 한 번 GET 해서
JSESSIONID 를 받아둔다. 화면별 세션은 프로세스 수명 동안 재사용한다.

참고: 예탁결제원은 openplatform.seibro.or.kr 에 정식 Open API 도 운영하는데
그쪽은 "API 활용신청" 승인이 있어야 응답한다. 여기서 쓰는 경로는 웹사이트가
자기 화면을 그릴 때 쓰는 것과 동일한 공개 경로다.
"""
from __future__ import annotations

import logging
import re
import threading

import requests

log = logging.getLogger("seibro")

_BASE = "https://seibro.or.kr"
_SERVLET = f"{_BASE}/websquare/engine/proworks/callServletService.jsp"
_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0 Safari/537.36"
)

_FIELD = re.compile(r'<([A-Z_0-9]+) value="([^"]*)"')
_RESULT = re.compile(r"<result>(.*?)</result>", re.S)

_lock = threading.Lock()
_sessions: dict[str, requests.Session] = {}


def _session_for(w2x_path: str) -> requests.Session:
    """화면별 세션. 첫 호출에서 해당 화면을 GET 해 쿠키를 받아둔다."""
    with _lock:
        sess = _sessions.get(w2x_path)
        if sess is not None:
            return sess
        sess = requests.Session()
        sess.headers.update({"User-Agent": _UA})
        _sessions[w2x_path] = sess
    sess.get(f"{_BASE}/websquare/control.jsp", params={"w2xPath": w2x_path}, timeout=10)
    return sess


def _escape(v: str) -> str:
    return v.replace("&", "&amp;").replace('"', "&quot;").replace("<", "&lt;")


def query(w2x_path: str, action: str, task: str, params: dict[str, str]) -> list[dict[str, str]]:
    """SEIBro 조회 1건. 실패하면 예외를 올린다 (호출부가 직전 값을 유지하도록)."""
    body = "".join(
        f'<{k} value="{_escape(str(v))}"/>' for k, v in params.items()
    )
    payload = f'<reqParam action="{action}" task="{task}">{body}</reqParam>'

    sess = _session_for(w2x_path)
    r = sess.post(
        _SERVLET,
        data=payload.encode("utf-8"),
        headers={
            "Content-Type": "application/xml; charset=UTF-8",
            "Referer": f"{_BASE}/websquare/control.jsp?w2xPath={w2x_path}",
        },
        timeout=20,
    )
    r.raise_for_status()
    text = r.text

    if "<WARNING>" in text:
        msg = _FIELD.search(text)
        raise RuntimeError(f"SEIBro {action} rejected: {msg.group(2) if msg else '?'}")

    return [dict(_FIELD.findall(blk)) for blk in _RESULT.findall(text)]


def num(v: str | None) -> float | None:
    """SEIBro 는 값이 없을 때 빈 문자열이나 0을 준다. 0은 진짜 0이 아니라
    '해당 만기 없음'인 경우가 많아 호출부에서 구분해 쓴다."""
    if v is None or v == "":
        return None
    try:
        return float(v.replace(",", ""))
    except ValueError:
        return None
