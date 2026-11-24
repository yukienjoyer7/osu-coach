"""Thin typer layer. Every command prints a short summary; --json prints machine output."""
import json
import math
import sys
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


def _run(fn, *a, **kw):
    try:
        return fn(*a, **kw)
    except OsuError as e:
        _fail(e)


def _client() -> Client:
    return _run(Client)


# ---- auth ----------------------------------------------------------------
@auth_app.command("check")
def auth_check(json_out: bool = JSON) -> None:
    """Token works; print user + pp."""
    c = _client()
    u = _run(c.get, f"/users/{c.user_id}/osu")
    st = u["statistics"]
    _out({"username": u["username"], "pp": st["pp"], "global_rank": st["global_rank"]}, json_out,
         f"{u['username']} | {st['pp']:,.0f} pp | #{st['global_rank']:,}")


# ---- fetch ---------------------------------------------------------------
@fetch_app.command("profile")
def fetch_profile(json_out: bool = JSON) -> None:
    """Snapshot profile -> data/profile/YYYY-MM-DD.json."""
    p = _run(F.fetch_profile, _client())
    _out(p, json_out, f"{p['pp']:,.0f} pp | #{p['global_rank']:,} | acc {p['accuracy']}% "
                      f"| {p['play_count']:,} plays")


@fetch_app.command("best")
def fetch_best(json_out: bool = JSON) -> None:
    """Top 100 plays -> data/best.json."""
    s = _run(F.fetch_best, _client())
    _out({"scores": len(s)}, json_out, f"best.json: {len(s)} scores, top {s[0]['pp']:.0f}pp")


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
    _out({"session_id": sid, "scores": len(scores)}, json_out,
         f"{sid}: {len(scores)} scores saved")


@fetch_app.command("beatmap")
def fetch_beatmap(beatmap_id: int, mods: Optional[str] = typer.Option(None, "--mods", help="e.g. HD,DT"),
                  json_out: bool = JSON) -> None:
    """Mod-adjusted difficulty + map stats -> data/beatmaps/ID_MODS.json (cached)."""
    d, called = _run(F.fetch_beatmap, _client(), beatmap_id, M.parse_mods(mods))
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
@app.command("maps")
def maps(min_sr: Optional[float] = typer.Option(None, "--min-sr", min=0),
         max_sr: Optional[float] = typer.Option(None, "--max-sr", min=0),
         min_bpm: Optional[float] = typer.Option(None, "--min-bpm", min=0),
         max_bpm: Optional[float] = typer.Option(None, "--max-bpm", min=0)) -> None:
    """Find recorded maps with cached attributes and output JSON."""
    if ((min_sr is not None and max_sr is not None and min_sr > max_sr)
            or (min_bpm is not None and max_bpm is not None and min_bpm > max_bpm)):
        _fail(OsuError("minimum must not exceed maximum"))
    results = MAPS.find(min_sr, max_sr, min_bpm, max_bpm)
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
    if not prof or not best:
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
    tbl = G.table([b["pp"] for b in best], prof["pp"], thr, values)
    res = {"pp": prof["pp"], "rank": rank, "threshold": thr, "gap": round(thr - prof["pp"], 1),
           "threshold_fetched_at": entry.get("fetched_at") if entry else None,
           "profile_date": prof["fetched_at"][:10], "table": tbl}
    lines = [f"pp {prof['pp']:,.0f} | #{rank:,} = {thr:,.0f} | gap {res['gap']:,.0f} "
             f"(profile {res['profile_date']})", "new play pp -> plays needed"]
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
    _out({"session_id": sid, "scores": len(scores), "maps": maps}, json_out,
         f"session {sid} ended: {len(scores)} scores, {maps} maps ({api} fetched)")


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

    res = ST.compute(scores, sr, SS.load(sid).get("blocks"))
    res["session_id"] = sid
    _out(res, json_out, ST.render(sid, res))
