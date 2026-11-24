"""Offline tests: no network, no real data/ writes."""
import json
import os
import subprocess
import sys
from datetime import datetime, timedelta, timezone

import pytest
from typer.testing import CliRunner

from osucoach import fetch as F
from osucoach import gap as G
from osucoach import mods as M
from osucoach import paths as P
from osucoach import scores as S
from osucoach import session as SS
from osucoach import stats as ST
from osucoach.api import OsuError
from osucoach.cli import app


# ---- gap -------------------------------------------------------------------
def test_weighted():
    assert G.weighted([100, 100]) == pytest.approx(195)


def test_plays_needed_zero_when_already_there():
    assert G.plays_needed([300] * 100, 9000, 8000, 500) == 0


def test_plays_needed_matches_manual_top_insert():
    # one new 500 play above everything: W' = 500 + 0.95 * W (minus the dropped 100th)
    best = [200.0] * 100
    total = G.weighted(best)  # no bonus
    new = G.weighted([500.0] + best[:99])
    assert G.plays_needed(best, total, new - 0.01, 500) == 1
    assert G.plays_needed(best, total, new + 0.5, 500) == 2


def test_plays_needed_none_when_play_too_weak():
    assert G.plays_needed([300.0] * 100, 8000, 9000, 100) is None


def test_rank_threshold_requires_matching_rank(tmp_path, monkeypatch):
    monkeypatch.setattr(P, "PROFILE_DIR", tmp_path / "profile")

    class Client:
        def get(self, path, **params):
            assert params["cursor[page]"] == 2
            return {"ranking": [{"global_rank": 100, "pp": 20000.0}]}

    from osucoach.store import write_json
    write_json(F.profile_path(), {"pp": 8000, "fetched_at": "2026-01-01T00:00:00Z"})
    profile = F.fetch_threshold(Client(), 100)
    assert profile["rank_thresholds"]["100"]["pp"] == 20000.0
    with pytest.raises(OsuError, match="did not contain"):
        F.fetch_threshold(Client(), 99)


# ---- mods ------------------------------------------------------------------
def test_parse_mods():
    assert [m["acronym"] for m in M.parse_mods("HD,DT")] == ["HD", "DT"]
    assert [m["acronym"] for m in M.parse_mods("hddt")] == ["HD", "DT"]
    assert M.parse_mods("NM") == [] and M.parse_mods(None) == []


def test_key_ignores_non_difficulty_mods_and_aliases():
    assert M.key(M.parse_mods("HD")) == "NM"
    assert M.key(M.parse_mods("HDNC")) == "DT"
    assert M.key(M.parse_mods("HR,DT")) == M.key(M.parse_mods("DTHR")) == "DTHR"


def test_custom_rate_gets_own_key():
    m = M.from_score(["DT"], {"DT": {"speed_change": 1.3}})
    assert M.key(m) == "DT-sp1.3"
    assert M.key(M.from_score(["DT"], {"DT": {"speed_change": 1.5}})) == "DT"


def test_adjust_dt_matches_known_values():
    # AR8.2 + DT = ~9.8, OD8.2 + DT = ~9.91
    a = M.adjust(3.8, 8.2, 8.2, 135, 209, M.parse_mods("DT"))
    assert a["ar"] == pytest.approx(9.8, abs=0.05)
    assert a["od"] == pytest.approx(9.91, abs=0.05)
    assert a["bpm"] == 202.5 and a["length_s"] == 139 and a["cs"] == 3.8


def test_adjust_hr_ez_caps():
    hr = M.adjust(4, 8, 8, 100, 100, M.parse_mods("HR"))
    assert hr["ar"] == 10 and hr["od"] == 10 and hr["cs"] == pytest.approx(5.2)
    ez = M.adjust(4, 8, 8, 100, 100, M.parse_mods("EZ"))
    assert ez["ar"] == 4 and ez["od"] == 4 and ez["cs"] == 2


