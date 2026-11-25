"""Compare recorded score conditions without deciding whether performance improved."""
import re

from . import account, fetch, paths, session
from .api import OsuError
from .player import SETUP_FIELDS
from .store import read_json


def _score(session_id, score_id):
    sid = session.resolve(session_id)
    rows = read_json(paths.session_dir(sid) / "scores.json")
    if rows is None:
        raise OsuError(f"scores missing for {sid}")
    found = [row for row in rows if row.get("score_id") == score_id]
    if len(found) != 1:
        raise OsuError(f"expected one saved score {score_id} in {sid}; found {len(found)}")
    sess = session.load(sid)
    return {"session_id": sid, "session_started_at": sess.get("started_at"),
            "session_ended_at": sess.get("ended_at"), "setup": sess.get("setup"),
            "scores_sync": fetch.recent_sync(sid), "score": found[0]}


def _equality(left, right):
    return "unknown" if left is None or right is None else "same" if left == right else "different"


def _mods(score):
    if score.get("mods_known") is not True or not isinstance(score.get("mods"), list):
        return None
    return sorted(score["mods"])


def _settings(score):
    mods = _mods(score)
    if mods is None:
        return None
    if not mods:
        return {}
    settings = score.get("mod_settings")
    return settings if isinstance(settings, dict) else None


def _checksum(score):
    value = score.get("beatmap_checksum")
    return value.lower() if isinstance(value, str) and re.fullmatch(r"[a-fA-F0-9]{32}", value) else None


def compare(first_session, first_score, second_session, second_score):
    first, second = _score(first_session, first_score), _score(second_session, second_score)
    a, b = first["score"], second["score"]
    setup_a, setup_b = first["setup"] or {}, second["setup"] or {}
    checks = {
        "beatmap_id": _equality(a.get("beatmap_id"), b.get("beatmap_id")),
        "fetch_time_beatmap_checksum": _equality(_checksum(a), _checksum(b)),
        "user_id": _equality(a.get("user_id"), b.get("user_id")),
        "recorded_mods": _equality(_mods(a), _mods(b)),
        "recorded_mod_settings": _equality(_settings(a), _settings(b)),
        "build_id": _equality(a.get("build_id"), b.get("build_id")),
    }
    checks.update({f"session_{key}": _equality(setup_a.get(key), setup_b.get(key)) for key in SETUP_FIELDS})
    return {"account": account.binding(), "first": first, "second": second,
            "same_score_id": a.get("score_id") == b.get("score_id"), "recorded_checks": checks,
            "limitations": [
                "Same recorded values do not prove comparable conditions, client rules or causal improvement.",
                "Beatmap checksums describe fetch-time metadata, not necessarily the version played.",
                "Session setup is a snapshot, not proof of settings used on each score.",
                "Mod settings may omit defaults or other relevant conditions; missing values are unknown.",
                "Score and session coverage may be incomplete; inspect sync metadata and player experience.",
            ]}
