"""API -> data/ files, per DATA_CONTRACT.md. All network calls go through api.Client."""
from datetime import timedelta
import math

from . import account
from . import mods as M
from . import paths as P
from . import scores as S
from .api import Client, HttpError, OsuError
from .store import now_local, parse_ts, read_json, write_json

SESSION_GRACE = timedelta(minutes=5)


class MapDataError(OsuError):
    """The API returned unusable data for one beatmap."""


# ---- profile / threshold -------------------------------------------------
def profile_path(day: str | None = None):
    return P.PROFILE_DIR / f"{day or now_local().strftime('%Y-%m-%d')}.json"


def _latest_profile_record():
    files = sorted(P.PROFILE_DIR.glob("*.json")) if P.PROFILE_DIR.exists() else []
    if not files:
        return None, None
    profiles = [(path, read_json(path)) for path in files]
    dated = []
    for path, profile in profiles:
        try:
            fetched = parse_ts(profile["fetched_at"])
            if fetched.utcoffset() is not None:
                dated.append((fetched, path.name, path, profile))
        except (TypeError, KeyError, ValueError, AttributeError):
            continue
    # Legacy files with no usable timestamp have unknown freshness. Use a
    # filename fallback only if no profile has a comparable fetch timestamp.
    if dated:
        _, _, path, profile = max(dated, key=lambda item: (item[0], item[1]))
        return path, profile
    return profiles[-1]


def latest_profile() -> dict | None:
    return _latest_profile_record()[1]


def fetch_profile(c: Client) -> dict:
    user_id = account.check(c.user_id)
    u = c.get(f"/users/{c.user_id}/osu")
    if user_id.isdecimal() and u.get("id") is not None and u["id"] != int(user_id):
        raise OsuError("profile data belongs to another osu! user")
    st = u["statistics"]
    account.bind(user_id)
    path = profile_path()
    old = latest_profile() or {}
    prof = {
        "fetched_at": now_local().isoformat(timespec="seconds"),
        "user_id": u.get("id"),
        "pp": st["pp"],
        "global_rank": st["global_rank"],
        "country_rank": st["country_rank"],
        "accuracy": round(st["hit_accuracy"], 2),
        "play_count": st["play_count"],
        "play_time_s": st["play_time"],
        "rank_threshold_10k_pp": old.get("rank_threshold_10k_pp"),
        "rank_thresholds": old.get("rank_thresholds", {}),
    }
    write_json(path, prof)
    return prof


def fetch_threshold(c: Client, rank: int = 10000) -> dict:
    if rank < 1:
        raise OsuError("rank must be positive")
    if account.binding() is not None:
        account.check(c.user_id)
    page = (rank - 1) // 50 + 1
    r = c.get("/rankings/osu/performance", **{"cursor[page]": page})
    rows = r.get("ranking") or []
    match = next((row for row in rows if row.get("global_rank") == rank), None)
    if match is None:
        raise OsuError(f"ranking response did not contain rank #{rank}")
    pp = match.get("pp")
    if type(pp) not in (int, float) or not math.isfinite(pp) or pp < 0:
        raise OsuError(f"ranking response contained invalid pp for rank #{rank}")
    path, prof = _latest_profile_record()
    if prof is None:
        prof = fetch_profile(c)
        path = profile_path()
    prof.setdefault("rank_thresholds", {})[str(rank)] = {
        "pp": pp, "fetched_at": now_local().isoformat(timespec="seconds")
    }
    if rank == 10000:
        prof["rank_threshold_10k_pp"] = pp
    write_json(path, prof)
    return prof


# ---- scores --------------------------------------------------------------
def _save_meta(raw: dict) -> None:
    meta = S.beatmap_meta(raw)
    if meta:
        write_json(P.META_DIR / f"{meta['id']}.json", meta)


def fetch_best(c: Client) -> list[dict]:
    user_id = account.check(c.user_id)
    raw = c.get(f"/users/{c.user_id}/scores/best", mode="osu", limit=100)
    account.check_response(user_id, raw)
    account.bind(user_id)
    for r in raw:
        _save_meta(r)
    fetched_at = now_local().isoformat(timespec="seconds")
    scores = [S.map_score(r) for r in raw]
    for score in scores:
        score["retrieved_at"] = fetched_at
    fill_map_max_combo(scores)
    write_json(P.BEST, scores)
    write_json(P.BEST.with_name("best_sync.json"), {
        "fetched_at": fetched_at,
        "scores_returned": len(scores),
    })
    return scores