# ---- score mapping / retry_index ---------------------------------------------
RAW = {
    "id": 5, "beatmap_id": 11, "ended_at": "2026-11-20T10:00:00Z", "passed": True,
    "accuracy": 0.98, "max_combo": 400, "rank": "S", "pp": 300.5,
    "mods": [{"acronym": "HD"}, {"acronym": "DT", "settings": {"speed_change": 1.3}}],
    "statistics": {"great": 500, "ok": 10, "meh": 1, "miss": 2, "ignore_hit": 99},
    "weight": {"percentage": 95.0, "pp": 285.0},
    "beatmap": {"id": 11, "version": "Insane", "beatmapset_id": 7, "cs": 4, "ar": 9,
                "accuracy": 8, "bpm": 180, "total_length": 100, "count_circles": 1,
                "count_sliders": 2, "count_spinners": 0},
    "beatmapset": {"id": 7, "title": "Song"},
}


def test_map_score_lazer_format():
    sc = S.map_score(RAW)
    assert (sc["n300"], sc["n100"], sc["n50"], sc["miss"]) == (500, 10, 1, 2)
    assert sc["mods"] == ["HD", "DT"] and sc["mod_settings"] == {"DT": {"speed_change": 1.3}}
    assert sc["created_at"] == "2026-11-20T10:00:00Z" and sc["weight_pct"] == 95.0
    assert sc["title"] == "Song" and sc["version"] == "Insane" and sc["beatmapset_id"] == 7


def test_map_score_fail_without_pp_or_weight():
    raw = {**RAW, "passed": False, "pp": None, "weight": None, "mods": []}
    sc = S.map_score(raw)
    assert sc["passed"] is False and sc["pp"] is None and sc["weight_pct"] is None
    assert "mod_settings" not in sc


def _sc(i, bid, t, passed=True, acc=0.95):
    return {"score_id": i, "beatmap_id": bid, "created_at": t, "passed": passed,
            "accuracy": acc, "pp": None, "title": "t", "version": "v", "mods": []}


def test_retry_index_counts_per_map_in_time_order():
    rows = [_sc(3, 1, "2026-01-01T00:03:00Z"), _sc(1, 1, "2026-01-01T00:01:00Z"),
            _sc(2, 2, "2026-01-01T00:02:00Z"), _sc(4, 1, "2026-01-01T00:04:00Z")]
    out = S.add_retry_index(rows)
    assert [(r["score_id"], r["retry_index"]) for r in out] == [(1, 1), (2, 1), (3, 2), (4, 3)]


# ---- session window ------------------------------------------------------------
def test_in_session_window():
    from osucoach.store import parse_ts
    start, end = parse_ts("2026-01-01T10:00:00+00:00"), parse_ts("2026-01-01T11:00:00+00:00")
    assert not F.in_session("2026-01-01T09:59:00Z", start, end)
    assert F.in_session("2026-01-01T10:30:00Z", start, end)
    assert F.in_session("2026-01-01T11:04:00Z", start, end)  # inside grace
    assert not F.in_session("2026-01-01T11:10:00Z", start, end)
    assert F.in_session("2030-01-01T00:00:00Z", start, None)  # open session


def test_recent_sync_records_page_limit(tmp_data, monkeypatch):
    from osucoach import context

    monkeypatch.setattr(P, "PROFILE_DIR", tmp_data / "profile")
    monkeypatch.setattr(P, "BEST", tmp_data / "best.json")
    monkeypatch.setattr(F, "_save_meta", lambda raw: None)
    monkeypatch.setattr(F, "fill_map_max_combo", lambda rows: None)
    clock = datetime(2026, 11, 20, 9, 0, tzinfo=timezone.utc)
    monkeypatch.setattr(SS, "now_local", lambda: clock)
    sess = SS.start()

    class Client:
        user_id = "test"

        def get(self, path, **params):
            offset = params["offset"]
            return [{**RAW, "id": offset + i} for i in range(100)]

    rows = F.fetch_recent(Client(), sess["session_id"], sess)
    sync = context.build(sess["session_id"])["scores_sync"]
    assert len(rows) == 500
    assert sync["page_limit_reached"] is True
    assert sync["returned_scores"] == 500
    assert sync["oldest_returned_at"] == RAW["ended_at"]


