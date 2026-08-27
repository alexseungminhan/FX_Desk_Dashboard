"""마지막으로 성공한 수집 결과를 디스크에 떠 두는 곳.

바깥 소스가 잠깐 대답을 안 할 때 화면이 비지 않게 하려는 것뿐이다. 두 군데가
쓴다:

* 국내 순위(등락·거래대금) — 네이버의 장중 순위는 개장(09:00) 전에는 표가
  통째로 비어서 온다. 수집 창은 08:30 부터라 그 30분은 "받으러 가지만 받을 게
  없는" 구간이고, 하필 그때 서버를 새로 띄우면 기동 시드까지 빈 표를 받는다.
* 외환보유액 단기 유출예정액 — 월 1회 갱신이라 폴링이 주 1회다. 기동 시
  한 번 타임아웃 나면 다음 시도가 일주일 뒤라 그동안 패널이 비어 있었다.

읽어 갈 때는 언제 것인지(`session`)를 같이 돌려준다 — 오늘 값인 척하지
않는 게 이 파일의 요점이다. 화면에 "08.26 기준" 으로 적힌다.
"""
from __future__ import annotations

import json
import logging
import os
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

log = logging.getLogger("disk_cache")

KST = timezone(timedelta(hours=9))
CACHE_DIR = Path(__file__).resolve().parent / "cache"


def _path(name: str) -> Path:
    return CACHE_DIR / f"{name}.json"


def load(name: str, max_age_days: int) -> dict | None:
    """떠 둔 값. 없거나 `max_age_days` 보다 묵었으면 None.

    묵은 값을 끊는 건 "직전 영업일" 과 "그냥 옛날 값" 이 다르기 때문이다.
    월 단위로 갱신되는 표는 max_age_days 를 넉넉히 준다.
    """
    try:
        raw = json.loads(_path(name).read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None
    except Exception:
        log.exception("%s 캐시를 읽지 못했다 — 없는 것으로 친다", name)
        return None

    session = raw.get("session")
    if not session:
        return None
    try:
        age = datetime.now(tz=KST).date() - datetime.strptime(session, "%Y-%m-%d").date()
    except ValueError:
        return None
    if age > timedelta(days=max_age_days):
        log.info("%s 캐시가 %s일 묵었다 (%s) — 쓰지 않는다", name, age.days, session)
        return None
    return raw


def save(name: str, payload: dict) -> None:
    """`payload` 를 오늘 날짜로 떠 둔다. 내용이 그대로면 쓰지 않는다 —
    폴링은 계속 도는데 장 마감 뒤에는 값이 안 바뀐다."""
    body_obj = dict(payload)
    body_obj["session"] = datetime.now(tz=KST).strftime("%Y-%m-%d")
    body_obj["savedAt"] = datetime.now(tz=KST).isoformat(timespec="seconds")

    try:
        old = json.loads(_path(name).read_text(encoding="utf-8"))
        # savedAt 은 매번 달라지니 빼고 견준다
        if all(old.get(k) == v for k, v in body_obj.items() if k != "savedAt"):
            return
    except FileNotFoundError:
        pass
    except Exception:
        pass  # 못 읽으면 그냥 새로 쓴다

    body = json.dumps(body_obj, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
    # 쓰다 죽어도 반쪽짜리 파일이 남지 않게 옆에 쓰고 갈아 끼운다
    try:
        CACHE_DIR.mkdir(exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=str(CACHE_DIR), prefix=f".{name}_", suffix=".json")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write(body)
            os.replace(tmp, _path(name))
        except Exception:
            os.unlink(tmp)
            raise
    except Exception:
        log.exception("%s 캐시를 쓰지 못했다 — 화면에는 지장 없다", name)
