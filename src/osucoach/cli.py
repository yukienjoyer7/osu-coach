"""Thin typer layer. Every command prints a short summary; --json prints machine output."""
import json
import math
import sys
from importlib.resources import files
from urllib.parse import urlencode
from typing import Optional

import typer

from . import fetch as F
from . import context as C
from . import gap as G
from . import history as H
from . import maps as MAPS
from . import mods as M
from . import paths as P
from . import scores as S
from . import session as SS
from . import stats as ST
from .api import Client, OsuError
from .store import read_json

for _s in (sys.stdout, sys.stderr):  # map/player titles contain unicode
    try:
        _s.reconfigure(encoding="utf-8")
    except Exception:
        pass

app = typer.Typer(no_args_is_help=True, add_completion=False)
auth_app = typer.Typer(no_args_is_help=True, help="Check API credentials.")
fetch_app = typer.Typer(no_args_is_help=True, help="Fetch data from the osu! API into data/.")
session_app = typer.Typer(no_args_is_help=True, help="Open, close and annotate sessions.")
app.add_typer(auth_app, name="auth")
app.add_typer(fetch_app, name="fetch")
app.add_typer(session_app, name="session")

JSON = typer.Option(False, "--json", help="Machine-readable output.")


def _fail(e: Exception):
    typer.echo(f"error: {e}", err=True)
    raise typer.Exit(1)


def _out(obj, as_json: bool, text: str) -> None:
    typer.echo(json.dumps(obj, ensure_ascii=False) if as_json else text)


def _rank(value):
    return f"#{value:,}" if value is not None else "unranked"


def _run(fn, *a, **kw):
    try:
        return fn(*a, **kw)
    except OsuError as e:
        _fail(e)


def _client() -> Client:
    return _run(Client)


@app.command("init")
def init(json_out: bool = JSON) -> None:
    """Initialize coaching files in the workspace without overwriting existing files."""
    templates = {
        P.COACH / "player.md": "# Player\n\n- user: <osu! username and id>\n- goal: <your goals>\n- history: <experience and breaks>\n- input: <tablet or mouse>\n- client: <lazer or stable>\n\n## Open hypotheses\n",
        P.COACH / "journal.md": "# Journal\n\nAppend-only. One entry per session, newest at the bottom.\n",
    }
    created, existing = [], []
    for path, content in templates.items():
        path.parent.mkdir(parents=True, exist_ok=True)
        try:
            with path.open("x", encoding="utf-8") as out:
                out.write(content)
        except FileExistsError:
            existing.append(str(path.relative_to(P.ROOT)))
        else:
            created.append(str(path.relative_to(P.ROOT)))
    for directory in (P.COACH / "plans", P.COACH / "reviews"):
        directory.mkdir(parents=True, exist_ok=True)
    result = {"workspace": str(P.ROOT), "created": created, "existing": existing}
    _out(result, json_out, f"workspace {P.ROOT}: {len(created)} files created, {len(existing)} already present")


# ---- auth ----------------------------------------------------------------
@auth_app.command("check")
def auth_check(json_out: bool = JSON) -> None:
    """Token works; print user + pp."""
    c = _client()
    u = _run(c.get, f"/users/{c.user_id}/osu")
    st = u["statistics"]
    _out({"username": u["username"], "pp": st["pp"], "global_rank": st["global_rank"]}, json_out,
         f"{u['username']} | {st['pp']:,.0f} pp | {_rank(st['global_rank'])}")


# ---- fetch ---------------------------------------------------------------
@fetch_app.command("profile")
def fetch_profile(json_out: bool = JSON) -> None:
    """Snapshot profile -> data/profile/YYYY-MM-DD.json."""
    p = _run(F.fetch_profile, _client())
    _out(p, json_out, f"{p['pp']:,.0f} pp | {_rank(p['global_rank'])} | acc {p['accuracy']}% "
                      f"| {p['play_count']:,} plays")


@fetch_app.command("best")
def fetch_best(json_out: bool = JSON) -> None:
    """Top 100 plays -> data/best.json."""
    s = _run(F.fetch_best, _client())
    summary = f"best.json: {len(s)} scores"
    if s and s[0].get("pp") is not None:
        summary += f", top {s[0]['pp']:.0f}pp"
    _out({"scores": len(s)}, json_out, summary)


