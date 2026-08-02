"""금융투자협회 채권정보센터(KOFIA) 조회 서비스 공용 클라이언트.

kofiabond.or.kr 도 WebSquare 인데 SEIBro 와 달리 Proframe 규약을 쓴다.
화면 스크립트의 callProFrame(appId, svcId, fnId, DTO, ...) 이 아래 봉투를
만들어 하나의 서블릿에 POST 한다:

    POST /proframeWeb/XMLSERVICES/
    <message>
      <proframeHeader>
        <pfmAppName>BIS-KOFIABOND</pfmAppName>
        <pfmSvcName>BISIvtrTrdSrchSO</pfmSvcName>
        <pfmFnName>list</pfmFnName>
      </proframeHeader>
      <systemHeader></systemHeader>
      <BISComDspDatDTO><val1>20260731</val1>...</BISComDspDatDTO>
    </message>

응답은 같은 DTO 이름의 반복 블록이고, 필드가 val1..valN 이라 의미는 화면
헤더를 봐야 알 수 있다 — 각 모듈이 자기 컬럼 맵을 들고 있는 이유다.

주의: 조회 조건 코드가 숫자가 아니다. 단위는 '8'(억원), 조회구분은
'C'(거래대금)처럼 화면 셀렉트박스의 코드값을 그대로 넣어야 하며, 틀리면
에러 대신 **행은 정상이고 값만 전부 0** 으로 돌아온다. 코드값은 화면이
실제로 보내는 요청을 확인해서 맞춘 것이다.
"""
from __future__ import annotations

import logging
import re
import threading

import requests

log = logging.getLogger("kofia")

_BASE = "https://www.kofiabond.or.kr"
_SERVLET = f"{_BASE}/proframeWeb/XMLSERVICES/"
_REFERER = f"{_BASE}/websquare/websquare.html?w2xPath=/xml/main.xml"
_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0 Safari/537.36"
)

_APP = "BIS-KOFIABOND"

_lock = threading.Lock()
_session: requests.Session | None = None


def _get_session() -> requests.Session:
    global _session
    with _lock:
        if _session is not None:
            return _session
        sess = requests.Session()
        sess.headers.update({"User-Agent": _UA})
        _session = sess
    sess.get(_REFERER, timeout=10)
    return sess


def _escape(v: str) -> str:
    return v.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def query(service: str, fn: str, dto: str, params: dict[str, str],
          app: str = _APP) -> list[dict[str, str]]:
    """KOFIA 조회 1건. dto 는 요청·응답에 같은 이름을 쓰는 경우가 많아
    응답도 같은 태그로 파싱한다. 실패하면 예외를 올린다."""
    body = "".join(f"<{k}>{_escape(str(v))}</{k}>" for k, v in params.items())
    payload = (
        '<?xml version="1.0" encoding="utf-8"?>'
        "<message><proframeHeader>"
        f"<pfmAppName>{app}</pfmAppName>"
        f"<pfmSvcName>{service}</pfmSvcName>"
        f"<pfmFnName>{fn}</pfmFnName>"
        "</proframeHeader><systemHeader></systemHeader>"
        f"<{dto}>{body}</{dto}></message>"
    )

    sess = _get_session()
    r = sess.post(
        _SERVLET,
        data=payload.encode("utf-8"),
        headers={"Content-Type": "application/xml; charset=UTF-8", "Referer": _REFERER},
        timeout=25,
    )
    r.raise_for_status()

    blocks = re.findall(rf"<{dto}>(.*?)</{dto}>", r.text, re.S)
    return [dict(re.findall(r"<(\w+)>([^<]*)</\1>", b)) for b in blocks]


def num(v: str | None) -> int:
    if not v:
        return 0
    try:
        return int(v.replace(",", ""))
    except ValueError:
        return 0
