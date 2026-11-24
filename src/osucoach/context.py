"""Offline context for an agent taking over a coaching session."""
from datetime import datetime, timezone

from . import fetch, paths, scores, session, stats
from .store import read_json


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
    entries = journal.split("\n## ") if journal else []
    recent_journal = "\n## ".join(entries[-3:]) if entries else None
    result = {
        "schema_version": 1,
        "player": _text(paths.COACH / "player.md"),
        "recent_journal": recent_journal,
        "profile": profile,
        "best": read_json(paths.BEST),
        "best_file_modified_at": _file_time(paths.BEST),
        "session": session.load(sid) if sid else None,
        "current_session_id": session.current(),
        "plan": _text(paths.COACH / "plans" / f"{sid}.md") if sid else None,
        "review": _text(paths.COACH / "reviews" / f"{sid}.md") if sid else None,
        "scores_file_modified_at": None,
        "stats": None,
    }
    if sid:
        score_path = paths.session_dir(sid) / "scores.json"
        rows = read_json(score_path)
        result["scores_file_modified_at"] = _file_time(score_path)
        if rows is not None:
            def sr(row):
                beatmap = read_json(fetch.beatmap_path(row["beatmap_id"], scores.score_mods(row)))
                return beatmap.get("star_rating") if beatmap else None

            result["stats"] = stats.compute(rows, sr)
    return result