@fetch_app.command("threshold")
def fetch_threshold(rank: int = typer.Option(10000, "--rank", min=1), json_out: bool = JSON) -> None:
    """Fetch the pp threshold for a global rank into today's profile."""
    p = _run(F.fetch_threshold, _client(), rank)
    threshold = p["rank_thresholds"][str(rank)]
    result = {"rank": rank, **threshold}
    if rank == 10000:
        result["rank_threshold_10k_pp"] = threshold["pp"]
    _out(result, json_out, f"#{rank:,} = {threshold['pp']:,.0f} pp")


@fetch_app.command("recent")
def fetch_recent(session: Optional[str] = typer.Option(None, "--session", help="Session id (default: current)"),
                 json_out: bool = JSON) -> None:
    """Scores (incl. fails) of a session -> sessions/<id>/scores.json."""
    sid = _run(SS.resolve, session)
    scores = _run(F.fetch_recent, _client(), sid, SS.load(sid))
    sync = F.recent_sync(sid)
    warning = " (recent score limit reached; coverage may be incomplete)" if sync and sync.get("limit_reached_ever") else ""
    _out({"session_id": sid, "scores": len(scores), "scores_sync": sync}, json_out,
         f"{sid}: {len(scores)} scores saved{warning}")


@fetch_app.command("beatmap")
def fetch_beatmap(beatmap_id: int, mods: Optional[str] = typer.Option(None, "--mods", help="e.g. HD,DT"),
                  rate: Optional[float] = typer.Option(None, "--rate", help="Custom DT/NC/HT/DC speed multiplier."),
                  ar: Optional[float] = typer.Option(None, "--ar", help="Difficulty Adjust AR."),
                  od: Optional[float] = typer.Option(None, "--od", help="Difficulty Adjust OD."),
                  cs: Optional[float] = typer.Option(None, "--cs", help="Difficulty Adjust CS."),
                  refresh: bool = typer.Option(False, "--refresh", help="Refresh cached attributes."),
                  json_out: bool = JSON) -> None:
    """Mod-adjusted difficulty + map stats -> data/beatmaps/ID_MODS.json (cached)."""
    selected = M.parse_mods(mods)
    if rate is not None:
        speed_mods = [mod for mod in selected if mod["acronym"] in M.SPEED]
        if not math.isfinite(rate) or rate <= 0 or len(speed_mods) != 1:
            _fail(OsuError("--rate requires a positive finite value and exactly one DT, NC, HT or DC mod"))
        speed_mods[0]["settings"] = {"speed_change": rate}
    adjustments = {key: value for key, value in (("approach_rate", ar), ("overall_difficulty", od),
                                                 ("circle_size", cs)) if value is not None}
    if adjustments:
        da_mods = [mod for mod in selected if mod["acronym"] == "DA"]
        if (len(da_mods) != 1 or any(not math.isfinite(value) or not 0 <= value <= 10
                                      for value in adjustments.values())):
            _fail(OsuError("--ar, --od and --cs require DA and finite values from 0 to 10"))
        da_mods[0]["settings"] = adjustments
    d, called = _run(F.fetch_beatmap, _client(), beatmap_id, selected, refresh)
    _out(d, json_out, f"{beatmap_id} {''.join(d['mods']) or 'NM'}: {d['star_rating']}* "
                      f"AR{d['ar']} OD{d['od']} CS{d['cs']} {d['bpm']}bpm "
                      f"{'(fetched)' if called else '(cached)'}")


@fetch_app.command("beatmaps")
def fetch_beatmaps(session: Optional[str] = typer.Option(None, "--session", help="Session id; default: best.json"),
                   json_out: bool = JSON) -> None:
    """Fetch beatmap files for every score in best.json (or a session)."""
    if session:
        path = P.session_dir(_run(SS.resolve, session)) / "scores.json"
    else:
        path = P.BEST
    scores = read_json(path)
    if scores is None:
        _fail(OsuError(f"{path.name} missing; fetch it first"))
    maps, api = _run(F.fetch_beatmaps_for, _client(), scores)
    from .store import write_json
    write_json(path, scores)  # map_max_combo may now be filled
    _out({"maps": maps, "fetched": api}, json_out, f"{maps} maps ({api} fetched, {maps - api} cached)")


