"""Find previously recorded maps with cached difficulty attributes."""
import json

from . import fetch, paths, scores, session
from .store import read_json


def find(min_sr=None, max_sr=None, min_bpm=None, max_bpm=None,
         min_ar=None, max_ar=None, min_length=None, max_length=None):
    candidates = {}
    sources = [paths.BEST] + [paths.session_dir(sid) / "scores.json" for sid in session.list_ids()]
    for source in sources:
        for score in read_json(source, []) or []:
            attributes = read_json(fetch.beatmap_path(score["beatmap_id"], scores.score_mods(score)))
            if not attributes:
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
                "ranked_status": None, "pattern_type": None,
            }
    return sorted(candidates.values(), key=lambda item: (item["attributes"]["star_rating"], item["beatmap_id"]))
