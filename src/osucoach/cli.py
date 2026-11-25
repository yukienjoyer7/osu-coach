"""Thin typer layer. Every command prints a short summary; --json prints machine output."""
import json
import math
import sys
from importlib.resources import files
from urllib.parse import urlencode
from typing import Optional

import typer

from . import account as A
from . import analysis as AN
from . import fetch as F
from . import context as C
from . import gap as G
from . import history as H
from . import hypothesis as HY
from . import maps as MAPS
from . import mods as M
from . import paths as P
from . import scores as S
from . import session as SS
from . import stats as ST
from .api import Client, HttpError, OsuError
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
analysis_app = typer.Typer(no_args_is_help=True, help="Import and inspect external analysis reports.")
app.add_typer(auth_app, name="auth")
app.add_typer(fetch_app, name="fetch")
app.add_typer(session_app, name="session")
app.add_typer(analysis_app, name="analysis")

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


@analysis_app.command("import")
def analysis_import(path: str = typer.Argument(..., help="Path to an external JSON report"),
                    session: str = typer.Option(..., "--session", help="Session ID"),
                    source: str = typer.Option(..., "--source", help="Analyzer name"),
                    version: Optional[str] = typer.Option(None, "--version", help="Analyzer version"),
                    beatmap: int = typer.Option(..., "--beatmap", min=1, help="Reported beatmap ID"),
                    score: Optional[int] = typer.Option(None, "--score", min=1, help="Reported score ID")) -> None:
    """Copy an external report into a session without validating its analysis."""
    sid = _run(SS.resolve, session)
    result = _run(AN.import_report, sid, path, source, version, beatmap, score)
    typer.echo(json.dumps(result, ensure_ascii=False))


@analysis_app.command("list")
def analysis_list(session: str = typer.Option(..., "--session", help="Session ID")) -> None:
    """List imported report metadata for a session."""
    sid = _run(SS.resolve, session)
    typer.echo(json.dumps({"reports": AN.list_reports(sid)}, ensure_ascii=False))


@analysis_app.command("show")
def analysis_show(report_id: str, session: str = typer.Option(..., "--session", help="Session ID")) -> None:
    """Show an imported, unverified external report."""
    sid = _run(SS.resolve, session)
    typer.echo(json.dumps(_run(AN.show_report, sid, report_id), ensure_ascii=False))


@app.command("init")
def init(json_out: bool = JSON) -> None:
    """Initialize coaching files in the workspace without overwriting existing files."""
    templates = {
        P.COACH / "player.md": "# Player\n\n- user: <osu! username and id>\n- goal: <your goals>\n- history: <experience and breaks>\n- input: <tablet or mouse>\n- client: <lazer or stable>\n- rapid trigger: <optional settings>\n- actuation: <optional settings>\n\n## Open hypotheses\n",
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
    _run(A.check, c.user_id)
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
    """Fetch the pp threshold for a global rank into the latest profile."""
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
    selected = _run(M.parse_mods, mods)
    if rate is not None:
        _run(M.set_rate, selected, rate)
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
        sid = _run(SS.resolve, session)
        path = P.session_dir(sid) / "scores.json"
    else:
        path = P.BEST
    scores = read_json(path)
    if scores is None:
        _fail(OsuError(f"{path.name} missing; fetch it first"))
    maps, api, errors = _run(F.fetch_beatmaps_for, _client(), scores)
    from .store import write_json
    write_json(path, scores)  # map_max_combo may now be filled
    if session:
        F.save_map_sync(sid, maps, api, errors)
    _out({"maps": maps, "fetched": api, "errors": errors}, json_out,
         f"{maps} maps ({api} fetched, {maps - api - len(errors)} cached, {len(errors)} unavailable)")


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
                           "requested_scope": "ranked osu!standard beatmapsets", "scope_verified": False}, ensure_ascii=False))


@app.command("discover")
def discover(query: str = typer.Argument("", help="osu! website search query"),
             cursor: Optional[str] = typer.Option(None, "--cursor", help="Cursor returned by a previous search"),
             min_sr: Optional[float] = typer.Option(None, "--min-sr", min=0, help="Local nomod minimum star rating"),
             max_sr: Optional[float] = typer.Option(None, "--max-sr", min=0, help="Local nomod maximum star rating"),
             tag: Optional[str] = typer.Option(None, "--tag", help="Filter by a player-supplied map label")) -> None:
    """Search osu! for candidate beatmapsets and output JSON."""
    if any(v is not None and not math.isfinite(v) for v in (min_sr, max_sr)):
        _fail(OsuError("star range must contain finite numbers"))
    if min_sr is not None and max_sr is not None and min_sr > max_sr:
        _fail(OsuError("minimum must not exceed maximum"))
    if tag is not None and not tag.strip():
        _fail(OsuError("--tag cannot be blank"))
    result = _run(MAPS.discover, _client(), query, cursor, min_sr, max_sr, tag.strip() if tag else None)
    typer.echo(json.dumps(result, ensure_ascii=False, indent=2))