# ---- gap -----------------------------------------------------------------
@app.command("guide")
def guide() -> None:
    """Print the coaching playbook bundled with the installed package."""
    typer.echo(files("osucoach").joinpath("COACHING.md").read_text(encoding="utf-8"))


@app.command("schema")
def schema() -> None:
    """Print the data contract bundled with the installed package."""
    typer.echo(files("osucoach").joinpath("DATA_CONTRACT.md").read_text(encoding="utf-8"))


@app.command("map-tag")
def map_tag(beatmap_id: int = typer.Argument(..., min=1),
            tags: str = typer.Option(..., "--tags", help="Comma-separated labels; empty clears them.")) -> None:
    """Replace the user-supplied labels for a beatmap."""
    result = MAPS.set_tags(beatmap_id, tags.split(","))
    typer.echo(json.dumps(result, ensure_ascii=False))


@app.command("map-search")
def map_search(query: str = typer.Argument(..., help="osu! website search query")) -> None:
    """Build an osu!standard ranked-map search URL without requesting data."""
    params = urlencode({"m": "0", "s": "ranked", "q": query})
    typer.echo(json.dumps({"url": f"https://osu.ppy.sh/beatmapsets?{params}", "query": query,
                           "scope": "ranked osu!standard beatmapsets"}, ensure_ascii=False))


@app.command("maps")
def maps(min_sr: Optional[float] = typer.Option(None, "--min-sr", min=0),
         max_sr: Optional[float] = typer.Option(None, "--max-sr", min=0),
         min_bpm: Optional[float] = typer.Option(None, "--min-bpm", min=0),
         max_bpm: Optional[float] = typer.Option(None, "--max-bpm", min=0),
         min_ar: Optional[float] = typer.Option(None, "--min-ar", min=0),
         max_ar: Optional[float] = typer.Option(None, "--max-ar", min=0),
         min_length: Optional[float] = typer.Option(None, "--min-length", min=0, help="Seconds"),
         max_length: Optional[float] = typer.Option(None, "--max-length", min=0, help="Seconds"),
         tag: Optional[str] = typer.Option(None, "--tag", help="User-supplied map label")) -> None:
    """Find recorded maps with cached attributes and output JSON."""
    ranges = ((min_sr, max_sr), (min_bpm, max_bpm), (min_ar, max_ar), (min_length, max_length))
    if any(value is not None and not math.isfinite(value) for pair in ranges for value in pair):
        _fail(OsuError("map filters must be finite numbers"))
    if any(low is not None and high is not None and low > high for low, high in ranges):
        _fail(OsuError("minimum must not exceed maximum"))
    results = MAPS.find(min_sr, max_sr, min_bpm, max_bpm, min_ar, max_ar, min_length, max_length,
                        tag.strip() if tag else None)
    typer.echo(json.dumps({"maps": results}, ensure_ascii=False, indent=2))


@app.command("history")
def history(beatmap_id: int = typer.Argument(..., min=1)) -> None:
    """Show saved session scores and conditions for a beatmap as JSON."""
    typer.echo(json.dumps(H.map_history(beatmap_id), ensure_ascii=False, indent=2))


@app.command("context")
def context(session: Optional[str] = typer.Argument(None, help="Session id (default: current/last)")) -> None:
    """Print saved coaching context as JSON, without fetching or modifying data."""
    typer.echo(json.dumps(_run(C.build, session), ensure_ascii=False, indent=2))


