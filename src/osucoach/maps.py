"""Find previously recorded maps with cached difficulty attributes."""
import json

from . import fetch, paths, scores, session
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
            key = (score["beatmap_id"], tuple(score["mods"]), json.dumps(settings, sort_keys=True))
            candidates[key] = {
                "beatmap_id": score["beatmap_id"], "title": score.get("title"),
                "version": score.get("version"), "mods": score["mods"],
                "mod_settings": settings, "attributes": attributes,
                "user_tags": tags,
                "ranked_status": None, "pattern_type": None,
            }
    return sorted(candidates.values(), key=lambda item: (item["attributes"]["star_rating"], item["beatmap_id"]))


def discover(client, query, cursor=None):
    """Search the upstream beatmapset endpoint; results are unverified candidates."""
    params = {"m": 0, "s": "ranked", "q": query}
    if cursor:
        params["cursor_string"] = cursor
    response = client.get("/beatmapsets/search", **params)
    if not isinstance(response, dict) or not isinstance(response.get("beatmapsets"), list):
        raise OsuError("beatmap search returned an unexpected response")
    if response.get("error"):
        raise OsuError("beatmap search returned an error")
    return {"query": query, "fetched_at": now_local().isoformat(timespec="seconds"),
            "requested_mode": "osu", "requested_status": "ranked",
            "beatmapsets": response["beatmapsets"], "total": response.get("total"),
            "cursor_string": response.get("cursor_string"),
            "selection_limits": [
                "Upstream filters may depend on search permissions; verify each difficulty and status.",
                "A set result can include difficulties that do not match the query or osu!standard.",
                "Results do not establish player familiarity, patterns, or mod-adjusted suitability.",
            ]}
