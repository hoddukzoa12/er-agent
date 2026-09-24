"""Output rail: nothing the AI says about the patient may go beyond the dispatch record.

A prompt rule alone still let the caller invent details ("얼음 팩에 넣어 보관 중…").
`check` splits an utterance into atomic patient claims, asks Nemotron to quote
record evidence for each, and verifies the quotes in code. `enforce` runs it as a
NeMo Guardrails output rail (rails/config.yml, rails/rails.co, rails/actions.py).
"""
from __future__ import annotations

import re
import threading
from pathlib import Path

from . import llm

RAILS_DIR = Path(__file__).parent / "rails"

SAFE_LINE = "그 부분은 기록에 없어 정확하지 않습니다. 구급대원이 직접 말씀드리도록 바로 연결하겠습니다."

_TOOL = {
    "type": "function",
    "function": {
        "name": "extract_claims",
        "description": "발화를 환자에 관한 원자적 사실로 쪼개고, 각 사실의 근거를 기록에서 그대로 복사한다.",
        "parameters": {
            "type": "object",
            "properties": {
                "claims": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "claim": {"type": "string", "description": "환자에 관한 사실 하나 (예: '얼음 팩에 넣어 보관')"},
                            "evidence": {"type": "string",
                                         "description": "이 사실을 뒷받침하는 기록 원문 구절을 글자 그대로 복사. 기록에 그 구체적 내용이 없으면 빈 문자열."},
                            "supports": {"type": "boolean",
                                         "description": "evidence가 claim의 구체적 내용(수치·상태·방법)을 직접 말하는가. 더 일반적인 말이면 false."},
                        },
                        "required": ["claim", "evidence", "supports"],
                    },
                }
            },
            "required": ["claims"],
        },
    },
}

_SYSTEM = """너는 응급 이송 통화의 사실 검증기다.
AI 발화에서 '환자에 관한 사실'(증상, 활력징후, 의식, 처치, 보관 방법, 병력, 약물 등)을 가능한 한 잘게 쪼개 claims로 나열한다.
각 claim마다 그 구체적 내용을 직접 말하는 기록 원문 구절을 evidence에 글자 그대로 복사한다.
- 기록이 더 일반적인 말만 한다면(예: 기록 '보관 중' vs 발화 '얼음 팩에 넣어 보관') 그 세부 내용의 evidence는 빈 문자열이다.
- 인사, 도착 예정 시간, 감사, '확인해서 전달하겠다', '기록에 없다' 같은 절차적 말은 claim이 아니다."""


def _norm(s: str) -> str:
    return "".join(s.split()).replace("·", "").replace(",", "")


def _numbers(s: str) -> set[str]:
    return set(re.findall(r"\d+(?:[./]\d+)?", s))


def _unsupported(claim: dict, record: str) -> bool:
    text, ev = claim.get("claim", ""), claim.get("evidence", "")
    if any(m in text for m in HANDOFF_MARKERS):  # "기록에 당뇨 정보가 없습니다" is a hand-off, not a patient fact
        return False
    if not ev or _norm(ev) not in _norm(record):  # quote must literally exist in the record
        return True
    if not claim.get("supports", False):  # quote exists but is more general than the claim
        return True
    return bool(_numbers(text) - _numbers(record))  # any figure absent from the record is invented


# Clinical topics the caller must never assert unless the record mentions them.
CLINICAL_TERMS = ["의식", "혈압", "맥박", "심박", "호흡", "산소포화도", "체온", "혈당", "출혈", "지혈", "통증",
                  "병력", "당뇨", "고혈압", "기저질환", "복용", "약물", "알레르기", "임신", "마비", "경련", "구토",
                  "얼음", "거즈", "냉장", "식염수", "골절", "쇼크"]
HANDOFF_MARKERS = ["기록에 없", "정보가 없", "확인해", "확인 후", "모르", "알 수 없", "구급대원이 직접", "구급대원에게"]


def _sentences(utterance: str) -> list[str]:
    return [x for x in re.split(r"(?<=[.!?。])\s+|\n", utterance.strip()) if x.strip()]


def _handoff_only(utterance: str) -> bool:
    """Every sentence defers to the record/crew — nothing is asserted about the patient."""
    sents = _sentences(utterance)
    return bool(sents) and all(any(m in x for m in HANDOFF_MARKERS) for x in sents)


def _keyword_violations(record: str, utterance: str) -> list[str]:
    """Deterministic backstop: a sentence asserting a clinical topic absent from the record."""
    out = []
    for sent in _sentences(utterance):
        if any(m in sent for m in HANDOFF_MARKERS):
            continue
        out += [f"{t} (기록에 없음)" for t in CLINICAL_TERMS if t in sent and t not in record]
    return out


def check(record: str, utterance: str) -> tuple[bool, list[str]]:
    """Grounded only if every extracted claim quotes evidence that literally appears in the record."""
    backstop = _keyword_violations(record, utterance)
    if backstop:
        return False, backstop
    if _handoff_only(utterance):
        return True, []
    try:
        r = llm.call_tool([{"role": "system", "content": _SYSTEM},
                           {"role": "user", "content": f"환자 기록:\n{record}\n\nAI 발화:\n{utterance}"}], _TOOL)
    except Exception as exc:
        return False, [f"사실 검증 실패: {type(exc).__name__}: {str(exc)[:80]}"]  # fail closed: the safe line only hands the question to the crew
    unsupported = [c.get("claim", "") for c in r.get("claims", []) if _unsupported(c, record)]
    return not unsupported, unsupported


_rails_obj = None
_rails_lock = threading.Lock()


def _rails():
    """Build LLMRails once; construction is not thread-safe (framework registry race)."""
    global _rails_obj
    with _rails_lock:
        if _rails_obj is None:
            import logging
            from nemoguardrails import LLMRails, RailsConfig
            logging.getLogger("nemoguardrails").setLevel(logging.WARNING)  # per-event INFO logs flood the console
            _rails_obj = LLMRails(RailsConfig.from_path(str(RAILS_DIR)))
        return _rails_obj


def enforce(record: str, utterance: str, heard: str = "") -> tuple[str, list[str]]:
    """Run the NeMo Guardrails output rail on a drafted utterance.

    Returns (utterance to speak, blocked claims). Fails closed to SAFE_LINE.
    """
    try:
        res = _rails().generate(
            messages=[{"role": "context", "content": {"record": record}},
                      {"role": "user", "content": heard or "(병원 응답)"},
                      {"role": "assistant", "content": utterance}],
            options={"rails": {"input": False, "dialog": False, "retrieval": False, "output": True},
                     "output_vars": ["unsupported_claims"]},
        )
    except Exception as exc:
        return SAFE_LINE, [f"가드레일 실행 실패: {type(exc).__name__}"]
    said = res.response[0]["content"] if res.response else SAFE_LINE
    blocked = (res.output_data or {}).get("unsupported_claims") or []
    if said.strip() != utterance.strip() and not blocked:
        blocked = ["가드레일이 발화를 대체함"]
    return said, (blocked if said.strip() != utterance.strip() else [])
