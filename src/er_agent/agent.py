"""Dispatch session: plan → react to events (rejections, patient changes) → monitor live data.

The initial plan is a fixed pipeline (fast, predictable). After that Nemotron
drives: each field event is handed to a tool-calling loop that decides whether
to mark a rejection, update the patient, refresh live data and replan.
"""
from __future__ import annotations

import asyncio
import datetime as dt
import json
import os
import uuid
from dataclasses import dataclass, field
from typing import Callable

from . import board, llm, nemc, restrictions, triage
from .config import ROOT, now_kst
from .planner import Candidate, attach_capabilities, nearby, rank
from .triage import Assessment

Emit = Callable[[dict], None]
TOP_JUDGE = 15  # nearest N hospitals get their restriction messages read


@dataclass
class Session:
    mode: str
    text: str
    lat: float
    lon: float
    replay: str | None = None
    id: str = field(default_factory=lambda: uuid.uuid4().hex[:8])
    notes: list[str] = field(default_factory=list)
    rejected: dict[str, str] = field(default_factory=dict)
    assessment: Assessment | None = None
    snapshot: nemc.Snapshot | None = None
    verdicts: dict[str, dict] = field(default_factory=dict)
    judged_msgs: dict[str, str] = field(default_factory=dict)  # hpid -> message fingerprint
    plan: list[Candidate] = field(default_factory=list)
    log: list[dict] = field(default_factory=list)

    @property
    def patient_text(self) -> str:
        return self.text + ("".join(f"\n[추가] {n}" for n in self.notes))


SESSIONS: dict[str, Session] = {}
EMITTERS: dict[str, "Emit"] = {}  # session id -> UI stream, so NAT tool calls also reach the browser


def _load(s: Session) -> nemc.Snapshot:
    return nemc.load_replay(s.replay) if s.replay else nemc.load_live()


def _fingerprint(msgs: list[dict]) -> str:
    return "|".join(sorted(m.get("symblkmsg", "") for m in msgs))


def _judge_messages(s: Session, cands: list[Candidate], emit: Emit) -> None:
    """Read restriction messages for the nearest hospitals; only re-read ones that changed."""
    todo = []
    for c in cands[:TOP_JUDGE]:
        fp = _fingerprint(c.messages)
        if c.messages and s.judged_msgs.get(c.hpid) != fp:
            todo.append((c.hpid, c.name, c.messages))
            s.judged_msgs[c.hpid] = fp
        elif not c.messages:
            s.verdicts.pop(c.hpid, None)
    if todo:
        n = sum(len(t[2]) for t in todo)
        emit({"type": "step", "msg": f"진료제한 메시지 {n}건 ({len(todo)}개 병원) 해석 중 · Nemotron"})
        s.verdicts.update(restrictions.judge(s.assessment.summary + "\n" + s.patient_text, s.snapshot.fetched_at, todo))
    for c in cands:
        c.verdict = s.verdicts.get(c.hpid)


def replan(s: Session, emit: Emit, refresh: bool = True) -> list[Candidate]:
    if refresh or s.snapshot is None:
        emit({"type": "step", "msg": "실시간 병상·수용가능·진료제한 데이터 조회"})
        s.snapshot = _load(s)
    snap = s.snapshot
    cands = nearby(snap, s.lat, s.lon, s.mode)
    emit({"type": "step", "msg": f"반경 내 응급실 {len(cands)}곳 · 데이터 기준 {snap.fetched_at:%H:%M} ({snap.source})"})
    attach_capabilities(cands, snap, s.assessment)
    _judge_messages(s, cands, emit)
    s.plan = rank(cands, s.assessment, s.rejected, s.mode, recent=board.recent(snap.fetched_at if s.replay else None))
    emit({"type": "plan", "plan": plan_payload(s)})
    return s.plan


def start(s: Session, emit: Emit) -> None:
    SESSIONS[s.id] = s
    emit({"type": "session", "id": s.id})
    emit({"type": "step", "msg": "환자 상태 해석 · 필요한 처치 역량 추정 · Nemotron"})
    s.assessment = triage.assess(s.patient_text, s.mode)
    emit({"type": "assessment", "assessment": s.assessment.to_dict()})
    replan(s, emit)


def plan_payload(s: Session) -> dict:
    tiers = {"A": 0, "B": 0, "X": 0}
    for c in s.plan:
        tiers[c.tier] += 1
    return {
        "session": s.id,
        "mode": s.mode,
        "origin": [s.lat, s.lon],
        "data_time": s.snapshot.fetched_at.strftime("%Y-%m-%d %H:%M"),
        "source": s.snapshot.source,
        "tiers": tiers,
        "rejected": s.rejected,
        "assessment": s.assessment.to_dict(),
        "candidates": [c.to_dict() for c in s.plan],
    }


