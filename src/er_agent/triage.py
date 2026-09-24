"""Patient assessment: free text → the capabilities a receiving hospital must have.

This step never diagnoses. It only estimates what the receiving emergency
department needs (severe-disease capability, pediatric beds, isolation) so the
planner can filter hospitals. A keyword fallback keeps it working without an LLM.
"""
from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field

from . import llm
from .catalog import SEVERE

MODES = {"ems": "119 구급대원", "guardian": "보호자/일반인"}
# mkioskty28 (general ER gatekeeper) is mostly unreported, so requiring it would demote nearly every hospital
SELECTABLE = {k: v for k, v in SEVERE.items() if k != "mkioskty28"}


@dataclass
class Assessment:
    summary: str
    age_group: str  # newborn | infant | child | adult | elderly | unknown
    age_years: float | None
    acuity: str  # critical | urgent | less_urgent
    required: list[str] = field(default_factory=list)  # SEVERE codes
    needs_pediatric: bool = False
    needs_isolation: bool = False
    red_flags: list[str] = field(default_factory=list)
    advise_119: bool = False
    call_script: str = ""
    rationale: str = ""
    engine: str = "llm"

    def to_dict(self) -> dict:
        d = asdict(self)
        d["required_labels"] = [SEVERE[c] for c in self.required if c in SEVERE]
        return d


_TOOL = {
    "type": "function",
    "function": {
        "name": "record_assessment",
        "description": "응급실 선정에 필요한 환자 요구사항을 기록한다 (진단 아님).",
        "parameters": {
            "type": "object",
            "properties": {
                "summary": {"type": "string", "description": "한 줄 환자 요약 (나이/성별/주증상)"},
                "age_group": {"type": "string", "enum": ["newborn", "infant", "child", "adult", "elderly", "unknown"],
                              "description": "newborn<28일, infant<1세, child<15세, elderly>=65세"},
                "age_years": {"type": ["number", "null"]},
                "acuity": {"type": "string", "enum": ["critical", "urgent", "less_urgent"],
                           "description": "critical≈KTAS1-2, urgent≈KTAS3, less_urgent≈KTAS4-5 (추정)"},
                "required": {"type": "array", "items": {"type": "string", "enum": list(SELECTABLE)},
                             "description": "수용 병원에 필요한 중증질환 처치 역량 코드. 근거가 뚜렷한 것만."},
                "needs_pediatric": {"type": "boolean", "description": "소아 응급 병상/소아 진료 필요"},
                "needs_isolation": {"type": "boolean", "description": "감염 의심으로 격리 병상 필요"},
                "red_flags": {"type": "array", "items": {"type": "string"}, "description": "생명 위협 징후"},
                "advise_119": {"type": "boolean", "description": "보호자가 직접 이동하지 말고 119를 불러야 하는가"},
                "call_script": {"type": "string",
                                "description": "병원 응급실에 수용 문의 전화할 때 읽을 2~3문장 (나이·성별·주증상·활력징후·필요 처치)"},
                "rationale": {"type": "string", "description": "요구 역량을 그렇게 판단한 근거 1~2문장"},
            },
            "required": ["summary", "age_group", "acuity", "required", "needs_pediatric", "needs_isolation",
                         "red_flags", "advise_119", "call_script", "rationale"],
        },
    },
}

_SYSTEM = """너는 응급실 선정 보조 에이전트의 '환자 평가' 단계다.
- 진단하지 않는다. 수용 병원이 갖춰야 할 역량만 추정한다.
- required에는 아래 중증질환 처치 역량 코드 중 근거가 뚜렷한 것만 넣는다. 애매하면 비워 둔다.
{codes}
- 15세 미만이면 needs_pediatric=true.
- 사용자 유형: {mode}. 보호자 모드에서 의식저하·호흡곤란·흉통·편측마비·경련 지속·대량출혈 등 위험 징후가 있으면 advise_119=true.
- 반드시 record_assessment 도구를 호출한다."""


def assess(text: str, mode: str = "ems") -> Assessment:
    if not llm.available():
        return assess_rules(text, mode)
    codes = "\n".join(f"  {k}: {v}" for k, v in SELECTABLE.items())
    try:
        args = llm.call_tool(
            [{"role": "system", "content": _SYSTEM.format(codes=codes, mode=MODES.get(mode, mode))},
             {"role": "user", "content": text}],
            _TOOL,
        )
    except Exception as exc:  # fall back rather than stall a dispatch
        a = assess_rules(text, mode)
        a.rationale = f"LLM 오류로 규칙 기반 평가 사용: {exc}"[:200]
        return a
    return Assessment(
        summary=args.get("summary", text[:60]),
        age_group=args.get("age_group", "unknown"),
        age_years=args.get("age_years"),
        acuity=args.get("acuity", "urgent"),
        required=[c for c in args.get("required", []) if c in SELECTABLE],
        needs_pediatric=bool(args.get("needs_pediatric")),
        needs_isolation=bool(args.get("needs_isolation")),
        red_flags=args.get("red_flags", []),
        advise_119=bool(args.get("advise_119")),
        call_script=args.get("call_script", ""),
        rationale=args.get("rationale", ""),
    )


# --- keyword fallback -------------------------------------------------------------
_RULES = [
    (r"흉통|가슴.*(통증|아프|조여)|심근경색|STEMI", ["mkioskty1"]),
    (r"편마비|한쪽.*(마비|힘)|말이 어눌|구음장애|뇌졸중|뇌경색|안면.*마비", ["mkioskty2"]),
    (r"지주막하|거미막하|벼락.*두통", ["mkioskty3"]),
    (r"뇌출혈|두개내출혈", ["mkioskty4"]),
    (r"대동맥.*(박리|파열)", ["mkioskty5"]),
    (r"토혈|흑색변|혈변|위장관.*출혈", ["mkioskty11"]),
    (r"진통|양수|분만|출산", ["mkioskty16"]),
    (r"화상", ["mkioskty19"]),
    (r"절단|손가락.*잘|발가락.*잘", ["mkioskty20"]),
    (r"자살|자해|정신과|환청", ["mkioskty24"]),
    (r"눈.*(외상|찔|손상)|안구", ["mkioskty25"]),
]
_RED = r"의식.*(저하|없|잃)|호흡곤란|숨.*(못|차)|청색증|경련|심정지|대량.*출혈|쇼크|편마비|흉통"


def assess_rules(text: str, mode: str = "ems") -> Assessment:
    age = None
    if m := re.search(r"(\d+)\s*(세|살)", text):
        age = float(m.group(1))
    elif m := re.search(r"(\d+)\s*개월", text):
        age = int(m.group(1)) / 12
    if "신생아" in text or re.search(r"생후\s*\d+\s*일", text):
        group = "newborn"
    elif age is None:
        group = "unknown"
    elif age < 1:
        group = "infant"
    elif age < 15:
        group = "child"
    elif age >= 65:
        group = "elderly"
    else:
        group = "adult"
    required: list[str] = []
    for pattern, codes in _RULES:
        if re.search(pattern, text):
            required += [c for c in codes if c not in required]
    reds = re.findall(_RED, text)
    return Assessment(
        summary=text[:80],
        age_group=group,
        age_years=age,
        acuity="critical" if reds else "urgent",
        required=required,
        needs_pediatric=group in ("newborn", "infant", "child"),
        needs_isolation=bool(re.search(r"결핵|홍역|수두|격리|감염병", text)),
        red_flags=reds,
        advise_119=bool(reds) and mode == "guardian",
        call_script="",
        rationale="규칙 기반 키워드 평가 (LLM 미사용)",
        engine="rules",
    )
