"""Tiny in-process TTL cache for detail/chart/search responses.

Popup detail payloads aggregate several slow upstream calls (Naver,
Yahoo). Prices on the board tick every 10s anyway, so serving a
seconds-old popup payload is indistinguishable to the user — but it
makes reopening a popup (or re-running a search) instant.
"""
from __future__ import annotations

import threading
import time
from typing import Any, Callable

_lock = threading.Lock()
_store: dict[str, tuple[float, Any]] = {}
_MAX_ENTRIES = 500


def get_or_fetch(key: str, ttl: float, fn: Callable[[], Any]) -> Any:
    now = time.time()
    with _lock:
        hit = _store.get(key)
        if hit is not None and now - hit[0] < ttl:
            return hit[1]
    value = fn()
    # Only cache real payloads — a transient upstream failure shouldn't
    # pin "no data" for the whole TTL.
    if value is not None:
        with _lock:
            if len(_store) >= _MAX_ENTRIES:
                oldest = min(_store, key=lambda k: _store[k][0])
                _store.pop(oldest, None)
            _store[key] = (time.time(), value)
    return value
