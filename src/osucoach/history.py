"""Recorded session scores for a beatmap, with their original conditions."""
from . import account, fetch, paths, session
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
                "scores_sync": fetch.recent_sync(sid),
                "scores": sorted(matches, key=lambda row: (parse_ts(row["created_at"]), row["score_id"])),
            })
    best = read_json(paths.BEST)
    best_matches = None if best is None else sorted(
        [row for row in best if row.get("beatmap_id") == beatmap_id],
        key=lambda row: (parse_ts(row["created_at"]), row["score_id"]))
    session_ids = {row["score_id"] for entry in entries for row in entry["scores"]}
    duplicates = None if best_matches is None else sorted(
        session_ids & {row["score_id"] for row in best_matches})
    return {"beatmap_id": beatmap_id, "account": account.binding(), "sessions": entries,
            "missing_score_sessions": missing_sessions, "best_scores": best_matches,
            "best_sync": read_json(paths.BEST.with_name("best_sync.json")),
            "best_session_duplicate_score_ids": duplicates}