@pytest.fixture
def tmp_data(tmp_path, monkeypatch):
    monkeypatch.setattr(P, "DATA", tmp_path)
    monkeypatch.setattr(P, "COACH", tmp_path / "coach")
    monkeypatch.setattr(P, "SESSIONS", tmp_path / "sessions")
    monkeypatch.setattr(P, "CURRENT", tmp_path / "current_session")
    return tmp_path


def test_session_lifecycle(tmp_data):
    assert SS.current() is None
    s = SS.start({"sleep_h": 7, "hand_pain_0_10": 1})
    assert SS.current() == s["session_id"] and s["before"]["sleep_h"] == 7
    assert s["before"]["energy_1_5"] is None and s["setup"]["client"] is None
    with pytest.raises(OsuError):
        SS.start()
    SS.stamp_end(s)
    SS.apply_updates(s, after={"fatigue_1_5": 2}, followed_plan="partly")
    SS.save(s)
    SS.close(s["session_id"])
    assert SS.current() is None
    assert SS.resolve() == s["session_id"]  # falls back to last
    assert SS.load(s["session_id"])["after"]["fatigue_1_5"] == 2


def test_session_end_retry_preserves_time_and_feedback(tmp_data, monkeypatch):
    clock = datetime(2026, 11, 21, 10, 0, tzinfo=timezone.utc)
    monkeypatch.setattr(SS, "now_local", lambda: clock)
    session = SS.start()
    clock += timedelta(minutes=45)
    from osucoach import cli

    def fail_client():
        raise OsuError("offline")

    monkeypatch.setattr(cli, "Client", fail_client)
    runner = CliRunner()
    result = runner.invoke(app, ["session", "end", "--fatigue", "3", "--notes", "hard jumps"])
    assert result.exit_code != 0
    saved = SS.load(session["session_id"])
    assert saved["ended_at"] == clock.isoformat(timespec="seconds")
    assert saved["after"]["duration_min"] == 45
    assert saved["after"]["notes"] == "hard jumps"
    assert SS.current() == session["session_id"]

    clock += timedelta(hours=1)
    monkeypatch.setattr(cli, "Client", lambda: object())
    monkeypatch.setattr(F, "fetch_recent", lambda client, sid, sess: [])
    monkeypatch.setattr(F, "fetch_beatmaps_for", lambda client, scores: (0, 0))
    result = runner.invoke(app, ["session", "end", "--json"])
    assert result.exit_code == 0, result.output
    assert SS.current() is None
    assert SS.load(session["session_id"]) == saved


