"""Recorded session scores for a beatmap, with their original conditions."""
from . import account, analysis, fetch, paths, session
from .store import parse_ts, read_json


def map_history(beatmap_id: int) -> dict:
    entries = []
    map_observations = []
    score_linked_observations = []
    map_analyses = []
    missing_sessions = []
    for sid in session.list_ids():
        sess = session.load(sid)
        observations = [item for item in sess.get("observations", []) if item.get("beatmap_id") == beatmap_id]
        if observations:
            map_observations.append({"session_id": sid, "observations": observations})
        reports = [item for item in analysis.list_reports(sid) if item.get("beatmap_id") == beatmap_id]
        if reports:
            map_analyses.append({"session_id": sid, "reports": reports})
        score_path = paths.session_dir(sid) / "scores.json"
        rows = read_json(score_path)
        if rows is None:
            missing_sessions.append(sid)
            continue
        matches = [row for row in rows if row.get("beatmap_id") == beatmap_id]
        score_links = []
        for item in sess.get("observations", []):
            score_id = item.get("score_id")
            if type(score_id) is not int:
                continue
            linked = [row for row in rows if row.get("score_id") == score_id]
            if len(linked) != 1 or linked[0].get("beatmap_id") != beatmap_id:
                continue
            declared_map = item.get("beatmap_id")
            score_links.append({"observation": item,
                                "declared_beatmap_matches_saved_score": (
                                    declared_map == beatmap_id if type(declared_map) is int else None)})
        if score_links:
            score_linked_observations.append({"session_id": sid, "links": score_links})
        if matches:
            entries.append({
                "session_id": sid,
                "session_started_at": sess.get("started_at"),
                "session_ended_at": sess.get("ended_at"),
                "blocks": sess.get("blocks", []),
                "setup": sess.get("setup"),
                "scores_sync": fetch.recent_sync(sid),
                "scores": sorted(matches, key=lambda row: (parse_ts(row["created_at"]), row["score_id"])),
            })
    best = read_json(paths.BEST)
    best_matches = None if best is None else sorted(
        [row for row in best if row.get("beatmap_id") == beatmap_id],
        key=lambda row: (parse_ts(row["created_at"]), row["score_id"]))
    score_sessions = {}
    for entry in entries:
        for row in entry["scores"]:
            score_sessions.setdefault(row["score_id"], set()).add(entry["session_id"])
    session_ids = set(score_sessions)
    session_duplicates = [{"score_id": score_id, "session_ids": sorted(ids)}
                          for score_id, ids in sorted(score_sessions.items()) if len(ids) > 1]
    duplicates = None if best_matches is None else sorted(
        session_ids & {row["score_id"] for row in best_matches})
    return {"beatmap_id": beatmap_id, "account": account.binding(), "sessions": entries,
            "map_observations": map_observations,
            "score_linked_observations": score_linked_observations,
            "map_analyses": map_analyses,
            "missing_score_sessions": missing_sessions, "best_scores": best_matches,
            "best_sync": read_json(paths.BEST.with_name("best_sync.json")),
            "unique_recorded_session_score_count": len(session_ids),
            "session_duplicate_scores": session_duplicates,
            "best_session_duplicate_score_ids": duplicates}
