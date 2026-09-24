"""Korean speech via NVIDIA Speech NIM (hosted on NVCF): Magpie TTS + Parakeet multilingual ASR."""
from __future__ import annotations

import functools
import os
import wave
from pathlib import Path

from .config import NVIDIA_API_KEY

GRPC = "grpc.nvcf.nvidia.com:443"
TTS_FID = os.getenv("ER_TTS_FUNCTION", "877104f7-e885-42b9-8de8-f6e4c6303969")  # magpie-tts-multilingual
ASR_FID = os.getenv("ER_ASR_FUNCTION", "71203149-d3b7-4460-8231-1be2543a1fca")  # parakeet-1.1b-rnnt-multilingual
RATE = 22050
AI_VOICE = "Magpie-Multilingual.KO-KR.Aria"


def _auth(fid: str):
    import riva.client
    return riva.client.Auth(uri=GRPC, use_ssl=True,
                            metadata_args=[["function-id", fid], ["authorization", f"Bearer {NVIDIA_API_KEY}"]])


@functools.cache
def _tts():
    import riva.client
    return riva.client.SpeechSynthesisService(_auth(TTS_FID))


@functools.cache
def _asr():
    import riva.client
    return riva.client.ASRService(_auth(ASR_FID))


@functools.cache
def korean_voices() -> list[str]:
    """Korean Magpie voices advertised by the deployed model (fallback to known names)."""
    import riva.client
    try:
        svc = _tts()
        cfg = svc.stub.GetRivaSynthesisConfig(riva.client.proto.riva_tts_pb2.RivaSynthesisConfigRequest(),
                                              metadata=svc.auth.get_auth_metadata())
        names = []
        for m in cfg.model_config:
            for sub in m.parameters.get("subvoices", "").split(","):
                name = sub.split(":")[0]
                if name.upper().startswith("KO-KR") and name.count(".") == 1:
                    names.append(f"{m.parameters.get('voice_name')}.{name}")
        if names:
            return names
    except Exception:
        pass
    return [AI_VOICE, "Magpie-Multilingual.KO-KR.Ray", "Magpie-Multilingual.KO-KR.Louise"]


def synthesize(text: str, voice: str = AI_VOICE) -> bytes:
    return _tts().synthesize(text, voice_name=voice, language_code="ko-KR", sample_rate_hz=RATE).audio


def transcribe(pcm: bytes, rate: int = RATE) -> str:
    import riva.client
    cfg = riva.client.RecognitionConfig(
        encoding=riva.client.AudioEncoding.LINEAR_PCM, sample_rate_hertz=rate, language_code="ko-KR",
        max_alternatives=1, enable_automatic_punctuation=True, audio_channel_count=1)
    resp = _asr().offline_recognize(pcm, cfg)
    return " ".join(r.alternatives[0].transcript.strip() for r in resp.results if r.alternatives).strip()


def save_wav(path: Path, pcm: bytes, rate: int = RATE) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(pcm)