def test_cli_session_start_snapshots_latest_player_setup(tmp_data, monkeypatch):
    profile = P.COACH / "player.md"
    profile.parent.mkdir()
    markdown = (
        "- input: tablet\n"
        "- tablet: Example tablet\n"
        "- keypad: Example keypad\n"
        "- fps: ~480\n"
        "- client: lazer (always)\n"
        "- tablet area: 60 × 40 mm, center (75.5, 45) mm, rotation 0°\n"
        "- driver: OpenTabletDriver, Absolute Mode\n"
    )
    profile.write_text(markdown, encoding="utf-8")
    started_at = datetime(2026, 11, 21, tzinfo=timezone.utc)
    monkeypatch.setattr(SS, "now_local", lambda: started_at)
    runner = CliRunner()

    first = runner.invoke(app, ["session", "start", "--json"])
    assert first.exit_code == 0, first.output
    first_session = json.loads(first.output)
    assert first_session["setup"] == {
        "input": "tablet", "tablet": "Example tablet",
        "area_mm": [60, 40], "area_center_mm": [75.5, 45],
        "area_offset_mm": None, "area_rotation_deg": 0,
        "driver": "OpenTabletDriver, Absolute Mode", "keypad": "Example keypad",
        "fps": 480, "client": "lazer", "changed_since_last": False,
    }
    SS.close(first_session["session_id"])

    profile.write_text(markdown.replace("60 × 40", "65 x 45"), encoding="utf-8")
    started_at += timedelta(minutes=1)
    second = runner.invoke(app, ["session", "start", "--json"])
    assert second.exit_code == 0, second.output
    second_session = json.loads(second.output)
    assert second_session["setup"]["area_mm"] == [65, 45]
    assert second_session["setup"]["changed_since_last"] is True
    assert SS.load(first_session["session_id"])["setup"] == first_session["setup"]
    SS.close(second_session["session_id"])

    started_at += timedelta(minutes=1)
    third = SS.start()
    assert third["setup"]["changed_since_last"] is False


def test_legacy_player_setup_and_missing_profile_fallback(tmp_data, monkeypatch):
    profile = P.COACH / "player.md"
    profile.parent.mkdir()
    profile.write_text(
        "- setup: Example tablet (previously mouse), Example keypad, ~480 fps\n"
        "- client: lazer (always)\n"
        "- tablet area: 60 x 40 mm, driver: OpenTabletDriver\n",
        encoding="utf-8",
    )
    started_at = datetime(2026, 11, 21, tzinfo=timezone.utc)
    monkeypatch.setattr(SS, "now_local", lambda: started_at)
    first = SS.start()
    assert first["setup"]["input"] == "tablet"
    assert first["setup"]["tablet"] == "Example tablet"
    assert first["setup"]["driver"] == "OpenTabletDriver"
    assert first["setup"]["area_mm"] == [60, 40]
    assert first["setup"]["fps"] == 480
    first["setup"].pop("area_center_mm")
    first["setup"].pop("area_rotation_deg")
    SS.save(first)
    SS.close(first["session_id"])

    profile.unlink()
    started_at += timedelta(minutes=1)
    assert SS.start(setup_changed=True)["setup"] == {
        **first["setup"], "area_center_mm": None, "area_rotation_deg": None,
        "changed_since_last": True,
    }
    assert SS.load(first["session_id"]) == first


def test_apply_updates_validation():
    s = {"before": {}, "after": {}, "setup": {}}
    with pytest.raises(OsuError):
        SS.apply_updates(s, after={"bogus": 1})
    with pytest.raises(OsuError):
        SS.apply_updates(s, followed_plan="maybe")


def test_context_distinguishes_missing_and_empty_scores(tmp_data, monkeypatch):
    from osucoach.store import write_json

    monkeypatch.setattr(P, "PROFILE_DIR", tmp_data / "profile")
    monkeypatch.setattr(P, "BEST", tmp_data / "best.json")
    runner = CliRunner()
    empty = runner.invoke(app, ["context"])
    assert empty.exit_code == 0, empty.output
    assert json.loads(empty.output)["session"] is None
    sess = SS.start()
    sid = sess["session_id"]
    missing = json.loads(runner.invoke(app, ["context"]).output)
    assert missing["stats"] is None
    assert missing["scores_file_modified_at"] is None
    write_json(P.session_dir(sid) / "scores.json", [])
    recorded = json.loads(runner.invoke(app, ["context", sid]).output)
    assert recorded["stats"] == {"plays": 0}
    assert recorded["scores_file_modified_at"] is not None
    assert recorded["current_session_id"] == sid


