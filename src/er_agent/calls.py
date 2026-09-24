"""Parallel AI inquiry calls to emergency departments, dialled in waves.

Safety design (so the agent does not flood ER phone lines):
  * at most `concurrency` lines open at once, taken in plan order
  * the first acceptance cancels every other open line politely
  * the AI discloses itself, shares only age/sex/symptoms/vitals, and never
    answers beyond the record; clinical follow-ups are handed to the crew

Hospitals are simulated desks for the demo: each one is initialised from the
real snapshot (beds, capabilities, restriction messages) plus a seeded "hidden
situation" that public data does not show — which is what a call discovers.
With voice on, every utterance goes through Magpie TTS and the hospital side
is heard back through Parakeet ASR, so the agent reacts to what it *heard*.
"""
from __future__ import annotations

import datetime as dt
import json
import random
import re
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field

from . import board, guard, llm
from .config import CACHE_DIR, now_kst
from .planner import Candidate

MAX_ROUNDS = 4
ACCEPT_GRACE_S = 6.0  # on an acceptance, wait this long for better-ranked calls still in progress
AUDIO_DIR = CACHE_DIR / "audio"

HIDDEN_REJECT = [
    "수지접합·미세수술 담당 전문의가 다른 수술 중",
    "방금 중증외상 환자 2명이 동시에 들어와 처치 여력 없음",
    "CT 장비 점검 중이라 영상 검사 지연",
    "중환자실 만실이라 입원 불가",
    "소아과 당직의가 병동 응급 상황 대응 중",
    "응급실 대기 환자 과다로 신규 중증 수용 곤란",
]
HIDDEN_CONDITIONAL = [
    "담당 전문의에게 확인 후 3분 안에 회신 가능",
    "응급실 진료는 가능하나 입원 여부는 전문의 판단 필요",
    "처치는 가능하나 병상 정리까지 20분 정도 대기 필요",
]


@dataclass
class Desk:
    """A simulated ER phone desk whose private situation drives its answers."""
    cand: Candidate
    decision: str  # accept | reject | conditional | no_answer
    situation: str
    voice: str

    def persona(self) -> str:
        c = self.cand
        er = c.beds["er"]
        msgs = "; ".join(m.get("symblkmsg", "") for m in c.messages[:4]) or "없음"
        return (f"너는 {c.name} 응급실에서 전화를 받는 간호사다. 병원 등급: {c.level}.\n"
                f"공개된 현황: 응급실 가용 {er[0]}/{er[1]}, 진료제한 공지: {msgs}\n"
                f"지금 너만 아는 내부 상황: {self.situation}\n"
                f"이 문의에 대한 결론: {DECISION_TEXT[self.decision]}\n"
                "바쁜 응급실 간호사처럼 한 번에 최대 2문장, 60자 안팎의 구어체로 말한다. 결론을 바꾸지 않는다. "
                "필요하면 환자 정보를 한 가지 되물을 수 있다(혈압, 의식 등).")


DECISION_TEXT = {
    "accept": "수용 가능. 도착 예정 시간을 묻고 받겠다고 한다.",
    "reject": "수용 불가. 이유를 짧게 말한다.",
    "conditional": "지금 확답은 어렵고 조건이 있다. 조건을 말한다.",
}


def _seeded(session_id: str, hpid: str) -> random.Random:
    return random.Random(f"{session_id}:{hpid}")


def make_desk(c: Candidate, session_id: str, voices: list[str]) -> Desk:
    """Decide the desk's private situation from its public data plus a seeded draw."""
    rnd = _seeded(session_id, c.hpid)
    voice = voices[rnd.randrange(len(voices))] if voices else ""
    if rnd.random() < 0.1:
        return Desk(c, "no_answer", "통화 중", voice)
    if c.verdict and c.verdict.get("impact") == "block":
        return Desk(c, "reject", c.verdict.get("reason", "진료제한"), voice)
    er_free = c.beds["er"][0]
    if er_free is not None and er_free <= 0 and rnd.random() < 0.8:
        return Desk(c, "reject", f"응급실 병상 포화 (가용 {er_free})", voice)
    p_accept, p_reject = (0.45, 0.35) if c.tier == "A" else (0.25, 0.5)
    x = rnd.random()
    if x < p_accept:
        return Desk(c, "accept", "수용 여력 있음", voice)
    if x < p_accept + p_reject:
        return Desk(c, "reject", rnd.choice(HIDDEN_REJECT), voice)
    return Desk(c, "conditional", rnd.choice(HIDDEN_CONDITIONAL), voice)


_CALLER_TOOL = {
    "type": "function",
    "function": {
        "name": "respond",
        "description": "병원 응답을 분류하고 AI가 다음에 할 말을 정한다.",
        "parameters": {
            "type": "object",
            "properties": {
                "status": {"type": "string", "enum": ["accept", "reject", "conditional", "continue"],
                           "description": "accept=수용 확정, reject=거절, conditional=조건부/회신 대기, continue=대화 계속"},
                "reason": {"type": "string", "description": "거절·조건의 핵심 사유 (한 구절)"},
                "say": {"type": "string", "description": "AI가 병원에 할 다음 말 (1~2문장). 종료 시 감사 인사."},
            },
            "required": ["status", "reason", "say"],
        },
    },
}

