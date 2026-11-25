"""Compare recorded score conditions without deciding whether performance improved."""
import re

from . import account, analysis, fetch, paths, session
from .api import OsuError
from .player import SETUP_FIELDS
from .store import parse_ts, read_json


def _timestamp(value):
    try:
        parsed = parse_ts(value)
        return parsed if parsed.utcoffset() is not None else None
    except (TypeError, ValueError, AttributeError):
        return None


def _boundary(score, sess):
    session_start, session_end = _timestamp(sess.get("started_at")), _timestamp(sess.get("ended_at"))
    score_start = _timestamp(score.get("started_at"))
    if score_start is None and score.get("timestamp_source") == "started_at":
        score_start = _timestamp(score.get("created_at"))
    score_end = _timestamp(score.get("created_at")) if score.get("timestamp_source") == "ended_at" else None
    starts_before = score_start < session_start if score_start and session_start else None
    ends_after = score_end > session_end if score_end and session_end else None
    ordered = score_start <= score_end if score_start and score_end else None
    if starts_before is True or ends_after is True or ordered is False:
        contained = False
    elif starts_before is False and ends_after is False and ordered is True:
        contained = True
    else:
        contained = None
    return {"started_before_session": starts_before, "ended_after_session": ends_after,
            "score_times_ordered": ordered, "fully_contained": contained}


def _score(session_id, score_id):
    sid = session.resolve(session_id)
    rows = read_json(paths.session_dir(sid) / "scores.json")
    if rows is None:
        raise OsuError(f"scores missing for {sid}")
    found = [row for row in rows if row.get("score_id") == score_id]
    if len(found) != 1:
        raise OsuError(f"expected one saved score {score_id} in {sid}; found {len(found)}")
    sess = session.load(sid)
    observations = [item for item in sess.get("observations", []) if item.get("score_id") == score_id]
    reports = [item for item in analysis.list_reports(sid) if item.get("score_id") == score_id]
    return {"session_id": sid, "session_started_at": sess.get("started_at"),
            "session_ended_at": sess.get("ended_at"), "setup": sess.get("setup"),
            "scores_sync": fetch.recent_sync(sid), "score": found[0],
            "session_boundary_evidence": _boundary(found[0], sess),
            "observations_with_declared_score_id": observations,
            "analyses_with_declared_score_id": reports}


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
                "Observation and analysis score IDs are declared links, not verified play identity or causation.",
                "Mod settings may omit defaults or other relevant conditions; missing values are unknown.",
                "Score and session coverage may be incomplete; inspect sync metadata and player experience.",
            ]}
