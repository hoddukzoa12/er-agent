"""Deterministic ranking: capabilities, bed availability, travel time, restriction verdicts → call order.

Tiers
  A  수용 가능성 높음 — every required capability reported "Y", ER beds free, no restriction concerns
  B  전화 확인 필요   — unknown capability, crowded ER, caution message, stale data
  X  제외            — capability "불가", restriction "block", or already rejected this dispatch
"""
from __future__ import annotations

import math
from dataclasses import asdict, dataclass, field

from .catalog import BEDS, EQUIPMENT, SEVERE, SEVERE_AGE_MSG, level_rank
from .nemc import Snapshot, parse_ts
from .triage import Assessment

ROAD_FACTOR = 1.35  # straight line → road distance (urban average)
SPEED_KMH = {"ems": 45, "guardian": 30}
STALE_MIN = 60


def haversine_km(lat1, lon1, lat2, lon2) -> float:
    r = 6371.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def _int(v) -> int | None:
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


@dataclass
class Candidate:
    hpid: str
    name: str
    level: str
    level_rank: int
    addr: str
    tel: str
    lat: float
    lon: float
    km: float
    eta_min: float
    beds: dict = field(default_factory=dict)  # key -> [available, baseline]
    equipment: list = field(default_factory=list)
    updated_at: str = ""
    data_age_min: float | None = None
    caps: dict = field(default_factory=dict)  # code -> Y | N | unknown
    cap_notes: dict = field(default_factory=dict)
    messages: list = field(default_factory=list)
    verdict: dict | None = None
    tier: str = "B"
    reasons: list = field(default_factory=list)
    score: float = 0.0

    def to_dict(self) -> dict:
        return asdict(self)


def nearby(snap: Snapshot, lat: float, lon: float, mode: str, k: int = 20, max_km: float = 60) -> list[Candidate]:
    """Emergency departments with live bed data, nearest first."""
    out = []
    for hpid, bed in snap.beds.items():
        info = snap.directory.get(hpid)
        if not info or not info.get("wgs84lat"):
            continue
        hlat, hlon = float(info["wgs84lat"]), float(info["wgs84lon"])
        km = haversine_km(lat, lon, hlat, hlon)
        if km > max_km:
            continue
        updated = parse_ts(bed.get("hvidate"))
        out.append(Candidate(
            hpid=hpid,
            name=info.get("dutyname") or bed.get("dutyname", hpid),
            level=info.get("dutyemclsname", ""),
            level_rank=level_rank(info.get("dutyemclsname", "")),
            addr=info.get("dutyaddr", ""),
            tel=info.get("dutytel3") or info.get("dutytel1", ""),
            lat=hlat, lon=hlon, km=round(km, 1),
            eta_min=round(km * ROAD_FACTOR / SPEED_KMH.get(mode, 40) * 60, 1),
            beds={key: [_int(bed.get(a)), _int(bed.get(b))] for key, (a, b, _) in BEDS.items()},
            equipment=[label for f, label in EQUIPMENT.items() if bed.get(f) == "Y"],
            updated_at=updated.strftime("%H:%M") if updated else "",
            data_age_min=round((snap.fetched_at - updated).total_seconds() / 60, 1) if updated else None,
            messages=snap.active_messages(hpid),
        ))
    out.sort(key=lambda c: c.km)
    return out[:k]


def attach_capabilities(cands: list[Candidate], snap: Snapshot, a: Assessment) -> None:
    for c in cands:
        sev = snap.severe.get(c.hpid, {})
        for code in a.required:
            raw = (sev.get(code) or "").strip()
            c.caps[code] = "Y" if raw == "Y" else "N" if raw in ("N", "불가능") else "unknown"
            note = sev.get(SEVERE_AGE_MSG.get(code, ""), "")
            if note and note != ".":
                c.cap_notes[code] = note


def rank(cands: list[Candidate], a: Assessment, rejected: dict[str, str], mode: str,
         recent: dict[str, dict] | None = None) -> list[Candidate]:
    """recent: shared rejection board (other dispatches' phone inquiries)."""
    recent = recent or {}
    for c in cands:
        c.reasons, c.tier = [], "A"

        def demote(reason: str):
            c.reasons.append(reason)
            if c.tier == "A":
                c.tier = "B"

        if c.hpid in rejected:
            c.tier = "X"
            c.reasons.append(f"이번 이송에서 거절됨: {rejected[c.hpid]}")
        if c.hpid in recent and c.hpid not in rejected:
            r = recent[c.hpid]
            demote(f"{r['minutes_ago']}분 전 다른 문의에서 거절: {r['reason']}")
        for code, st in c.caps.items():
            if st == "N":
                c.tier = "X"
                c.reasons.append(f"{SEVERE[code]} 수용 불가 (병원 보고)")
            elif st == "unknown":
                demote(f"{SEVERE[code]} 가능 여부 정보 없음")
        if c.verdict:
            if c.verdict["impact"] == "block":
                c.tier = "X"
                c.reasons.append(f"진료제한: {c.verdict['reason']}")
            elif c.verdict["impact"] == "caution":
                demote(f"진료제한 확인 필요: {c.verdict['reason']}")
        er_free, er_base = c.beds["er"]
        if er_free is not None and er_free <= 0:
            demote(f"응급실 과밀 (가용 {er_free}/{er_base})")
        if a.needs_pediatric:
            p_free, p_base = c.beds["peds"]
            if not p_base:
                demote("소아 전용 응급병상 없음")
            elif p_free is not None and p_free <= 0:
                demote(f"소아 병상 없음 (가용 {p_free}/{p_base})")
        if a.needs_isolation:
            iso = sum(max(c.beds[k][0] or 0, 0) for k in ("er_neg_iso", "er_iso"))
            if iso == 0:
                demote("격리 병상 없음")
        if c.data_age_min is not None and c.data_age_min > STALE_MIN:
            demote(f"병상 정보 {int(c.data_age_min)}분 전 갱신")
        # travel time dominates; severe patients get pulled toward higher-level centres
        pull = {"critical": 6, "urgent": 3}.get(a.acuity, 0)
        c.score = c.eta_min - pull * c.level_rank
        if c.tier != "X" and not c.reasons:
            c.reasons.append("요구 역량 가능 · 응급실 병상 여유")
    order = {"A": 0, "B": 1, "X": 2}
    return sorted(cands, key=lambda c: (order[c.tier], c.score))
