"""Runtime configuration, read from the project .env file."""
import datetime as dt
import os
from pathlib import Path
from zoneinfo import ZoneInfo

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[2]
load_dotenv(ROOT / ".env")

DATA_KEY = os.getenv("data_key") or os.getenv("DATA_KEY")
NVIDIA_API_KEY = os.getenv("nvidia_api_key") or os.getenv("NVIDIA_API_KEY")
NIM_BASE_URL = os.getenv("NIM_BASE_URL", "https://integrate.api.nvidia.com/v1")
if NVIDIA_API_KEY:  # NeMo Guardrails / Agent Toolkit NIM clients read the upper-case name
    os.environ.setdefault("NVIDIA_API_KEY", NVIDIA_API_KEY)

# System 2: judgement, restriction reading, replanning
MODEL_REASON = os.getenv("ER_MODEL_REASON", "nvidia/nemotron-3-super-120b-a12b")

# Writable cache (snapshots, audio, rejection board). Inside an OpenShell sandbox the code is
# read-only, so the image points this at the sandbox workspace.
CACHE_DIR = Path(os.getenv("ER_CACHE_DIR", ROOT / "data" / "cache"))
# Speech NIM uses gRPC, which the sandbox proxy cannot inspect for credential rewriting.
VOICE_ENABLED = os.getenv("ER_VOICE", "1") != "0"
SNAPSHOT_DIR = CACHE_DIR / "snapshots"

# NEMC timestamps and restriction messages ("월~금 9A~5P30'") are Korea local time.
# Containers usually run in UTC, so never rely on the host timezone.
KST = ZoneInfo("Asia/Seoul")


def now_kst() -> dt.datetime:
    """Naive Korea-local wall clock, comparable with NEMC timestamps."""
    return dt.datetime.now(KST).replace(tzinfo=None)
