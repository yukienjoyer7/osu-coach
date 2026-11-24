"""Offline context for an agent taking over a coaching session."""
from datetime import datetime, timedelta, timezone
import re

from . import fetch, paths, scores, session, stats
from .store import parse_ts, read_json


def _text(path):
    try:
        return path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return None


def _file_time(path):
    try:
        return datetime.fromtimestamp(path.stat().st_mtime, timezone.utc).isoformat()
    except FileNotFoundError:
        return None


def build(session_id=None):
    """Return saved evidence only. File times describe local writes, not API coverage."""
    ids = session.list_ids()
    sid = session.resolve(session_id) if session_id else session.current() or (ids[-1] if ids else None)
    profile = fetch.latest_profile()
    journal = _text(paths.COACH / "journal.md")
    entries = re.split(r"(?m)(?=^## )", journal) if journal else []
    sections = [entry for entry in entries if entry.startswith("## ")]
    recent_journal = "".join(sections[-3:]) if sections else journal
    result = {
        "schema_version": 1,
        "player": _text(paths.COACH / "player.md"),
        "recent_journal": recent_journal,
        "profile": profile,
        "best": read_json(paths.BEST),
        "best_sync": read_json(paths.BEST.with_name("best_sync.json")),
        "best_file_modified_at": _file_time(paths.BEST),
        "session": session.load(sid) if sid else None,
        "current_session_id": session.current(),
        "plan": _text(paths.COACH / "plans" / f"{sid}.md") if sid else None,
        "review": _text(paths.COACH / "reviews" / f"{sid}.md") if sid else None,
        "previous_reviews": [
            {"session_id": previous_id,
             "review": _text(paths.COACH / "reviews" / f"{previous_id}.md")}
            for previous_id in [item for item in ids if sid and item < sid][-3:]
        ],
        "scores_file_modified_at": None,
        "scores_sync": None,
        "overlapping_session_ids": [],
        "stats": None,
    }
    if sid:
        current_session = result["session"]
        start = parse_ts(current_session["started_at"])
        end = parse_ts(current_session["ended_at"]) if current_session.get("ended_at") else None
        for other_id in ids:
            if other_id == sid:
                continue
            other = session.load(other_id)
            other_start = parse_ts(other["started_at"])
            other_end = parse_ts(other["ended_at"]) if other.get("ended_at") else None
            if ((end is None or other_start <= end + timedelta(minutes=5))
                    and (other_end is None or start <= other_end + timedelta(minutes=5))):
                result["overlapping_session_ids"].append(other_id)
        score_path = paths.session_dir(sid) / "scores.json"
        rows = read_json(score_path)
        result["scores_file_modified_at"] = _file_time(score_path)
        result["scores_sync"] = read_json(paths.session_dir(sid) / "scores_sync.json")
        if rows is not None:
            def sr(row):
                beatmap = read_json(fetch.beatmap_path(row["beatmap_id"], scores.score_mods(row)))
                return beatmap.get("star_rating") if beatmap else None

            result["stats"] = stats.compute(rows, sr, result["session"].get("blocks"))
    return result