def _recent_raw(c: Client) -> tuple[list[dict], bool]:
    # osu!web currently caps recent scores at 100 total, including pagination.
    rows = c.get(f"/users/{c.user_id}/scores/recent", mode="osu",
                 include_fails=1, limit=100, offset=0)
    return rows, len(rows) >= 100


def in_session(created_at: str, start, end) -> bool:
    t = parse_ts(created_at)
    return t >= start and (end is None or t <= end + SESSION_GRACE)


def recent_sync(session_id: str) -> dict | None:
    sync = read_json(P.session_dir(session_id) / "scores_sync.json")
    if sync is not None:
        sync["limit_reached_ever"] = bool(sync.get("limit_reached_ever") or sync.get("page_limit_reached")
                                          or sync.get("returned_scores", 0) >= 100)
    return sync


def save_map_sync(session_id: str, map_count: int, fetched: int, errors: list[dict]) -> dict:
    """Record the last completed map pass and its score-sync reference."""
    score_sync = recent_sync(session_id) or {}
    result = {"completed_at": now_local().isoformat(timespec="seconds"),
              "scores_sync_fetched_at": score_sync.get("fetched_at"),
              "maps": map_count, "fetched": fetched, "errors": errors}
    write_json(P.session_dir(session_id) / "map_sync.json", result)
    return result


def fetch_recent(c: Client, session_id: str, session: dict) -> list[dict]:
    """Fetch recent scores inside the session window, merge with any saved, write scores.json."""
    start = parse_ts(session["started_at"])
    end = parse_ts(session["ended_at"]) if session.get("ended_at") else None
    path = P.session_dir(session_id) / "scores.json"
    sync_path = P.session_dir(session_id) / "scores_sync.json"
    previous_sync = recent_sync(session_id) or {}
    user_id = account.check(c.user_id)
    saved_rows = read_json(path, [])
    account.check_response(user_id, saved_rows)
    merged = {s["score_id"]: s for s in saved_rows}  # recent only spans 24h: keep old
    raw, page_limit_reached = _recent_raw(c)
    account.check_response(user_id, raw)
    account.bind(user_id)
    fetched_at = now_local().isoformat(timespec="seconds")
    for r in raw:
        sc = S.map_score(r)
        if sc["created_at"] and in_session(sc["created_at"], start, end):
            _save_meta(r)
            sc["retrieved_at"] = fetched_at
            merged[sc["score_id"]] = sc
    scores = S.add_retry_index(list(merged.values()))
    fill_map_max_combo(scores)
    write_json(path, scores)
    timestamps = [r.get("ended_at") or r.get("started_at") for r in raw]
    timestamps = [t for t in timestamps if t]
    write_json(sync_path, {
        "fetched_at": fetched_at,
        "returned_scores": len(raw),
        "page_limit_reached": page_limit_reached,
        "limit_reached_ever": bool(page_limit_reached or previous_sync.get("limit_reached_ever")
                                   or previous_sync.get("page_limit_reached")
                                   or previous_sync.get("returned_scores", 0) >= 100),
        "oldest_returned_at": min(timestamps, key=parse_ts) if timestamps else None,
        "newest_returned_at": max(timestamps, key=parse_ts) if timestamps else None,
    })
    return scores


# ---- beatmaps ------------------------------------------------------------
def _r(v, nd):
    return round(v, nd) if type(v) in (int, float) and math.isfinite(v) else None


def _valid_map_meta(meta, beatmap_id):
    if not isinstance(meta, dict) or meta.get("id") != beatmap_id:
        return False
    fields = ("cs", "ar", "accuracy", "total_length", "count_circles", "count_sliders", "count_spinners")
    if any(type(meta.get(field)) not in (int, float) or not math.isfinite(meta[field]) for field in fields):
        return False
    bpm = meta.get("bpm")
    return ("bpm" in meta and (bpm is None or
            (type(bpm) in (int, float) and math.isfinite(bpm) and bpm > 0)) and
            all(meta[field] >= 0 for field in fields[3:]))