_CALLER_SYSTEM = """너는 119 구급대를 대신해 응급실에 수용 문의 전화를 거는 AI다.
환자 기록(이것 외의 사실은 말하지 않는다):
{record}
이 병원까지 예상 도착: 약 {eta}분.
규칙:
- 병원이 묻는 것 중 기록에 글자 그대로 있는 것만 답한다. 기록에 없는 항목(의식, 혈압, 출혈, 병력 등)은 절대 추정하거나 일반화하지 않는다.
  예: 기록에 '활력징후 안정'만 있고 의식이 없으면 의식은 모른다고 하고 "구급대원이 직접 말씀드리도록 연결하겠습니다"라고 한다.
- 수용 확정이면 도착 예정 시간을 알리고 감사 인사로 끝낸다 (status=accept).
- 거절이면 사유를 확인하고 감사 인사로 끝낸다 (status=reject).
- 조건부/회신 대기면 조건을 복창하고 구급대원에게 전달하겠다고 한다 (status=conditional).
- 짧은 전화 말투. 반드시 respond 도구를 호출한다."""


@dataclass
class Call:
    id: str
    desk: Desk
    rank: int = 0
    status: str = "queued"
    reason: str = ""
    transcript: list = field(default_factory=list)
    started: float = 0.0
    ended: float = 0.0


