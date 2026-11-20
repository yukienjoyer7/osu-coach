"""Score mapping: the ONLY place that knows the osu! API score shape (lazer format)."""
from collections import defaultdict

from . import mods as M


def map_score(s: dict) -> dict:
    bm = s.get("beatmap") or {}
    st = s.get("statistics") or {}
    mods = [m["acronym"] for m in s.get("mods") or []]
    settings = {m["acronym"]: m["settings"] for m in s.get("mods") or [] if m.get("settings")}
    w = s.get("weight") or {}
    out = {
        "score_id": s["id"],
        "beatmap_id": s.get("beatmap_id") or bm.get("id"),
        "beatmapset_id": bm.get("beatmapset_id") or (s.get("beatmapset") or {}).get("id"),
        "title": (s.get("beatmapset") or {}).get("title", ""),
        "version": bm.get("version", ""),
        "created_at": s.get("ended_at") or s.get("started_at"),
        "mods": mods,
        "passed": bool(s.get("passed")),
        "accuracy": s.get("accuracy"),
        "max_combo": s.get("max_combo"),
        "map_max_combo": None,  # filled from beatmap files once fetched
        "n300": st.get("great", 0),
        "n100": st.get("ok", 0),
        "n50": st.get("meh", 0),
        "miss": st.get("miss", 0),
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
    scores.sort(key=lambda x: (x["created_at"], x["score_id"]))
    seen: dict[int, int] = defaultdict(int)
    for sc in scores:
        seen[sc["beatmap_id"]] += 1
        sc["retry_index"] = seen[sc["beatmap_id"]]
    return scores


def score_mods(sc: dict) -> list[dict]:
    return M.from_score(sc["mods"], sc.get("mod_settings"))