def test_cli_uses_explicit_workspace(tmp_path):
    workspace = tmp_path / "player workspace"
    (workspace / "coach").mkdir(parents=True)
    (workspace / "coach" / "player.md").write_text("- goal: test workspace\n", encoding="utf-8")
    env = {**os.environ, "OSUCOACH_HOME": str(workspace)}
    result = subprocess.run(
        [sys.executable, "-c", "from osucoach.cli import app; app()", "context"],
        cwd=tmp_path, env=env, capture_output=True, text=True,
    )
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["player"] == "- goal: test workspace\n"


def test_context_previous_reviews_follow_selected_session(tmp_data, monkeypatch):
    from osucoach import context

    monkeypatch.setattr(P, "PROFILE_DIR", tmp_data / "profile")
    monkeypatch.setattr(P, "BEST", tmp_data / "best.json")
    clock = datetime(2026, 11, 21, 10, 0, tzinfo=timezone.utc)
    monkeypatch.setattr(SS, "now_local", lambda: clock)
    ids = []
    for _ in range(4):
        sess = SS.start()
        ids.append(sess["session_id"])
        SS.close(sess["session_id"])
        clock += timedelta(minutes=1)
    reviews = P.COACH / "reviews"
    reviews.mkdir(parents=True)
    (reviews / f"{ids[0]}.md").write_text("previous adjustment", encoding="utf-8")
    previous = context.build(ids[2])["previous_reviews"]
    assert previous == [
        {"session_id": ids[0], "review": "previous adjustment"},
        {"session_id": ids[1], "review": None},
    ]


# ---- stats -----------------------------------------------------------------------
def test_stats_basic():
    rows = [_sc(1, 1, "2026-01-01T00:00:00Z", True, 0.96), _sc(2, 1, "2026-01-01T00:10:00Z", False),
            _sc(3, 2, "2026-01-01T00:20:00Z", True, 0.98), _sc(4, 2, "2026-01-01T00:30:00Z", True, 0.94)]
    S.add_retry_index(rows)
    s = ST.compute(rows, lambda r: 5.0 if r["beatmap_id"] == 1 else 6.0)
    assert (s["plays"], s["passes"], s["pass_rate"]) == (4, 3, 75.0)
    assert s["acc_passed"] == 96.0 and s["span_min"] == 30.0
    assert s["avg_sr_passed"] == pytest.approx(5.67, abs=0.01) and s["avg_sr_failed"] == 5.0
    assert s["first_half"]["plays"] == 2 and s["second_half"]["plays"] == 2
    assert s["first_20min"] is None  # session too short for 20 min windows
    assert s["maps"][0]["plays"] == 2 and s["maps"][0]["max_retry"] in (2,)


def test_stats_empty_and_render():
    assert ST.compute([]) == {"plays": 0}
    assert "no plays" in ST.render("x", {"plays": 0})


def test_session_blocks_assign_end_times(tmp_data, monkeypatch):
    clock = datetime(2026, 1, 1, tzinfo=timezone.utc)
    monkeypatch.setattr(SS, "now_local", lambda: clock)
    sess = SS.start()
    clock += timedelta(minutes=1)
    runner = CliRunner()
    assert runner.invoke(app, ["session", "block", "warmup"]).exit_code == 0
    clock += timedelta(minutes=10)
    assert runner.invoke(app, ["session", "block", "focus"]).exit_code == 0
    blocks = SS.load(sess["session_id"])["blocks"]
    rows = S.add_retry_index([
        _sc(1, 1, "2026-01-01T00:00:30Z"),
        _sc(2, 2, "2026-01-01T00:02:00Z", acc=0.97),
        _sc(3, 3, "2026-01-01T00:11:00Z", passed=False),
    ])
    result = ST.compute(rows, blocks=blocks)
    assert result["plays_before_first_block"] == 1
    assert [(b["label"], b["plays"], b["pass_rate"]) for b in result["blocks"]] == [
        ("warmup", 1, 100.0), ("focus", 1, 0.0)
    ]
    assert runner.invoke(app, ["session", "block", "same second"]).exit_code != 0
