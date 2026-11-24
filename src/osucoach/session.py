"""Session lifecycle: data/sessions/<id>/session.json plus a `current_session` pointer."""
from datetime import datetime
from math import isfinite

from . import paths as P
from .api import OsuError
from .player import SETUP_FIELDS, read_setup
from .store import now_local, parse_ts, read_json, write_json

BEFORE_KEYS = ("sleep_h", "energy_1_5", "hand_pain_0_10", "motivation_1_5")
AFTER_KEYS = ("duration_min", "fatigue_1_5", "hand_pain_0_10", "enjoyment_1_5",
              "felt_hard", "notes")


def new_id(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%d_%H%M")


def list_ids() -> list[str]:
    if not P.SESSIONS.exists():
        return []
    return sorted(d.name for d in P.SESSIONS.iterdir() if (d / "session.json").exists())


def current() -> str | None:
    if not P.CURRENT.exists():
        return None
    sid = P.CURRENT.read_text(encoding="utf-8").strip()
    return sid if sid and (P.session_dir(sid) / "session.json").exists() else None


def resolve(session_id: str | None = None) -> str:
    """Explicit id, else the open session, else the most recent one."""
    sid = session_id or current() or (list_ids() or [None])[-1]
    if not sid:
        raise OsuError("no session found; run `osucoach session start`")
    if not (P.session_dir(sid) / "session.json").exists():
        raise OsuError(f"unknown session {sid}")
    return sid


def load(session_id: str) -> dict:
    return read_json(P.session_dir(session_id) / "session.json")


def save(session: dict) -> None:
    write_json(P.session_dir(session["session_id"]) / "session.json", session)


def apply_updates(session: dict, before: dict | None = None, after: dict | None = None,
                  followed_plan: str | None = None, setup_changed: bool | None = None) -> dict:
    ranges = {"energy_1_5": (1, 5), "motivation_1_5": (1, 5),
              "fatigue_1_5": (1, 5), "enjoyment_1_5": (1, 5),
              "hand_pain_0_10": (0, 10)}
    for block, vals, keys in (("before", before, BEFORE_KEYS), ("after", after, AFTER_KEYS)):
        for k, v in (vals or {}).items():
            if k not in keys:
                raise OsuError(f"unknown {block} field {k}")
            if v is None:
                continue
            if k in ranges:
                low, high = ranges[k]
                if type(v) is not int or not low <= v <= high:
                    raise OsuError(f"{k} must be an integer from {low} to {high}")
            if k == "sleep_h" and (type(v) not in (int, float) or not isfinite(v) or v < 0):
                raise OsuError("sleep_h must be a finite non-negative number")
            if k == "duration_min" and (type(v) is not int or v < 0):
                raise OsuError("duration_min must be a non-negative integer")
    if followed_plan is not None and followed_plan not in ("yes", "partly", "no"):
        raise OsuError("followed_plan must be yes|partly|no")
    for block, vals in (("before", before), ("after", after)):
        for k, v in (vals or {}).items():
            if v is not None:
                session[block][k] = v
    if followed_plan is not None:
        session["followed_plan"] = followed_plan
    if setup_changed is not None:
        session["setup"]["changed_since_last"] = setup_changed
    return session


def start(before: dict | None = None, setup_changed: bool | None = None) -> dict:
    if current():
        raise OsuError(f"session {current()} is still open; run `osucoach session end`")
    now = now_local()
    sid = new_id(now)
    if (P.session_dir(sid) / "session.json").exists():
        raise OsuError(f"session {sid} already exists; wait a minute")
    ids = list_ids()
    previous_session = load(ids[-1]) if ids else {}
    previous_setup = previous_session.get("setup") or {}
    player_setup = read_setup(P.COACH / "player.md")
    setup = dict.fromkeys(SETUP_FIELDS)
    setup.update(previous_setup if player_setup is None else player_setup)
    setup["changed_since_last"] = bool(previous_setup) and any(
        setup[key] != previous_setup.get(key) for key in SETUP_FIELDS
    )
    sess = {
        "session_id": sid,
        "started_at": now.isoformat(timespec="seconds"),
        "ended_at": None,
        "before": dict.fromkeys(BEFORE_KEYS),
        "after": dict.fromkeys(AFTER_KEYS),
        "followed_plan": None,
        "setup": setup,
    }
    apply_updates(sess, before=before, setup_changed=setup_changed)
    save(sess)
    P.CURRENT.parent.mkdir(parents=True, exist_ok=True)
    P.CURRENT.write_text(sid, encoding="utf-8")
    return sess


def stamp_end(session: dict) -> dict:
    """Set ended_at and the computed duration (does not touch the current pointer)."""
    if session.get("ended_at"):
        return session
    end = now_local()
    session["ended_at"] = end.isoformat(timespec="seconds")
    mins = round((end - parse_ts(session["started_at"])).total_seconds() / 60)
    session["after"]["duration_min"] = mins
    return session


def mark_block(session: dict, label: str) -> dict:
    """Mark the beginning of a practice block in the current session."""
    label = label.strip()
    if not label:
        raise OsuError("block label cannot be empty")
    if session.get("ended_at"):
        raise OsuError("session has ended; finish syncing it first")
    at = now_local().isoformat(timespec="seconds")
    markers = session.setdefault("blocks", [])
    if markers and parse_ts(at) <= parse_ts(markers[-1]["started_at"]):
        raise OsuError("block markers must have distinct increasing timestamps")
    marker = {"label": label, "started_at": at}
    markers.append(marker)
    save(session)
    return marker


def close(session_id: str) -> None:
    if current() == session_id:
        P.CURRENT.unlink()