# --- event handling: Nemotron decides which tools to use ------------------------------------
_TOOLS = [
    {"type": "function", "function": {
        "name": "mark_rejected",
        "description": "병원이 이번 환자 수용을 거절했음을 기록한다.",
        "parameters": {"type": "object", "properties": {
            "hpid": {"type": "string"}, "reason": {"type": "string"}}, "required": ["hpid", "reason"]}}},
    {"type": "function", "function": {
        "name": "update_patient",
        "description": "환자 상태 변화나 새 정보를 기록하고 요구 역량을 다시 평가한다.",
        "parameters": {"type": "object", "properties": {
            "note": {"type": "string"}}, "required": ["note"]}}},
    {"type": "function", "function": {
        "name": "move_origin",
        "description": "구급차/환자의 현재 위치를 갱신한다.",
        "parameters": {"type": "object", "properties": {
            "lat": {"type": "number"}, "lon": {"type": "number"}}, "required": ["lat", "lon"]}}},
    {"type": "function", "function": {
        "name": "replan",
        "description": "실시간 데이터를 다시 조회하고 병원 순위를 재계산한다. 다른 도구 사용 후 마지막에 호출한다.",
        "parameters": {"type": "object", "properties": {}}}},
]

_EVENT_SYSTEM = """너는 응급실 선정 에이전트다. 현장에서 들어온 이벤트를 보고 도구로 상태를 갱신한 뒤 replan을 호출한다.
- 병원이 거절했다면 mark_rejected (후보 목록의 hpid 사용).
- 환자 상태가 바뀌었다면 update_patient.
- 위치가 바뀌었다면 move_origin.
- 상태를 바꿨다면 마지막에 replan. 단순 질문이면 도구 없이 답한다.
- 최종 답변은 한국어 2~3문장: 무엇을 반영했고 다음에 어디로 연락할지."""


def _candidate_brief(s: Session, n: int = 12) -> str:
    rows = []
    for i, c in enumerate(s.plan[:n], 1):
        rows.append(f"{i}. [{c.hpid}] {c.name} tier={c.tier} eta={c.eta_min}분 " + "; ".join(c.reasons[:2]))
    return "\n".join(rows)


NAT_CONFIG = ROOT / "configs" / "dispatch.yml"
ENGINE = os.getenv("ER_AGENT_ENGINE", "nat")  # nat | builtin


async def _run_nat(prompt: str) -> str:
    from nat.runtime.loader import load_workflow
    async with load_workflow(NAT_CONFIG) as manager:
        async with manager.session() as session:
            async with session.run(prompt) as runner:
                return await runner.result(to_type=str)


def handle_event(s: Session, text: str, emit: Emit) -> str:
    """Field event → NeMo Agent Toolkit workflow (Nemotron tool-calling agent) decides what to update."""
    s.log.append({"t": now_kst().isoformat(timespec="seconds"), "event": text})
    if ENGINE == "nat" and llm.available():
        EMITTERS[s.id] = emit
        emit({"type": "step", "msg": "NeMo Agent Toolkit 워크플로 · Nemotron이 도구 선택"})
        prompt = (f"dispatch_id={s.id} (mode={s.mode})\n환자: {s.assessment.summary}\n"
                  f"현재 후보:\n{_candidate_brief(s)}\n\n현장 이벤트: {text}")
        try:
            answer = asyncio.run(_run_nat(prompt)).strip()
        except Exception as exc:
            # Tools may already have updated the dispatch; only the final wording failed.
            emit({"type": "step", "msg": f"에이전트 최종 응답 실패({type(exc).__name__}) → 반영된 상태로 요약"})
            answer = _summary_after_event(s)
        finally:
            EMITTERS.pop(s.id, None)
        emit({"type": "message", "text": answer})
        return answer
    return _handle_event_builtin(s, text, emit)


def _summary_after_event(s: Session) -> str:
    top = next((c for c in s.plan if c.tier != "X"), None)
    parts = []
    if s.rejected:
        parts.append("거절 반영: " + ", ".join(c.name for c in s.plan if c.hpid in s.rejected))
    if s.notes:
        parts.append(f"환자 정보 반영: {s.notes[-1]} (중증도 {s.assessment.acuity})")
    if top:
        parts.append(f"다음 연락: {top.name} · 약 {round(top.eta_min)}분 · ☎ {top.tel}")
    return " / ".join(parts) or "변경 사항을 반영했습니다."