class Wave:
    def __init__(self, session, emit, concurrency: int = 3, use_voice: bool = True, max_calls: int = 12):
        self.s, self.emit = session, emit
        self.concurrency, self.use_voice, self.max_calls = concurrency, use_voice, max_calls
        self.lock = threading.Lock()
        self.stop = threading.Event()
        self.winner: Call | None = None
        self.accepts: list[Call] = []
        self.calls: list[Call] = []
        self.voices: list[str] = []
        self.audio_dir = AUDIO_DIR / session.id

    # --- utterances -----------------------------------------------------------------
    def _speak(self, call: Call, speaker: str, text: str, turn: int) -> str:
        """Emit an utterance; with voice on, synthesize it and (for the hospital) hear it back via ASR."""
        heard, audio_url = text, None
        if self.use_voice:
            from . import voice
            try:
                pcm = voice.synthesize(text, voice=call.desk.voice if speaker == "hospital" else voice.AI_VOICE)
                name = f"{call.id}_{turn:02d}_{speaker}.wav"
                voice.save_wav(self.audio_dir / name, pcm)
                audio_url = f"/audio/{self.s.id}/{name}"
                if speaker == "hospital":
                    heard = voice.transcribe(pcm) or text
            except Exception as exc:
                self.emit({"type": "step", "msg": f"음성 처리 실패, 텍스트로 진행: {exc}"[:160]})
        call.transcript.append({"speaker": speaker, "text": text, "heard": heard})
        self.emit({"type": "utter", "call": call.id, "speaker": speaker, "text": text, "heard": heard,
                   "audio": audio_url})
        return heard

    def _set(self, call: Call, status: str, reason: str = "") -> None:
        call.status, call.reason = status, reason
        c = call.desk.cand
        self.emit({"type": "call", "call": call.id, "hpid": c.hpid, "name": c.name, "status": status,
                   "reason": reason, "eta": c.eta_min, "tel": c.tel, "tier": c.tier})

    # --- one call -------------------------------------------------------------------
    def _run_call(self, call: Call) -> None:
        c, a = call.desk.cand, self.s.assessment
        call.started = time.time()
        self._set(call, "dialing")
        time.sleep(random.uniform(0.6, 1.8))  # ringing
        if self.stop.is_set():
            return self._set(call, "cancelled", "다른 병원 수용 확정")
        if call.desk.decision == "no_answer":
            time.sleep(2.5)
            return self._set(call, "no_answer", "응답 없음 (통화 중)")
        self._set(call, "talking")

        record = f"{a.summary}\n원문: {self.s.patient_text}"
        opening = (f"안녕하세요, {short(c.name)} 응급실이죠? 119 구급대를 대신한 AI 문의 전화입니다. "
                   f"{a.call_script or a.summary} 약 {round(c.eta_min)}분 거리인데 수용 가능하실까요?")
        self._speak(call, "ai", opening, 0)
        desk_msgs = [{"role": "system", "content": call.desk.persona()},
                     {"role": "user", "content": opening}]
        caller_msgs = [{"role": "system", "content": _CALLER_SYSTEM.format(record=record, eta=round(c.eta_min))},
                       {"role": "assistant", "content": opening}]
        for turn in range(1, MAX_ROUNDS + 1):
            if self.stop.is_set() and self.winner is not call:
                self._speak(call, "ai", "죄송합니다, 방금 다른 병원에서 수용이 확정되어 문의를 취소하겠습니다. 감사합니다.", turn)
                return self._set(call, "cancelled", "다른 병원 수용 확정")
            reply = " ".join((llm.chat(desk_msgs, max_tokens=200).content or "").split())
            desk_msgs.append({"role": "assistant", "content": reply})
            heard = self._speak(call, "hospital", reply, turn * 2 - 1)
            caller_msgs.append({"role": "user", "content": heard})
            r = llm.call_tool(caller_msgs, _CALLER_TOOL)
            r["say"], blocked = guard.enforce(record, r["say"], heard=heard)
            if blocked:
                self.emit({"type": "guard", "call": call.id, "blocked": blocked})
            caller_msgs.append({"role": "assistant", "content": r["say"]})
            status = r["status"]
            if status == "accept" and not self._claim(call):
                self._speak(call, "ai", "죄송합니다, 방금 다른 병원으로 확정되어 취소하겠습니다. 감사합니다.", turn * 2)
                return self._set(call, "cancelled", "다른 병원 수용 확정")
            self._speak(call, "ai", r["say"], turn * 2)
            if status != "continue":
                call.ended = time.time()
                if status == "reject":
                    self.s.rejected[c.hpid] = r["reason"]
                    board.record(c.hpid, c.name, r["reason"], ", ".join(a.to_dict()["required_labels"]) or a.summary)
                return self._set(call, status, r["reason"])
            desk_msgs.append({"role": "user", "content": r["say"]})
        self._set(call, "conditional", "통화가 길어져 구급대원 연결 필요")

    def _claim(self, call: Call) -> bool:
        """Accept only if no better-ranked call is still talking or has already accepted."""
        with self.lock:
            self.accepts.append(call)
        self._set(call, "holding", "수락 — 상위 후보 통화 확인 중")
        deadline = time.time() + ACCEPT_GRACE_S
        while time.time() < deadline:
            with self.lock:
                if self.winner is not None:
                    return False
                better_open = any(c.rank < call.rank and c.status in ("dialing", "talking") for c in self.calls)
                if not better_open:
                    break
            time.sleep(0.2)
        with self.lock:
            best = min(self.accepts, key=lambda c: c.rank)
            if self.winner is None and best is call:
                self.winner = call
                self.stop.set()
                return True
            return False

    # --- the wave ---------------------------------------------------------------------
    def run(self) -> dict:
        t0 = time.time()
        guard._rails()  # build the Guardrails runtime before worker threads race to do it
        if self.use_voice:
            from . import voice
            self.voices = [v for v in voice.korean_voices() if v != voice.AI_VOICE]
        targets = [c for c in self.s.plan if c.tier != "X" and c.hpid not in self.s.rejected][: self.max_calls]
        self.emit({"type": "step", "msg": f"AI 병렬 문의 시작 · 후보 {len(targets)}곳 · 동시 {self.concurrency}회선"
                                          f"{' · 음성(Magpie TTS / Parakeet ASR)' if self.use_voice else ''}"})
        self.calls = [Call(id=uuid.uuid4().hex[:6], desk=make_desk(c, self.s.id, self.voices), rank=i)
                      for i, c in enumerate(targets)]
        for call in self.calls:
            self._set(call, "queued")
        with ThreadPoolExecutor(max_workers=self.concurrency) as pool:
            futures = []
            for call in self.calls:
                futures.append(pool.submit(self._guarded, call))
            for f in futures:
                f.result()
        result = {
            "elapsed_s": round(time.time() - t0, 1),
            "accepted": self._summary(self.winner) if self.winner else None,
            "conditional": [self._summary(c) for c in self.calls if c.status == "conditional"],
            "rejected": [self._summary(c) for c in self.calls if c.status == "reject"],
            "dialed": sum(c.status not in ("queued", "skipped") for c in self.calls),
        }
        self.emit({"type": "wave_done", "result": result})
        self.s.log.append({"t": now_kst().isoformat(timespec="seconds"), "wave": result})
        return result

    def _guarded(self, call: Call) -> None:
        if self.stop.is_set():
            return self._set(call, "skipped", "다른 병원 수용 확정")
        try:
            self._run_call(call)
        except Exception as exc:
            self._set(call, "error", f"{type(exc).__name__}: {exc}"[:160])

    @staticmethod
    def _summary(call: Call) -> dict:
        c = call.desk.cand
        return {"hpid": c.hpid, "name": c.name, "eta": c.eta_min, "tel": c.tel, "reason": call.reason,
                "lat": c.lat, "lon": c.lon}


def short(name: str) -> str:
    """Spoken hospital name: drop the owning foundation and '의과대학부속'."""
    name = re.sub(r"^(학교법인|의료법인|재단법인|사회복지법인)?\S*?(학원|의료재단|재단)(?=\S)", "", name)
    return name.replace("의과대학부속", "").replace("의과대학", "").strip()


def run_wave(session, emit, concurrency: int = 3, use_voice: bool = True) -> dict:
    return Wave(session, emit, concurrency=concurrency, use_voice=use_voice).run()


def transcript_json(wave: Wave) -> str:
    return json.dumps([{"hospital": c.desk.cand.name, "status": c.status, "transcript": c.transcript}
                       for c in wave.calls], ensure_ascii=False, indent=1)