@app.command("gap")
def gap(targets: str = typer.Option(",".join(map(str, G.TARGETS)), "--pp",
                                    help="Comma-separated new-play pp values"),
        rank: int = typer.Option(10000, "--rank", min=1),
        json_out: bool = JSON) -> None:
    """Current pp vs a saved global rank threshold, and plays needed."""
    prof, best = F.latest_profile(), read_json(P.BEST)
    if not prof or best is None:
        _fail(OsuError("need profile and best: run `osucoach fetch profile` and `fetch best`"))
    entry = prof.get("rank_thresholds", {}).get(str(rank))
    thr = entry["pp"] if entry else (prof.get("rank_threshold_10k_pp") if rank == 10000 else None)
    if thr is None:
        _fail(OsuError(f"no threshold: run `osucoach fetch threshold --rank {rank}`"))
    try:
        values = [float(t) for t in targets.split(",")]
        if not all(math.isfinite(value) and value > 0 for value in values):
            raise ValueError
    except ValueError:
        _fail(OsuError("--pp must be a comma-separated list of positive finite numbers"))
    best_pps = [b.get("pp") for b in best]
    if any(type(value) not in (int, float) or not math.isfinite(value) for value in best_pps):
        _fail(OsuError("best scores contain missing or invalid pp; refresh best scores"))
    tbl = G.table(best_pps, prof["pp"], thr, values)
    sync = read_json(P.BEST.with_name("best_sync.json")) or {}
    res = {"pp": prof["pp"], "rank": rank, "threshold": thr, "gap": round(thr - prof["pp"], 1),
           "threshold_fetched_at": entry.get("fetched_at") if entry else None,
           "profile_date": prof["fetched_at"][:10], "profile_fetched_at": prof["fetched_at"],
           "best_fetched_at": sync.get("fetched_at"),
           "fixed_residual_pp": round(prof["pp"] - G.weighted(sorted(best_pps, reverse=True)[:100]), 2),
           "model": "top 100 only; fixed residual and threshold; distinct new scores",
           "table": tbl}
    lines = [f"pp {prof['pp']:,.0f} | #{rank:,} = {thr:,.0f} | gap {res['gap']:,.0f} "
             f"(profile {res['profile_fetched_at']})",
             f"best fetched: {res['best_fetched_at'] or 'unknown'}; threshold fetched: {res['threshold_fetched_at'] or 'unknown'}",
             "scenario: top 100 only; residual and threshold held fixed", "new play pp -> plays needed"]
    lines += [f"  {t['play_pp']:>5g} -> {t['plays_needed'] if t['plays_needed'] is not None else f'not reached within {G.CAP} plays'}"
              for t in tbl]
    _out(res, json_out, "\n".join(lines))


# ---- sessions ------------------------------------------------------------
def _before_opts(sleep_h, energy, pain, motivation) -> dict:
    return {"sleep_h": sleep_h, "energy_1_5": energy, "hand_pain_0_10": pain,
            "motivation_1_5": motivation}


@session_app.command("start")
def session_start(sleep_h: Optional[float] = typer.Option(None, "--sleep-h"),
                  energy: Optional[int] = typer.Option(None, "--energy", help="1-5"),
                  pain: Optional[int] = typer.Option(None, "--pain", help="hand pain 0-10"),
                  motivation: Optional[int] = typer.Option(None, "--motivation", help="1-5"),
                  setup_changed: Optional[bool] = typer.Option(None, "--setup-changed/--setup-same"),
                  json_out: bool = JSON) -> None:
    """Start a session using the setup in coach/player.md."""
    s = _run(SS.start, _before_opts(sleep_h, energy, pain, motivation), setup_changed)
    _out(s, json_out, f"session {s['session_id']} started")


@session_app.command("current")
def session_current(json_out: bool = JSON) -> None:
    """Print the open session id or 'none'."""
    sid = SS.current()
    _out({"session_id": sid}, json_out, sid or "none")


@session_app.command("block")
def session_block(label: str = typer.Argument(..., help="Name of the block beginning now"),
                  json_out: bool = JSON) -> None:
    """Mark the start of a practice block in the current session."""
    sid = SS.current()
    if not sid:
        _fail(OsuError("no open session"))
    marker = _run(SS.mark_block, SS.load(sid), label)
    _out(marker, json_out, f"{sid}: block {marker['label']} started")


def _after_opts(fatigue, pain, enjoyment, felt_hard, notes) -> dict:
    return {"fatigue_1_5": fatigue, "hand_pain_0_10": pain, "enjoyment_1_5": enjoyment,
            "felt_hard": felt_hard, "notes": notes}


