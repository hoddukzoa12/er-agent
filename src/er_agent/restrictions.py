"""Read free-text treatment-restriction messages (진료제한 메시지) against a specific patient.

These messages carry conditions rules cannot parse ("월~금 9A~5P30' 진료가능",
"신환 수용 불가, FU 환자 가능", "가와사키환자 포함"), so Nemotron judges each
hospital's messages for this patient at this moment.
"""
from __future__ import annotations

import datetime as dt

from . import llm

WEEKDAYS = "월화수목금토일"
CHUNK = 12

_TOOL = {
    "type": "function",
    "function": {
        "name": "judge_restrictions",
        "description": "병원별 진료제한 메시지가 이 환자 수용에 미치는 영향을 판정한다.",
        "parameters": {
            "type": "object",
            "properties": {
                "verdicts": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "hpid": {"type": "string"},
                            "impact": {"type": "string", "enum": ["block", "caution", "none"]},
                            "reason": {"type": "string", "description": "판정 근거 한 문장"},
                            "quote": {"type": "string", "description": "근거가 된 메시지 원문 일부"},
                        },
                        "required": ["hpid", "impact", "reason"],
                    },
                }
            },
            "required": ["verdicts"],
        },
    },
}

_SYSTEM = """너는 응급실 진료제한 메시지를 환자 조건에 비추어 판정한다.
- block: 이 환자는 현재 시각에 이 병원에서 수용될 수 없음이 메시지로 분명함 (해당 진료과/질환/연령/시간대 불가).
- caution: 관련될 수 있으나 불분명하여 전화 확인이 필요함 (예: '문의 후 이송', 인력 부족 일반 공지).
- none: 이 환자와 무관함 (다른 진료과·다른 질환·해당 없는 연령).
- 시간 조건이 있으면 현재 시각과 요일로 계산한다.
- 모든 병원에 대해 judge_restrictions를 한 번 호출한다."""


def _fmt_now(now: dt.datetime) -> str:
    return f"{now:%Y-%m-%d} ({WEEKDAYS[now.weekday()]}) {now:%H:%M}"


def judge(patient: str, now: dt.datetime, hospitals: list[tuple[str, str, list[dict]]]) -> dict[str, dict]:
    """hospitals: (hpid, name, active messages). Returns hpid → verdict."""
    targets = [h for h in hospitals if h[2]]
    if not targets:
        return {}
    if not llm.available():
        return {hpid: {"impact": "caution", "reason": "진료제한 메시지 있음 (LLM 미사용, 전화 확인)",
                       "quote": msgs[0].get("symblkmsg", "")[:80]} for hpid, _, msgs in targets}
    out: dict[str, dict] = {}
    for i in range(0, len(targets), CHUNK):
        chunk = targets[i:i + CHUNK]
        lines = []
        for hpid, name, msgs in chunk:
            lines.append(f"[{hpid}] {name}")
            for m in msgs:
                scope = " / ".join(x for x in (m.get("symtypcodmag"), m.get("trtprtcodmag")) if x)
                lines.append(f"   - ({scope}) {m.get('symblkmsg', '')}")
        prompt = f"현재 시각: {_fmt_now(now)}\n환자: {patient}\n\n병원별 진료제한 메시지:\n" + "\n".join(lines)
        try:
            args = llm.call_tool([{"role": "system", "content": _SYSTEM}, {"role": "user", "content": prompt}], _TOOL)
            for v in args.get("verdicts", []):
                if v.get("hpid") in {h[0] for h in chunk}:
                    out[v["hpid"]] = v
        except Exception as exc:
            for hpid, _, msgs in chunk:
                out[hpid] = {"impact": "caution", "reason": f"메시지 판정 실패, 전화 확인 ({exc})"[:120],
                             "quote": msgs[0].get("symblkmsg", "")[:80]}
    return out