def beatmap_path(beatmap_id: int, mods: list[dict]):
    return P.BEATMAPS / f"{beatmap_id}_{M.key(mods)}.json"


def fetch_beatmap(c: Client, beatmap_id: int, mods: list[dict], refresh: bool = False) -> tuple[dict, bool]:
    """Return (beatmap data, made_api_calls), optionally refreshing cached attributes."""
    path = beatmap_path(beatmap_id, mods)
    cached = read_json(path)
    cached_sr = _r(cached.get("star_rating"), 3) if isinstance(cached, dict) else None
    if cached_sr is not None and cached["star_rating"] >= 0 and not refresh:
        return cached, False
    norm = M.normalize(mods)
    meta_path = P.META_DIR / f"{beatmap_id}.json"
    meta = read_json(meta_path)
    if refresh or not _valid_map_meta(meta, beatmap_id):
        b = c.get(f"/beatmaps/{beatmap_id}")
        if not isinstance(b, dict):
            raise MapDataError(f"beatmap {beatmap_id} returned invalid metadata")
        meta = {field: b.get(field) for field in ("id", "cs", "ar", "accuracy", "bpm", "total_length",
                                                   "count_circles", "count_sliders", "count_spinners")}
        if not _valid_map_meta(meta, beatmap_id):
            raise MapDataError(f"beatmap {beatmap_id} returned invalid metadata")
    response = c.post(f"/beatmaps/{beatmap_id}/attributes", {"mods": norm, "ruleset": "osu"})
    a = response.get("attributes") if isinstance(response, dict) else None
    rating = _r(a.get("star_rating"), 3) if isinstance(a, dict) else None
    if rating is None or a["star_rating"] < 0:
        raise MapDataError(f"beatmap {beatmap_id} returned invalid difficulty attributes")
    adj = M.adjust(meta["cs"], meta["ar"], meta["accuracy"], meta["bpm"],
                   meta["total_length"], norm)
    data = {
        "beatmap_id": beatmap_id,
        "fetched_at": now_local().isoformat(timespec="seconds"),
        "mods": [m["acronym"] for m in norm],
        "mod_settings": {m["acronym"]: m["settings"] for m in norm if m.get("settings")},
        "star_rating": _r(a["star_rating"], 3),
        # some maps come back with only star_rating + max_combo 0: keep the rest null
        "aim": _r(a.get("aim_difficulty"), 3),
        "speed": _r(a.get("speed_difficulty"), 3),
        "slider_factor": _r(a.get("slider_factor"), 3),
        "speed_note_count": _r(a.get("speed_note_count"), 1),
        **adj,
        "n_circles": meta["count_circles"],
        "n_sliders": meta["count_sliders"],
        "n_spinners": meta["count_spinners"],
        "max_combo": (a.get("max_combo") if type(a.get("max_combo")) is int and a["max_combo"] > 0 else None),
    }
    write_json(meta_path, meta)
    write_json(path, data)
    return data, True


def fetch_beatmaps_for(c: Client, scores: list[dict]) -> tuple[int, int, list[dict]]:
    """Fetch distinct map/mod combinations, isolating missing or malformed maps."""
    seen, calls, errors = set(), 0, []
    for sc in scores:
        mods = S.score_mods(sc)
        k = (sc["beatmap_id"], M.key(mods))
        if k in seen:
            continue
        seen.add(k)
        try:
            _, called = fetch_beatmap(c, sc["beatmap_id"], mods)
        except (MapDataError, HttpError) as error:
            if isinstance(error, HttpError) and error.status != 404:
                raise
            errors.append({"beatmap_id": sc["beatmap_id"], "mods": mods, "error": str(error)})
            continue
        calls += called
    fill_map_max_combo(scores)
    return len(seen), calls, errors


def fill_map_max_combo(scores: list[dict]) -> None:
    """Use only attributes calculated for the score's recorded mod combination."""
    for sc in scores:
        data = read_json(beatmap_path(sc["beatmap_id"], S.score_mods(sc)))
        sc["map_max_combo"] = data.get("max_combo") if data else None
