import os
from pathlib import Path

ROOT = Path(os.environ.get("OSUCOACH_HOME") or Path(__file__).resolve().parents[2]).expanduser().resolve()
DATA = ROOT / "data"
COACH = ROOT / "coach"
ENV_FILE = ROOT / ".env"

PROFILE_DIR = DATA / "profile"
BEST = DATA / "best.json"
BEATMAPS = DATA / "beatmaps"
META_DIR = BEATMAPS / "_meta"  # raw per-map meta captured from score objects
SESSIONS = DATA / "sessions"
CURRENT = DATA / "current_session"


def session_dir(session_id: str) -> Path:
    return SESSIONS / session_id