@session_app.command("end")
def session_end(fatigue: Optional[int] = typer.Option(None, "--fatigue", help="1-5"),
                pain: Optional[int] = typer.Option(None, "--pain", help="hand pain 0-10"),
                enjoyment: Optional[int] = typer.Option(None, "--enjoyment", help="1-5"),
                felt_hard: Optional[str] = typer.Option(None, "--felt-hard"),
                notes: Optional[str] = typer.Option(None, "--notes"),
                followed_plan: Optional[str] = typer.Option(None, "--followed-plan", help="yes|partly|no"),
                offline: bool = typer.Option(False, "--offline", help="Close without fetching scores."),
                json_out: bool = JSON) -> None:
    """Stamp end, fetch recent scores and their beatmaps, close the session."""
    sid = SS.current()
    if not sid:
        _fail(OsuError("no open session"))
    sess = SS.load(sid)
    _run(SS.apply_updates, sess, after=_after_opts(fatigue, pain, enjoyment, felt_hard, notes),
         followed_plan=followed_plan)
    SS.stamp_end(sess)
    SS.save(sess)
    if offline:
        SS.close(sid)
        _out({"session_id": sid, "synced": False}, json_out,
             f"session {sid} ended offline; run `osucoach fetch recent --session {sid}` soon")
        return
    c = _client()
    scores = _run(F.fetch_recent, c, sid, sess)
    maps, api = _run(F.fetch_beatmaps_for, c, scores)
    from .store import write_json
    write_json(P.session_dir(sid) / "scores.json", scores)
    SS.save(sess)
    SS.close(sid)
    sync = F.recent_sync(sid)
    warning = " (recent score limit reached; coverage may be incomplete)" if sync and sync.get("limit_reached_ever") else ""
    _out({"session_id": sid, "scores": len(scores), "maps": maps, "scores_sync": sync}, json_out,
         f"session {sid} ended: {len(scores)} scores, {maps} maps ({api} fetched){warning}")


@session_app.command("update")
def session_update(session: Optional[str] = typer.Argument(None, help="Session id (default: current/last)"),
                   sleep_h: Optional[float] = typer.Option(None, "--sleep-h"),
                   energy: Optional[int] = typer.Option(None, "--energy"),
                   motivation: Optional[int] = typer.Option(None, "--motivation"),
                   pain_before: Optional[int] = typer.Option(None, "--pain-before"),
                   fatigue: Optional[int] = typer.Option(None, "--fatigue"),
                   pain_after: Optional[int] = typer.Option(None, "--pain-after"),
                   enjoyment: Optional[int] = typer.Option(None, "--enjoyment"),
                   felt_hard: Optional[str] = typer.Option(None, "--felt-hard"),
                   notes: Optional[str] = typer.Option(None, "--notes"),
                   followed_plan: Optional[str] = typer.Option(None, "--followed-plan"),
                   setup_changed: Optional[bool] = typer.Option(None, "--setup-changed/--setup-same"),
                   json_out: bool = JSON) -> None:
    """Fill or correct before/after fields of a session (the only way to edit session.json)."""
    sid = _run(SS.resolve, session)
    sess = SS.load(sid)
    _run(SS.apply_updates, sess,
         before=_before_opts(sleep_h, energy, pain_before, motivation),
         after=_after_opts(fatigue, pain_after, enjoyment, felt_hard, notes),
         followed_plan=followed_plan, setup_changed=setup_changed)
    SS.save(sess)
    _out(sess, json_out, f"session {sid} updated")


# ---- stats ---------------------------------------------------------------
@app.command("stats")
def stats(session: Optional[str] = typer.Argument(None, help="Session id (default: current/last)"),
          json_out: bool = JSON) -> None:
    """Summary numbers for a session."""
    sid = _run(SS.resolve, session)
    scores = read_json(P.session_dir(sid) / "scores.json")
    if scores is None:
        _fail(OsuError(f"scores missing for {sid}; run `osucoach fetch recent --session {sid}`"))

    def sr(sc):
        d = read_json(F.beatmap_path(sc["beatmap_id"], S.score_mods(sc)))
        return d["star_rating"] if d else None

    sess = SS.load(sid)
    res = ST.compute(scores, sr, sess.get("blocks"), sess.get("ended_at"))
    res["session_id"] = sid
    _out(res, json_out, ST.render(sid, res))
