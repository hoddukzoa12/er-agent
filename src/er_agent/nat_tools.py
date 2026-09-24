"""NeMo Agent Toolkit plugin: the dispatch capabilities as NAT functions.

Registered through the `nat.components` entry point, so `nat run/serve/eval`
with configs/dispatch.yml gets a Nemotron tool-calling agent that plans,
phones hospitals in waves and replans on field events.

Tools act on dispatch sessions kept in `agent.SESSIONS`; if the web UI owns a
session it registers an emitter in `agent.EMITTERS`, so steps taken by the NAT
agent still stream to the browser.
"""
from __future__ import annotations

import asyncio
import json

from pydantic import Field

from nat.builder.builder import Builder
from nat.builder.function_info import FunctionInfo
from nat.cli.register_workflow import register_function
from nat.data_models.function import FunctionBaseConfig

from . import agent, board, calls


def _emit(sid: str):
    return agent.EMITTERS.get(sid, lambda ev: None)


def _session(sid: str) -> agent.Session:
    if sid not in agent.SESSIONS:
        raise ValueError(f"unknown dispatch_id {sid}; call dispatch_plan first")
    return agent.SESSIONS[sid]


def _top(s: agent.Session, n: int = 6) -> list[dict]:
    return [{"hpid": c.hpid, "name": c.name, "tier": c.tier, "eta_min": round(c.eta_min),
             "reasons": c.reasons[:2]} for c in s.plan[:n]]


def _dumps(obj) -> str:
    return json.dumps(obj, ensure_ascii=False, default=str)


class DispatchPlanConfig(FunctionBaseConfig, name="er_dispatch_plan"):
    top_n: int = Field(default=6, description="How many ranked hospitals to return to the agent.")


@register_function(config_type=DispatchPlanConfig)
async def dispatch_plan(config: DispatchPlanConfig, builder: Builder):
    async def _plan(patient: str, lat: float, lon: float, mode: str = "ems") -> str:
        """Start a dispatch: assess the patient, pull live ER data (beds, severe-disease capability,
        restriction messages), read restrictions with Nemotron and rank hospitals.
        mode is "ems" (119 crew) or "guardian". Returns dispatch_id and the ranked hospitals."""
        s = agent.Session(mode=mode, text=patient, lat=lat, lon=lon)
        await asyncio.to_thread(agent.start, s, _emit(s.id))
        a = s.assessment.to_dict()
        return _dumps({"dispatch_id": s.id, "data_time": s.snapshot.fetched_at, "acuity": a["acuity"],
                       "required": a["required_labels"], "advise_119": a["advise_119"],
                       "hospitals": _top(s, config.top_n)})

    yield FunctionInfo.from_fn(_plan, description=_plan.__doc__)


class CallHospitalsConfig(FunctionBaseConfig, name="er_call_hospitals"):
    max_concurrency: int = Field(default=5, description="Hard cap on simultaneous lines.")
    voice: bool = Field(default=False, description="Route calls through Magpie TTS / Parakeet ASR.")


@register_function(config_type=CallHospitalsConfig)
async def call_hospitals(config: CallHospitalsConfig, builder: Builder):
    async def _call(dispatch_id: str, concurrency: int = 3) -> str:
        """Phone the ranked hospitals in waves (AI discloses itself, grounded by NeMo Guardrails).
        The best-ranked acceptance wins and every other line is cancelled. Rejections are recorded
        for this dispatch and on the shared board. Returns the accepted hospital (or none), conditionals
        and rejections."""
        s = _session(dispatch_id)
        conc = max(1, min(concurrency, config.max_concurrency))
        result = await asyncio.to_thread(calls.run_wave, s, _emit(s.id), conc, config.voice)
        return _dumps(result)

    yield FunctionInfo.from_fn(_call, description=_call.__doc__)


class MarkRejectedConfig(FunctionBaseConfig, name="er_mark_rejected"):
    pass


@register_function(config_type=MarkRejectedConfig)
async def mark_rejected(config: MarkRejectedConfig, builder: Builder):
    async def _reject(dispatch_id: str, hpid: str, reason: str) -> str:
        """Record that a hospital (by hpid from the ranked list) refused this patient."""
        s = _session(dispatch_id)
        return await asyncio.to_thread(agent._run_tool, s, "mark_rejected", {"hpid": hpid, "reason": reason},
                                       _emit(s.id))

    yield FunctionInfo.from_fn(_reject, description=_reject.__doc__)


class UpdatePatientConfig(FunctionBaseConfig, name="er_update_patient"):
    pass


@register_function(config_type=UpdatePatientConfig)
async def update_patient(config: UpdatePatientConfig, builder: Builder):
    async def _update(dispatch_id: str, note: str) -> str:
        """Add new patient information (e.g. vitals changed) and re-assess the required capabilities."""
        s = _session(dispatch_id)
        return await asyncio.to_thread(agent._run_tool, s, "update_patient", {"note": note}, _emit(s.id))

    yield FunctionInfo.from_fn(_update, description=_update.__doc__)


class MoveOriginConfig(FunctionBaseConfig, name="er_move_origin"):
    pass


@register_function(config_type=MoveOriginConfig)
async def move_origin(config: MoveOriginConfig, builder: Builder):
    async def _move(dispatch_id: str, lat: float, lon: float) -> str:
        """Update the ambulance position."""
        s = _session(dispatch_id)
        return await asyncio.to_thread(agent._run_tool, s, "move_origin", {"lat": lat, "lon": lon}, _emit(s.id))

    yield FunctionInfo.from_fn(_move, description=_move.__doc__)


class ReplanConfig(FunctionBaseConfig, name="er_replan"):
    pass


@register_function(config_type=ReplanConfig)
async def replan(config: ReplanConfig, builder: Builder):
    async def _replan(dispatch_id: str) -> str:
        """Refresh live data and re-rank hospitals for this dispatch. Call after recording changes."""
        s = _session(dispatch_id)
        await asyncio.to_thread(agent.replan, s, _emit(s.id), not s.replay)
        return _dumps({"dispatch_id": s.id, "rejected": s.rejected, "hospitals": _top(s)})

    yield FunctionInfo.from_fn(_replan, description=_replan.__doc__)


class RejectionBoardConfig(FunctionBaseConfig, name="er_rejection_board"):
    pass


@register_function(config_type=RejectionBoardConfig)
async def rejection_board(config: RejectionBoardConfig, builder: Builder):
    async def _board(query: str = "") -> str:
        """Hospitals that refused patients on recent AI inquiries (last 30 minutes), with reasons."""
        return _dumps(list(board.recent().values()))

    yield FunctionInfo.from_fn(_board, description=_board.__doc__)