def _handle_event_builtin(s: Session, text: str, emit: Emit) -> str:
    if not llm.available():
        emit({"type": "message", "text": "LLM 키가 없어 이벤트 해석을 할 수 없습니다. 거절 버튼을 사용하세요."})
        return ""
    messages = [
        {"role": "system", "content": _EVENT_SYSTEM},
        {"role": "user", "content": f"환자: {s.assessment.summary}\n현재 후보:\n{_candidate_brief(s)}\n\n이벤트: {text}"},
    ]
    for _ in range(6):
        emit({"type": "step", "msg": "이벤트 해석 · 다음 행동 결정 · Nemotron"})
        msg = llm.chat(messages, tools=_TOOLS)
        messages.append({
            "role": "assistant",
            "content": msg.content or "",
            **({"tool_calls": [{"id": t.id, "type": "function",
                                "function": {"name": t.function.name, "arguments": t.function.arguments or "{}"}}
                               for t in msg.tool_calls]} if msg.tool_calls else {}),
        })
        if not msg.tool_calls:
            answer = (msg.content or "").strip()
            emit({"type": "message", "text": answer})
            return answer
        for call in msg.tool_calls:
            args = json.loads(call.function.arguments or "{}")
            result = _run_tool(s, call.function.name, args, emit)
            messages.append({"role": "tool", "tool_call_id": call.id, "content": result})
    emit({"type": "message", "text": "재계획을 완료했습니다."})
    return ""


def _run_tool(s: Session, name: str, args: dict, emit: Emit) -> str:
    if name == "mark_rejected":
        hpid = args.get("hpid", "")
        target = next((c for c in s.plan if c.hpid == hpid), None)
        if not target:
            return f"unknown hpid {hpid}"
        s.rejected[hpid] = args.get("reason", "거절")
        emit({"type": "step", "msg": f"거절 기록: {target.name} — {s.rejected[hpid]}"})
        return f"{target.name} 거절 기록됨"
    if name == "update_patient":
        s.notes.append(args["note"])
        emit({"type": "step", "msg": f"환자 정보 추가: {args['note']} → 요구 역량 재평가"})
        s.assessment = triage.assess(s.patient_text, s.mode)
        s.judged_msgs.clear()  # new patient picture → re-read restriction messages
        emit({"type": "assessment", "assessment": s.assessment.to_dict()})
        return "요구 역량: " + (", ".join(s.assessment.to_dict()["required_labels"]) or "없음")
    if name == "move_origin":
        s.lat, s.lon = float(args["lat"]), float(args["lon"])
        emit({"type": "step", "msg": f"위치 갱신 ({s.lat:.4f}, {s.lon:.4f})"})
        return "위치 갱신됨"
    if name == "replan":
        replan(s, emit, refresh=not s.replay)
        return _candidate_brief(s, 5)
    return f"unknown tool {name}"


def reject(s: Session, hpid: str, reason: str, emit: Emit) -> None:
    """Button path: record a rejection without an LLM round-trip, then replan."""
    s.rejected[hpid] = reason or "거절"
    emit({"type": "step", "msg": f"거절 기록: {hpid} — {s.rejected[hpid]}"})
    replan(s, emit, refresh=False)


# --- monitoring (System 1: cheap deterministic checks every tick) ----------------------------
def monitor_tick(s: Session, emit: Emit) -> None:
    before = {c.hpid: c for c in s.plan if c.tier == "A"}
    top_before = [c.hpid for c in s.plan[:3]]
    plan = replan(s, emit, refresh=not s.replay)
    after = {c.hpid: c for c in plan}
    alerts = []
    for hpid, old in before.items():
        new = after.get(hpid)
        if not new:
            continue
        if new.tier != "A":
            alerts.append(f"{new.name}: 수용 가능 → {'제외' if new.tier == 'X' else '확인 필요'} ({new.reasons[0]})")
        elif (old.beds['er'][0] or 0) > 0 and (new.beds['er'][0] or 0) <= 0:
            alerts.append(f"{new.name}: 응급실 병상 소진")
    if [c.hpid for c in plan[:3]] != top_before:
        alerts.append("추천 순서가 바뀌었습니다: " + " → ".join(c.name for c in plan[:3]))
    emit({"type": "monitor", "alerts": alerts, "time": s.snapshot.fetched_at.strftime("%H:%M:%S")})
