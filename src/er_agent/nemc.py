"""National Emergency Medical Center (국립중앙의료원) OpenAPI client with snapshot record/replay.

Every live load is written to data/cache/snapshots/<timestamp>/ so the same
situation can be replayed later without an API key (reproducible demos).
"""
from __future__ import annotations

import datetime as dt
import json
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from pathlib import Path

import httpx

from .config import CACHE_DIR, DATA_KEY, KST, SNAPSHOT_DIR, now_kst

BASE = "http://apis.data.go.kr/B552657/ErmctInfoInqireService/"
OPS = {
    "beds": "getEmrrmRltmUsefulSckbdInfoInqire",
    "severe": "getSrsillDissAceptncPosblInfoInqire",
    "messages": "getEmrrmSrsillDissMsgInqire",
    "directory": "getEgytListInfoInqire",
}
PAGE_SIZE = 1000
DIRECTORY_TTL = dt.timedelta(hours=24)
TS_FMT = "%Y%m%d%H%M%S"


def parse_ts(value: str | None) -> dt.datetime | None:
    try:
        return dt.datetime.strptime((value or "")[:14], TS_FMT)
    except ValueError:
        return None


def fetch(op: str) -> list[dict]:
    """Fetch every page of an operation; tags are lower-cased and values stripped."""
    if not DATA_KEY:
        raise RuntimeError("data_key is missing from .env")
    items: list[dict] = []
    page = 1
    with httpx.Client(timeout=60) as client:
        while True:
            resp = client.get(
                BASE + OPS[op],
                params={"serviceKey": DATA_KEY, "pageNo": page, "numOfRows": PAGE_SIZE},
            )
            resp.raise_for_status()
            root = ET.fromstring(resp.content)
            if root.tag == "OpenAPI_ServiceResponse":  # gateway-level error (bad key, quota)
                raise RuntimeError(f"{op}: {root.findtext('.//returnAuthMsg')}")
            code = root.findtext(".//resultCode")
            if code != "00":
                raise RuntimeError(f"{op}: {code} {root.findtext('.//resultMsg')}")
            batch = [{c.tag.lower(): (c.text or "").strip() for c in it} for it in root.iter("item")]
            items.extend(batch)
            total = int(root.findtext(".//totalCount") or 0)
            if not batch or len(items) >= total:
                return items
            page += 1


@dataclass
class Snapshot:
    fetched_at: dt.datetime
    source: str
    beds: dict[str, dict]
    severe: dict[str, dict]
    messages: dict[str, list[dict]]
    directory: dict[str, dict]
    raw_counts: dict[str, int] = field(default_factory=dict)

    def active_messages(self, hpid: str) -> list[dict]:
        """Messages whose block window contains the snapshot time."""
        out = []
        for m in self.messages.get(hpid, []):
            start, end = parse_ts(m.get("symblksttdtm")), parse_ts(m.get("symblkenddtm"))
            if start and start > self.fetched_at:
                continue
            if end and end < self.fetched_at:
                continue
            out.append(m)
        return out


def _index(items: list[dict]) -> dict[str, dict]:
    return {it["hpid"]: it for it in items if it.get("hpid")}


def _group(items: list[dict]) -> dict[str, list[dict]]:
    out: dict[str, list[dict]] = {}
    for it in items:
        out.setdefault(it.get("hpid", ""), []).append(it)
    return out


def _load_directory() -> list[dict]:
    path = CACHE_DIR / "directory.json"
    if path.exists():
        age = now_kst() - dt.datetime.fromtimestamp(path.stat().st_mtime, KST).replace(tzinfo=None)
        if age < DIRECTORY_TTL:
            return json.loads(path.read_text())
    items = fetch("directory")
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(items, ensure_ascii=False))
    return items


def _build(fetched_at, source, beds, severe, messages, directory) -> Snapshot:
    return Snapshot(
        fetched_at=fetched_at,
        source=source,
        beds=_index(beds),
        severe=_index(severe),
        messages=_group(messages),
        directory=_index(directory),
        raw_counts={"beds": len(beds), "severe": len(severe), "messages": len(messages), "directory": len(directory)},
    )


def load_live(save: bool = True) -> Snapshot:
    now = now_kst().replace(microsecond=0)
    beds, severe, messages = fetch("beds"), fetch("severe"), fetch("messages")
    directory = _load_directory()
    if save:
        d = SNAPSHOT_DIR / now.strftime(TS_FMT)
        d.mkdir(parents=True, exist_ok=True)
        for name, data in (("beds", beds), ("severe", severe), ("messages", messages), ("directory", directory)):
            (d / f"{name}.json").write_text(json.dumps(data, ensure_ascii=False))
    return _build(now, "live", beds, severe, messages, directory)


def list_snapshots() -> list[str]:
    if not SNAPSHOT_DIR.exists():
        return []
    return sorted(p.name for p in SNAPSHOT_DIR.iterdir() if (p / "beds.json").exists())


def load_replay(name: str | None = None) -> Snapshot:
    names = list_snapshots()
    if not names:
        raise RuntimeError("no recorded snapshots; run `er-agent collect` first")
    name = name or names[-1]
    d: Path = SNAPSHOT_DIR / name
    parts = {p: json.loads((d / f"{p}.json").read_text()) for p in ("beds", "severe", "messages", "directory")}
    return _build(dt.datetime.strptime(name, TS_FMT), f"replay:{name}", **parts)