@app.command("map-attributes")
def map_attributes(beatmap_ids: str = typer.Argument(..., help="Comma-separated beatmap IDs, up to 25"),
                   mods: Optional[str] = typer.Option(None, "--mods", help="e.g. HD,DT"),
                   rate: Optional[float] = typer.Option(None, "--rate", help="Custom DT/NC/HT/DC rate"),
                   refresh: bool = typer.Option(False, "--refresh", help="Refresh cached attributes")) -> None:
    """Fetch comparable mod-adjusted attributes for a small set of candidates."""
    parts = [part.strip() for part in beatmap_ids.split(",")]
    if not parts or len(parts) > 25 or any(not part.isascii() or not part.isdecimal() or int(part) < 1 for part in parts):
        _fail(OsuError("provide 1 to 25 positive comma-separated beatmap IDs"))
    ids = list(dict.fromkeys(int(part) for part in parts))
    selected = _run(M.parse_mods, mods)
    if rate is not None:
        _run(M.set_rate, selected, rate)
    client = _client()
    results = []
    stopped_early = False
    for index, beatmap_id in enumerate(ids):
        try:
            attributes, fetched = F.fetch_beatmap(client, beatmap_id, selected, refresh)
        except OsuError as exc:
            results.append({"beatmap_id": beatmap_id, "attributes": None, "error": str(exc)})
            if not isinstance(exc, F.MapDataError) and not (isinstance(exc, HttpError) and exc.status == 404):
                stopped_early = True
                results.extend({"beatmap_id": remaining, "attributes": None,
                                "error": "not attempted after batch error", "skipped": True}
                               for remaining in ids[index + 1:])
                break
        else:
            results.append({"beatmap_id": beatmap_id, "attributes": attributes, "fetched": fetched, "error": None})
    typer.echo(json.dumps({"results": results, "stopped_early": stopped_early, "selection_limits": [
        "Attributes do not establish ranked status, pattern type or player familiarity.",
        "BPM is map-level tempo and total length includes breaks; verify the relevant section.",
    ]}, ensure_ascii=False, indent=2))
    if stopped_early:
        raise typer.Exit(1)


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
    diagnostics = {}
    results = MAPS.find(min_sr, max_sr, min_bpm, max_bpm, min_ar, max_ar, min_length, max_length,
                        tag.strip() if tag else None, diagnostics)
    typer.echo(json.dumps({"maps": results, "excluded_score_records": diagnostics,
                           "recorded_history": MAPS.recorded_history(), "selection_limits": [
        "Cached BPM is a map-level tempo, not a measurement of stream or tapping speed; tempo can vary.",
        "Total length includes breaks and does not measure continuous playing time.",
        "Pattern type and ranked status are not verified by this command.",
        "Saved score counts are incomplete and may contain legacy records of unknown origin.",
    ]}, ensure_ascii=False, indent=2))


@app.command("history")
def history(beatmap_id: int = typer.Argument(..., min=1)) -> None:
    """Show saved session scores and conditions for a beatmap as JSON."""
    typer.echo(json.dumps(H.map_history(beatmap_id), ensure_ascii=False, indent=2))


@app.command("hypothesis")
def hypothesis(tag: str = typer.Argument(..., help="Explicit tag such as H-aim"),
               through: Optional[str] = typer.Option(None, "--through", help="Include this session and earlier starts"),
               before: Optional[str] = typer.Option(None, "--before", help="Earlier sessions and observations reported before this session")) -> None:
    """Find tagged plans, reviews, player observations and journal entries as JSON."""
    result = _run(HY.history, tag, through, before)
    typer.echo(json.dumps(result, ensure_ascii=False, indent=2))


