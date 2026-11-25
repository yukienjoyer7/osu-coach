"""Score mapping: the ONLY place that knows the osu! API score shape (lazer format)."""
from collections import defaultdict
import math
import re

from . import mods as M
from .store import parse_ts


def _hit_count(statistics, key):
    if not isinstance(statistics, dict):
        return None
    value = statistics.get(key, 0)
    return value if type(value) is int and value >= 0 else None


def _boolean(source, key):
    value = source.get(key)
    return value if type(value) is bool else None


def _usable_mods(value):
    if not isinstance(value, list):
        return False
    names = set()
    for mod in value:
        if not isinstance(mod, dict) or not isinstance(mod.get("acronym"), str):
            return False
        name = mod["acronym"]
        if not re.fullmatch(r"[A-Z0-9]{2,8}", name) or name in names:
            return False
        names.add(name)
        settings = mod.get("settings")
        if settings is not None and not isinstance(settings, dict):
            return False
        settings = settings or {}
        numeric = ("speed_change",) if name in M.SPEED else ()
        if name == "DA":
            numeric = ("circle_size", "approach_rate", "overall_difficulty")
        for key in numeric:
            setting = settings.get(key)
            if key == "speed_change" and key in settings and setting is None:
                return False
            if setting is not None and (type(setting) not in (int, float) or not math.isfinite(setting)
                                        or (key == "speed_change" and setting <= 0)):
                return False
    return True


def map_score(s: dict) -> dict:
    bm = s.get("beatmap") or {}
    st = s.get("statistics")
    raw_mods = s.get("mods")
    mods_known = _usable_mods(raw_mods)
    mods = [m["acronym"] for m in raw_mods] if mods_known else []
    settings = {m["acronym"]: m["settings"] for m in raw_mods if m.get("settings")} if mods_known else {}
    w = s.get("weight") or {}
    out = {
        "score_id": s["id"],
        "user_id": s.get("user_id"),
        "legacy_score_id": s.get("legacy_score_id"),
        "build_id": s.get("build_id"),
        "beatmap_id": s.get("beatmap_id") or bm.get("id"),
        "beatmap_checksum": bm.get("checksum"),
        "beatmapset_id": bm.get("beatmapset_id") or (s.get("beatmapset") or {}).get("id"),
        "title": (s.get("beatmapset") or {}).get("title", ""),
        "version": bm.get("version", ""),
        "created_at": s.get("ended_at") or s.get("started_at"),
        "timestamp_source": "ended_at" if s.get("ended_at") else ("started_at" if s.get("started_at") else None),
        "started_at": s.get("started_at") if s.get("ended_at") else None,
        "mods": mods,
        "mods_known": mods_known,
        "passed": _boolean(s, "passed"),
        "processed": _boolean(s, "processed"),
        "ranked": _boolean(s, "ranked"),
        "is_perfect_combo": _boolean(s, "is_perfect_combo"),
        "legacy_perfect": _boolean(s, "legacy_perfect"),
        "accuracy": s.get("accuracy"),
        "max_combo": s.get("max_combo"),
        "map_max_combo": None,  # filled from beatmap files once fetched
        "total_score": s.get("total_score"),
        "classic_total_score": s.get("classic_total_score"),
        "legacy_total_score": s.get("legacy_total_score"),
        "raw_statistics": dict(st) if isinstance(st, dict) else None,
        "raw_maximum_statistics": (dict(s["maximum_statistics"])
                                   if isinstance(s.get("maximum_statistics"), dict) else None),
        "n300": _hit_count(st, "great"),
        "n100": _hit_count(st, "ok"),
        "n50": _hit_count(st, "meh"),
        "miss": _hit_count(st, "miss"),
        "pp": s.get("pp"),
        "weight_pct": w.get("percentage"),
        "rank": s.get("rank"),
        "retry_index": None,
    }
    if settings:
        out["mod_settings"] = settings
    return out


def beatmap_meta(s: dict) -> dict | None:
    """Raw per-map meta embedded in a score object (saves a GET /beatmaps call)."""
    bm = s.get("beatmap")
    if not bm or "cs" not in bm:
        return None
    return {k: bm.get(k) for k in ("id", "cs", "ar", "accuracy", "bpm", "total_length",
                                   "count_circles", "count_sliders", "count_spinners")}


def add_retry_index(scores: list[dict]) -> list[dict]:
    """Sort by created_at and number attempts per beatmap (1 = first attempt)."""
    scores.sort(key=lambda x: (parse_ts(x["created_at"]), x["score_id"]))
    seen: dict[int, int] = defaultdict(int)
    for sc in scores:
        seen[sc["beatmap_id"]] += 1
        sc["retry_index"] = seen[sc["beatmap_id"]]
    return scores


def score_mods(sc: dict) -> list[dict]:
    return M.from_score(sc["mods"], sc.get("mod_settings"))
