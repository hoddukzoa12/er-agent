"""FastAPI app: NDJSON streams so the UI shows each agent step as it happens."""
from __future__ import annotations

import json
import queue
import threading
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel

from . import agent, calls, llm, nemc
from .config import VOICE_ENABLED

WEB = Path(__file__).parent / "web"
app = FastAPI(title="ER Dispatch Agent")

PRESETS = [
    {"name": "서울 광화문", "lat": 37.5714, "lon": 126.9768},
    {"name": "서울 강남역", "lat": 37.4979, "lon": 127.0276},
    {"name": "서울 노원역", "lat": 37.6552, "lon": 127.0613},
    {"name": "경기 수원역", "lat": 37.2657, "lon": 127.0000},
    {"name": "대전역", "lat": 36.3326, "lon": 127.4342},
    {"name": "대구 동대구역", "lat": 35.8797, "lon": 128.6285},
    {"name": "부산 서면", "lat": 35.1578, "lon": 129.0597},
    {"name": "강원 춘천", "lat": 37.8813, "lon": 127.7298},
]


def _stream(work) -> StreamingResponse:
    """Run `work(emit)` in a thread and stream every emitted event as one JSON line."""
    q: queue.Queue = queue.Queue()

    def run():
        try:
            work(q.put)
        except Exception as exc:  # surface failures in the UI instead of a dead stream
            q.put({"type": "error", "msg": f"{type(exc).__name__}: {exc}"})
        finally:
            q.put(None)

    threading.Thread(target=run, daemon=True).start()

    def gen():
        while (item := q.get()) is not None:
            yield json.dumps(item, ensure_ascii=False, default=str) + "\n"

    return StreamingResponse(gen(), media_type="application/x-ndjson")


def _session(sid: str) -> agent.Session:
    if sid not in agent.SESSIONS:
        raise HTTPException(404, "session not found")
    return agent.SESSIONS[sid]


class PlanReq(BaseModel):
    mode: str = "ems"
    text: str
    lat: float
    lon: float
    replay: str | None = None


class EventReq(BaseModel):
    session: str
    text: str


class RejectReq(BaseModel):
    session: str
    hpid: str
    reason: str = ""


class SessionReq(BaseModel):
    session: str


class CallsReq(BaseModel):
    session: str
    concurrency: int = 3
    voice: bool = True


@app.get("/")
def index():
    return FileResponse(WEB / "index.html")


@app.get("/api/meta")
def meta():
    return {"presets": PRESETS, "snapshots": nemc.list_snapshots()[-20:], "llm": llm.available(),
            "voice": VOICE_ENABLED}


@app.get("/api/diag")
def diag(full: bool = False):
    """Reachability of every upstream the agent needs, with timings (deployment health check)."""
    import socket
    import time

    import httpx

    from .config import DATA_KEY, NVIDIA_API_KEY

    def probe(name, fn):
        t = time.time()
        try:
            detail = fn()
            return {"name": name, "ok": True, "ms": int((time.time() - t) * 1000), "detail": detail}
        except Exception as exc:
            return {"name": name, "ok": False, "ms": int((time.time() - t) * 1000),
                    "detail": f"{type(exc).__name__}: {exc}"[:200]}

    def nim():
        r = httpx.post("https://integrate.api.nvidia.com/v1/chat/completions", timeout=20,
                       headers={"Authorization": f"Bearer {NVIDIA_API_KEY}"},
                       json={"model": "nvidia/nemotron-3-super-120b-a12b", "max_tokens": 5,
                             "messages": [{"role": "user", "content": "ping"}],
                             "chat_template_kwargs": {"enable_thinking": False}})
        return f"HTTP {r.status_code}"

    def nemc_api():
        r = httpx.get("http://apis.data.go.kr/B552657/ErmctInfoInqireService/getEgytListInfoInqire", timeout=20,
                      params={"serviceKey": DATA_KEY, "numOfRows": 1})
        return f"HTTP {r.status_code} {r.text[:60]!r}"

    def grpc_tcp():
        socket.create_connection(("grpc.nvcf.nvidia.com", 443), timeout=10).close()
        return "tcp connected"

    def nim_tool_call():  # the exact call path the triage step uses, without retries
        from . import llm, triage
        return triage.assess("45세 남성 손가락 절단, 활력징후 안정").engine

    probes = [probe("nim", nim), probe("nemc", nemc_api), probe("speech_grpc", grpc_tcp)]
    if full:
        probes.append(probe("nim_tool_call", nim_tool_call))
    return {"keys": {"nvidia": bool(NVIDIA_API_KEY), "data": bool(DATA_KEY)}, "probes": probes}


@app.post("/api/plan")
def plan(req: PlanReq):
    s = agent.Session(mode=req.mode, text=req.text, lat=req.lat, lon=req.lon, replay=req.replay or None)
    return _stream(lambda emit: agent.start(s, emit))


@app.post("/api/event")
def event(req: EventReq):
    s = _session(req.session)
    return _stream(lambda emit: agent.handle_event(s, req.text, emit))


@app.post("/api/reject")
def reject(req: RejectReq):
    s = _session(req.session)
    return _stream(lambda emit: agent.reject(s, req.hpid, req.reason, emit))


@app.post("/api/monitor")
def monitor(req: SessionReq):
    s = _session(req.session)
    return _stream(lambda emit: agent.monitor_tick(s, emit))


@app.post("/api/calls")
def start_calls(req: CallsReq):
    s = _session(req.session)
    conc = max(1, min(req.concurrency, 5))  # hard cap: never flood ER phone lines
    return _stream(lambda emit: calls.run_wave(s, emit, concurrency=conc, use_voice=req.voice and VOICE_ENABLED))


@app.get("/audio/{sid}/{name}")
def audio(sid: str, name: str):
    path = (calls.AUDIO_DIR / sid / name).resolve()
    if not str(path).startswith(str(calls.AUDIO_DIR.resolve())) or not path.exists():
        raise HTTPException(404)
    return FileResponse(path, media_type="audio/wav")