@app.command("scores")
def session_scores(session: Optional[str] = typer.Argument(None, help="Session id (default: current/last)")) -> None:
    """Show saved session scores and their coverage metadata as JSON."""
    sid = _run(SS.resolve, session)
    sess = SS.load(sid)
    result = {"session_id": sid, "started_at": sess.get("started_at"), "ended_at": sess.get("ended_at"),
              "account": A.binding(), "scores_sync": F.recent_sync(sid),
              "scores": read_json(P.session_dir(sid) / "scores.json")}
    typer.echo(json.dumps(result, ensure_ascii=False, indent=2))


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
    binding = A.binding()
    binding_id = str(binding.get("user_id", "")) if binding else ""
    profile_id = prof.get("user_id")
    score_ids = [row.get("user_id") for row in best]
    known_ids = {value for value in [profile_id, *score_ids] if type(value) is int and value > 0}
    if binding_id.isdecimal() and int(binding_id) > 0:
        known_ids.add(int(binding_id))
    if len(known_ids) > 1:
        _fail(OsuError("profile, best scores or workspace binding identify different users; refresh in the correct workspace"))
    identity = {"profile_user_id": profile_id, "best_scores_with_user_id": sum(
        type(value) is int and value > 0 for value in score_ids), "best_scores_total": len(best),
        "workspace_account": binding}
    entry = prof.get("rank_thresholds", {}).get(str(rank))
    thr = entry["pp"] if entry else (prof.get("rank_threshold_10k_pp") if rank == 10000 else None)
    if thr is None:
        _fail(OsuError(f"no threshold: run `osucoach fetch threshold --rank {rank}`"))
    if (type(prof.get("pp")) not in (int, float) or not math.isfinite(prof["pp"]) or prof["pp"] < 0 or
            type(thr) not in (int, float) or not math.isfinite(thr) or thr < 0):
        _fail(OsuError("profile or threshold contains invalid pp; refresh both"))
    try:
        values = [float(t) for t in targets.split(",")]
        if not all(math.isfinite(value) and value > 0 for value in values):
            raise ValueError
    except ValueError:
        _fail(OsuError("--pp must be a comma-separated list of positive finite numbers"))
    best_pps = [b.get("pp") for b in best]
    if any(type(value) not in (int, float) or not math.isfinite(value) or value < 0 for value in best_pps):
        _fail(OsuError("best scores contain missing or invalid pp; refresh best scores"))
    tbl = G.table(best_pps, prof["pp"], thr, values)
    sync = read_json(P.BEST.with_name("best_sync.json")) or {}
    res = {"pp": prof["pp"], "rank": rank, "threshold": thr, "gap": round(thr - prof["pp"], 1),
           "identity_evidence": identity,
           "threshold_fetched_at": entry.get("fetched_at") if entry else None,
           "profile_date": prof["fetched_at"][:10], "profile_fetched_at": prof["fetched_at"],
           "best_fetched_at": sync.get("fetched_at"),
           "fixed_residual_pp": round(prof["pp"] - G.weighted(sorted(best_pps, reverse=True)[:100]), 2),
           "model": "top 100 only; fixed residual and threshold; distinct new scores that do not replace existing top scores",
           "table": tbl}
    lines = [f"pp {prof['pp']:,.0f} | #{rank:,} = {thr:,.0f} | gap {res['gap']:,.0f} "
             f"(profile {res['profile_fetched_at']})",
             f"best fetched: {res['best_fetched_at'] or 'unknown'}; threshold fetched: {res['threshold_fetched_at'] or 'unknown'}",
             f"identity: profile {profile_id or 'unknown'}; best scores with user ID {identity['best_scores_with_user_id']}/{len(best)}",
             "scenario: top 100 only; fixed residual and threshold; new scores do not replace existing top scores",
             "new play pp -> plays needed"]
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


@session_app.command("observe")
def session_observe(text: str = typer.Argument(..., help="Player observation to record"),
                    beatmap: Optional[int] = typer.Option(None, "--beatmap", min=1,
                                                          help="Map ID only when the player identifies it."),
                    session: Optional[str] = typer.Option(None, "--session", help="Explicit session ID for a retrospective report."),
                    json_out: bool = JSON) -> None:
    """Record a player observation, optionally for an explicitly selected session."""
    sid = _run(SS.resolve, session) if session else SS.current()
    if not sid:
        _fail(OsuError("no open session"))
    sess = SS.load(sid)
    if not session and sess.get("ended_at"):
        _fail(OsuError("session has ended; specify --session for a retrospective report"))
    observation = _run(SS.add_observation, sess, text, beatmap)
    _out(observation, json_out, f"{sid}: observation recorded")


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
    maps, api, errors = _run(F.fetch_beatmaps_for, c, scores)
    from .store import write_json
    write_json(P.session_dir(sid) / "scores.json", scores)
    F.save_map_sync(sid, maps, api, errors)
    SS.save(sess)
    SS.close(sid)
    sync = F.recent_sync(sid)
    warning = " (recent score limit reached; coverage may be incomplete)" if sync and sync.get("limit_reached_ever") else ""
    _out({"session_id": sid, "scores": len(scores), "maps": maps, "map_errors": errors, "scores_sync": sync}, json_out,
         f"session {sid} ended: {len(scores)} scores, {maps} maps ({api} fetched, {len(errors)} unavailable){warning}")


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
        if sc.get("mods_known") is False:
            return None
        d = read_json(F.beatmap_path(sc["beatmap_id"], S.score_mods(sc)))
        return d.get("star_rating") if isinstance(d, dict) else None

    sess = SS.load(sid)
    res = ST.compute(scores, sr, sess.get("blocks"), sess.get("ended_at"), sess.get("started_at"))
    res["session_id"] = sid
    _out(res, json_out, ST.render(sid, res))
