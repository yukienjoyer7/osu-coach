"""Find previously recorded maps with cached difficulty attributes."""
import json
import math

from . import account, fetch, paths, scores, session
from .api import OsuError
from .store import now_local, read_json, write_json


def set_tags(beatmap_id, tags):
    normalized = sorted({tag.strip().casefold() for tag in tags if tag.strip()})
    data = {"beatmap_id": beatmap_id, "tags": normalized}
    write_json(paths.DATA / "map_tags" / f"{beatmap_id}.json", data)
    return data


def find(min_sr=None, max_sr=None, min_bpm=None, max_bpm=None,
         min_ar=None, max_ar=None, min_length=None, max_length=None, tag=None):
    candidates = {}
    sources = [paths.BEST] + [paths.session_dir(sid) / "scores.json" for sid in session.list_ids()]
    for source in sources:
        for score in read_json(source, []) or []:
            attributes = read_json(fetch.beatmap_path(score["beatmap_id"], scores.score_mods(score)))
            if not attributes:
                continue
            annotation = read_json(paths.DATA / "map_tags" / f"{score['beatmap_id']}.json", {})
            tags = annotation.get("tags", [])
            if tag and tag.casefold() not in tags:
                continue
            filters = (("star_rating", min_sr, max_sr), ("bpm", min_bpm, max_bpm),
                       ("ar", min_ar, max_ar), ("length_s", min_length, max_length))
            if any((low is not None and (attributes.get(field) is None or attributes[field] < low))
                   or (high is not None and (attributes.get(field) is None or attributes[field] > high))
                   for field, low, high in filters):
                continue
            settings = score.get("mod_settings") or {}
            key = (score["beatmap_id"], tuple(sorted(score["mods"])), json.dumps(settings, sort_keys=True))
            if key not in candidates:
                candidates[key] = {
                    "beatmap_id": score["beatmap_id"], "title": score.get("title"),
                    "version": score.get("version"), "mods": score["mods"],
                    "mod_settings": settings, "attributes": attributes,
                    "user_tags": tags,
                    "ranked_status": None, "pattern_type": None,
                    "recorded_score_ids": set(),
                }
            score_id = score.get("score_id")
            if type(score_id) is int:
                candidates[key]["recorded_score_ids"].add(score_id)
    for candidate in candidates.values():
        candidate["recorded_score_ids"] = sorted(candidate["recorded_score_ids"])
        candidate["recorded_score_count"] = len(candidate["recorded_score_ids"])
    return sorted(candidates.values(), key=lambda item: (item["attributes"]["star_rating"], item["beatmap_id"]))


def _recorded_scores():
    """Index saved score IDs by beatmap; missing files are not evidence of unplayed maps."""
    seen = {}
    best = read_json(paths.BEST)
    missing_sessions = []
    sources = [best or []]
    for sid in session.list_ids():
        rows = read_json(paths.session_dir(sid) / "scores.json")
        if rows is None:
            missing_sessions.append(sid)
        else:
            sources.append(rows)
    for rows in sources:
        for row in rows:
            if row.get("beatmap_id") is not None and row.get("score_id") is not None:
                seen.setdefault(row["beatmap_id"], set()).add(row["score_id"])
    return seen, best is None, missing_sessions


def discover(client, query, cursor=None, min_sr=None, max_sr=None, tag=None):
    """Search the upstream beatmapset endpoint; results are unverified candidates."""
    params = {"m": 0, "s": "ranked", "q": query}
    if cursor:
        params["cursor_string"] = cursor
    response = client.get("/beatmapsets/search", **params)
    if not isinstance(response, dict) or not isinstance(response.get("beatmapsets"), list):
        raise OsuError("beatmap search returned an unexpected response")
    if response.get("error"):
        raise OsuError("beatmap search returned an error")
    recorded, best_missing, missing_sessions = _recorded_scores()
    candidates = []
    sets_without_difficulties = 0
    incomplete_difficulties = 0
    for beatmapset in response["beatmapsets"]:
        if not isinstance(beatmapset, dict) or not isinstance(beatmapset.get("beatmaps"), list):
            sets_without_difficulties += 1
            continue
        for beatmap in beatmapset["beatmaps"]:
            if not isinstance(beatmap, dict):
                incomplete_difficulties += 1
                continue
            rating = beatmap.get("difficulty_rating")
            if (type(beatmap.get("id")) is not int or type(beatmapset.get("id")) is not int
                    or not isinstance(beatmap.get("mode"), str) or not isinstance(beatmap.get("status"), str)
                    or type(rating) not in (int, float) or not math.isfinite(rating)):
                incomplete_difficulties += 1
                continue
            if (beatmap["mode"] != "osu" or beatmap["status"] != "ranked"
                    or (min_sr is not None and rating < min_sr)
                    or (max_sr is not None and rating > max_sr)):
                continue
            annotation = read_json(paths.DATA / "map_tags" / f"{beatmap['id']}.json", {})
            tags = annotation.get("tags", [])
            if tag is not None and tag.casefold() not in tags:
                continue
            candidates.append({"beatmap_id": beatmap["id"], "beatmapset_id": beatmapset["id"],
                               "title": beatmapset.get("title"), "artist": beatmapset.get("artist"),
                               "creator": beatmapset.get("creator"), "version": beatmap.get("version"),
                               "lazer_only": beatmap.get("lazer_only"), "total_length_s": beatmap.get("total_length"),
                               "star_rating_nomod": rating, "mode": beatmap["mode"],
                               "status": beatmap["status"], "user_tags": tags,
                               "recorded_score_count": len(recorded.get(beatmap["id"], set())),
                               "url": f"https://osu.ppy.sh/beatmapsets/{beatmapset['id']}#osu/{beatmap['id']}"})
    return {"query": query, "fetched_at": now_local().isoformat(timespec="seconds"),
            "requested_mode": "osu", "requested_status": "ranked",
            "local_star_range": {"min": min_sr, "max": max_sr}, "local_user_tag": tag,
            "recorded_history": {"account": account.binding(), "best_file_missing": best_missing,
                                 "missing_score_sessions": missing_sessions},
            "ranked_osu_candidates": candidates,
            "sets_without_difficulties": sets_without_difficulties,
            "incomplete_difficulties": incomplete_difficulties,
            "beatmapsets": response["beatmapsets"], "total": response.get("total"),
            "cursor_string": response.get("cursor_string"),
            "selection_limits": [
                "Upstream filters may depend on search permissions; verify each difficulty and status.",
                "Local candidates match returned mode, status, optional nomod star range and optional user tag; query filters are not verified.",
                "Recorded score counts cover saved data only; zero does not mean a map is unfamiliar or unplayed.",
                "User tags are subjective and apply across mods; they do not verify patterns.",
                "Results do not establish mod-adjusted suitability; check lazer_only for client compatibility.",
            ]}
