"""Shared rejection board: what phone inquiries learned that public data does not show.

A rejection heard on one dispatch ("수지접합 담당의 수술 중") is visible to the
next dispatch for a while, so another crew does not dial the same hospital blind.
"""
from __future__ import annotations

import datetime as dt
import json
import threading

from .config import CACHE_DIR, now_kst

PATH = CACHE_DIR / "rejections.json"
TTL = dt.timedelta(minutes=30)
_lock = threading.Lock()


def _read() -> list[dict]:
    try:
        return json.loads(PATH.read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        return []


def record(hpid: str, name: str, reason: str, need: str, source: str = "ai-call") -> None:
    with _lock:
        items = [r for r in _read() if dt.datetime.fromisoformat(r["ts"]) > now_kst() - TTL * 4]
        items.append({"ts": now_kst().isoformat(timespec="seconds"), "hpid": hpid, "name": name,
                      "reason": reason, "need": need, "source": source})
        PATH.parent.mkdir(parents=True, exist_ok=True)
        PATH.write_text(json.dumps(items, ensure_ascii=False, indent=1))


def recent(now: dt.datetime | None = None) -> dict[str, dict]:
    """Latest rejection per hospital within the TTL window."""
    now = now or now_kst()
    out: dict[str, dict] = {}
    for r in _read():
        ts = dt.datetime.fromisoformat(r["ts"])
        if now - TTL <= ts <= now + dt.timedelta(minutes=1):
            r["minutes_ago"] = int((now - ts).total_seconds() // 60)
            out[r["hpid"]] = r
    return out
