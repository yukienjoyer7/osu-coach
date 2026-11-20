"""API -> data/ files, per DATA_CONTRACT.md. All network calls go through api.Client."""
from datetime import timedelta

from . import mods as M
from . import paths as P
from . import scores as S
from .api import Client, OsuError
from .store import now_local, parse_ts, read_json, write_json

THRESHOLD_PAGE = 200  # last row of rankings page 200 = rank #10,000
SESSION_GRACE = timedelta(minutes=5)


# ---- profile / threshold -------------------------------------------------
def profile_path(day: str | None = None):
    return P.PROFILE_DIR / f"{day or now_local().strftime('%Y-%m-%d')}.json"


def latest_profile() -> dict | None:
    files = sorted(P.PROFILE_DIR.glob("*.json")) if P.PROFILE_DIR.exists() else []
    return read_json(files[-1]) if files else None


def fetch_profile(c: Client) -> dict:
    u = c.get(f"/users/{c.user_id}/osu")
    st = u["statistics"]
    path = profile_path()
    old = read_json(path, {})
    prof = {
        "fetched_at": now_local().isoformat(timespec="seconds"),
        "pp": st["pp"],
        "global_rank": st["global_rank"],
        "country_rank": st["country_rank"],
        "accuracy": round(st["hit_accuracy"], 2),
        "play_count": st["play_count"],
        "play_time_s": st["play_time"],
        "rank_threshold_10k_pp": old.get("rank_threshold_10k_pp"),  # keep today's value
    }
    write_json(path, prof)
    return prof


def fetch_threshold(c: Client) -> dict:
    r = c.get("/rankings/osu/performance", **{"cursor[page]": THRESHOLD_PAGE})
    rows = r.get("ranking") or []
    if not rows:
        raise OsuError("rankings page returned no rows")
    pp = rows[-1]["pp"]
    path = profile_path()
    prof = read_json(path) or fetch_profile(c)
    prof["rank_threshold_10k_pp"] = pp
    write_json(path, prof)
    return prof


# ---- scores --------------------------------------------------------------
def _save_meta(raw: dict) -> None:
    meta = S.beatmap_meta(raw)
    if meta:
        write_json(P.META_DIR / f"{meta['id']}.json", meta)


def fetch_best(c: Client) -> list[dict]:
    raw = c.get(f"/users/{c.user_id}/scores/best", mode="osu", limit=100)
    for r in raw:
        _save_meta(r)
    scores = [S.map_score(r) for r in raw]
    fill_map_max_combo(scores)
    write_json(P.BEST, scores)
    return scores


def _recent_raw(c: Client) -> list[dict]:
    out: list[dict] = []
    for page in range(5):  # API caps at 100/page; recent covers ~24h
        chunk = c.get(f"/users/{c.user_id}/scores/recent", mode="osu",
                      include_fails=1, limit=100, offset=page * 100)
        out += chunk
        if len(chunk) < 100:
            break
    return out


def in_session(created_at: str, start, end) -> bool:
    t = parse_ts(created_at)
    return t >= start and (end is None or t <= end + SESSION_GRACE)


def fetch_recent(c: Client, session_id: str, session: dict) -> list[dict]:
    """Fetch recent scores inside the session window, merge with any saved, write scores.json."""
    start = parse_ts(session["started_at"])
    end = parse_ts(session["ended_at"]) if session.get("ended_at") else None
    path = P.session_dir(session_id) / "scores.json"
    merged = {s["score_id"]: s for s in read_json(path, [])}  # recent only spans 24h: keep old
    for r in _recent_raw(c):
        sc = S.map_score(r)
        if sc["created_at"] and in_session(sc["created_at"], start, end):
            _save_meta(r)
            merged[sc["score_id"]] = sc
    scores = S.add_retry_index(list(merged.values()))
    fill_map_max_combo(scores)
    write_json(path, scores)
    return scores


# ---- beatmaps ------------------------------------------------------------
def _r(v, nd):
    return None if v is None else round(v, nd)


def beatmap_path(beatmap_id: int, mods: list[dict]):
    return P.BEATMAPS / f"{beatmap_id}_{M.key(mods)}.json"


def fetch_beatmap(c: Client, beatmap_id: int, mods: list[dict]) -> tuple[dict, bool]:
    """Return (beatmap data, made_api_calls). Cached forever by id + difficulty mods."""
    path = beatmap_path(beatmap_id, mods)
    cached = read_json(path)
    if cached:
        return cached, False
    norm = M.normalize(mods)
    meta_path = P.META_DIR / f"{beatmap_id}.json"
    meta = read_json(meta_path)
    if not meta:
        b = c.get(f"/beatmaps/{beatmap_id}")
        meta = {"id": b["id"], "cs": b["cs"], "ar": b["ar"], "accuracy": b["accuracy"],
                "bpm": b["bpm"], "total_length": b["total_length"],
                "count_circles": b["count_circles"], "count_sliders": b["count_sliders"],
                "count_spinners": b["count_spinners"]}
        write_json(meta_path, meta)
    body_mods = [m if m.get("settings") else m["acronym"] for m in norm]
    a = c.post(f"/beatmaps/{beatmap_id}/attributes", {"mods": body_mods, "ruleset": "osu"})
    a = a["attributes"]
    adj = M.adjust(meta["cs"], meta["ar"], meta["accuracy"], meta["bpm"],
                   meta["total_length"], norm)
    data = {
        "beatmap_id": beatmap_id,
        "mods": [m["acronym"] for m in norm],
        "star_rating": round(a["star_rating"], 3),
        # some maps come back with only star_rating + max_combo 0: keep the rest null
        "aim": _r(a.get("aim_difficulty"), 3),
        "speed": _r(a.get("speed_difficulty"), 3),
        "slider_factor": _r(a.get("slider_factor"), 3),
        "speed_note_count": _r(a.get("speed_note_count"), 1),
        **adj,
        "n_circles": meta["count_circles"],
        "n_sliders": meta["count_sliders"],
        "n_spinners": meta["count_spinners"],
        "max_combo": a.get("max_combo") or None,
    }
    write_json(path, data)
    return data, True


def fetch_beatmaps_for(c: Client, scores: list[dict]) -> tuple[int, int]:
    """Fetch every distinct (beatmap, mods) in scores. Returns (maps, maps_that_hit_the_api)."""
    seen, calls = set(), 0
    for sc in scores:
        mods = S.score_mods(sc)
        k = (sc["beatmap_id"], M.key(mods))
        if k in seen:
            continue
        seen.add(k)
        _, called = fetch_beatmap(c, sc["beatmap_id"], mods)
        calls += called
    fill_map_max_combo(scores)
    return len(seen), calls


def fill_map_max_combo(scores: list[dict]) -> None:
    """Set map_max_combo from any cached beatmap file of that map (mod independent)."""
    if not P.BEATMAPS.exists():
        return
    for sc in scores:
        if sc.get("map_max_combo") is None:
            for f in P.BEATMAPS.glob(f"{sc['beatmap_id']}_*.json"):
                d = read_json(f)
                if d and d.get("max_combo"):
                    sc["map_max_combo"] = d["max_combo"]
                    break
