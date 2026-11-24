"""Recorded session scores for a beatmap, with their original conditions."""
from . import paths, session
from .store import parse_ts, read_json


def map_history(beatmap_id: int) -> dict:
    entries = []
    missing_sessions = []
    for sid in session.list_ids():
        score_path = paths.session_dir(sid) / "scores.json"
        rows = read_json(score_path)
        if rows is None:
            missing_sessions.append(sid)
            continue
        matches = [row for row in rows if row.get("beatmap_id") == beatmap_id]
        if matches:
            sess = session.load(sid)
            entries.append({
                "session_id": sid,
                "setup": sess.get("setup"),
                "scores_sync": read_json(paths.session_dir(sid) / "scores_sync.json"),
                "scores": sorted(matches, key=lambda row: (parse_ts(row["created_at"]), row["score_id"])),
            })
    return {"beatmap_id": beatmap_id, "sessions": entries, "missing_score_sessions": missing_sessions}
