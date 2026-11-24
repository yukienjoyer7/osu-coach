"""Offline tests: no network, no real data/ writes."""
import json
import os
import subprocess
import sys
from datetime import datetime, timedelta, timezone

import pytest
import httpx
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


def test_token_network_failure_is_secret_free(monkeypatch):
    from osucoach.api import Client

    monkeypatch.setenv("OSU_CLIENT_ID", "1")
    monkeypatch.setenv("OSU_CLIENT_SECRET", "test-secret")
    client = Client()

    def fail(*args, **kwargs):
        raise httpx.ConnectError("test-secret")

    monkeypatch.setattr(client._http, "post", fail)
    with pytest.raises(OsuError, match="token network error") as error:
        client.get("/test")
    assert "test-secret" not in str(error.value)


def test_guide_exposes_coaching_rules():
    result = CliRunner().invoke(app, ["guide"])
    assert result.exit_code == 0, result.output
    assert "Never read or print `.env`" in result.output
    assert "## Evidence discipline" in result.output


def test_schema_exposes_data_limitations():
    result = CliRunner().invoke(app, ["schema"])
    assert result.exit_code == 0, result.output
    assert "scores_sync.json" in result.output
    assert "cannot recover unrecorded earlier gaps" in result.output


def test_map_search_encodes_query():
    from urllib.parse import parse_qs, urlparse

    result = CliRunner().invoke(app, ["map-search", "stars>=5 stars<6 tag=\"finger control\""])
    assert result.exit_code == 0, result.output
    data = json.loads(result.output)
    parsed = urlparse(data["url"])
    params = parse_qs(parsed.query)
    assert parsed.netloc == "osu.ppy.sh" and parsed.path == "/beatmapsets"
    assert params == {"m": ["0"], "s": ["ranked"], "q": [data["query"]]}


def test_unranked_player_and_empty_best_are_supported(monkeypatch):
    from osucoach import cli

    profile = {"pp": 0, "global_rank": None, "accuracy": 0, "play_count": 0}
    monkeypatch.setattr(cli, "_client", lambda: object())
    monkeypatch.setattr(F, "fetch_profile", lambda client: profile)
    monkeypatch.setattr(F, "fetch_best", lambda client: [])
    runner = CliRunner()
    result = runner.invoke(app, ["fetch", "profile", "--json"])
    assert result.exit_code == 0, result.output
    assert json.loads(result.output)["global_rank"] is None
    result = runner.invoke(app, ["fetch", "best", "--json"])
    assert result.exit_code == 0, result.output
    assert json.loads(result.output) == {"scores": 0}


def test_best_fetch_time_is_separate_from_file_modification(tmp_path, monkeypatch):
    from osucoach import context
    from osucoach.store import write_json

    monkeypatch.setattr(P, "DATA", tmp_path)
    monkeypatch.setattr(P, "BEST", tmp_path / "best.json")
    monkeypatch.setattr(P, "PROFILE_DIR", tmp_path / "profile")
    monkeypatch.setattr(P, "COACH", tmp_path / "coach")
    monkeypatch.setattr(P, "SESSIONS", tmp_path / "sessions")
    monkeypatch.setattr(P, "CURRENT", tmp_path / "current_session")
    monkeypatch.setattr(F, "now_local", lambda: datetime(2026, 1, 1, tzinfo=timezone.utc))

    class Client:
        user_id = "test"

        def get(self, path, **params):
            return []

    F.fetch_best(Client())
    write_json(P.BEST, [])  # A local enrichment write must not change fetch metadata.
    assert context.build()["best_sync"] == {
        "fetched_at": "2026-01-01T00:00:00+00:00", "scores_returned": 0
    }


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


def test_gap_reports_unreachable_scenario_and_invalid_input(tmp_path, monkeypatch):
    from osucoach.store import write_json

    monkeypatch.setattr(P, "PROFILE_DIR", tmp_path / "profile")
    monkeypatch.setattr(P, "BEST", tmp_path / "best.json")
    write_json(F.profile_path(), {"pp": 8000, "fetched_at": "2026-01-01T00:00:00Z",
                                  "rank_threshold_10k_pp": 9000})
    write_json(P.BEST, [{"pp": 300}] * 100)
    runner = CliRunner()
    result = runner.invoke(app, ["gap", "--pp", "100"])
    assert result.exit_code == 0
    assert "not reached within 300 plays" in result.output
    result = runner.invoke(app, ["gap", "--pp", "100", "--json"])
    scenario = json.loads(result.output)
    assert scenario["best_fetched_at"] is None and scenario["threshold_fetched_at"] is None
    assert scenario["profile_fetched_at"] == "2026-01-01T00:00:00Z"
    assert "top 100 only" in scenario["model"] and isinstance(scenario["fixed_residual_pp"], float)
    for value in ("nan", "-1", "bad"):
        result = runner.invoke(app, ["gap", "--pp", value])
        assert result.exit_code != 0
        assert "positive finite numbers" in result.output
    write_json(P.BEST, [])
    assert runner.invoke(app, ["gap", "--pp", "100"]).exit_code == 0


# ---- mods ------------------------------------------------------------------
def test_parse_mods():
    assert [m["acronym"] for m in M.parse_mods("HD,DT")] == ["HD", "DT"]
    assert [m["acronym"] for m in M.parse_mods("hddt")] == ["HD", "DT"]
    assert M.parse_mods("NM") == [] and M.parse_mods(None) == []


def test_key_distinguishes_mods_and_normalizes_aliases():
    assert M.key(M.parse_mods("HD")) != M.key([])
    assert M.key(M.parse_mods("HDNC")) == M.key(M.parse_mods("HDDT"))
    assert M.key(M.parse_mods("HR,DT")) == M.key(M.parse_mods("DTHR"))


def test_custom_rate_gets_own_key():
    m = M.from_score(["DT"], {"DT": {"speed_change": 1.3}})
    assert M.key(m) != M.key(M.parse_mods("DT"))
    assert M.key(M.from_score(["DT"], {"DT": {"speed_change": 1.5}})) == M.key(M.parse_mods("DT"))


def test_numeric_mod_keys_and_legacy_cache(tmp_path, monkeypatch):
    from osucoach.store import write_json

    monkeypatch.setattr(P, "BEATMAPS", tmp_path)
    int_mods = M.from_score(["DA"], {"DA": {"approach_rate": 9}})
    float_mods = M.from_score(["DA"], {"DA": {"approach_rate": 9.0}})
    assert M.key(int_mods) == M.key(float_mods)
    assert M.key(M.from_score(["DT"], {"DT": {"speed_change": 1.3000001}})) != M.key(
        M.from_score(["DT"], {"DT": {"speed_change": 1.3}}))
    old = tmp_path / "11_DA-ap9.0.json"
    write_json(old, {"star_rating": 5})
    assert F.beatmap_path(11, float_mods) != old


def test_refresh_beatmap_replaces_cached_attributes(tmp_path, monkeypatch):
    from osucoach.store import write_json

    monkeypatch.setattr(P, "BEATMAPS", tmp_path / "beatmaps")
    monkeypatch.setattr(P, "META_DIR", tmp_path / "meta")
    write_json(P.META_DIR / "11.json", {"cs": 4, "ar": 9, "accuracy": 8, "bpm": 180,
               "total_length": 100, "count_circles": 1, "count_sliders": 2, "count_spinners": 0})
    write_json(F.beatmap_path(11, []), {"beatmap_id": 11, "star_rating": 4.0})

    class Client:
        def get(self, path):
            return {"id": 11, "cs": 4, "ar": 9.5, "accuracy": 8, "bpm": 200,
                    "total_length": 120, "count_circles": 1, "count_sliders": 2, "count_spinners": 0}

        def post(self, path, body):
            return {"attributes": {"star_rating": 5.0, "max_combo": 10}}

    cached, called = F.fetch_beatmap(Client(), 11, [])
    assert not called and cached["star_rating"] == 4.0
    refreshed, called = F.fetch_beatmap(Client(), 11, [], refresh=True)
    assert called and refreshed["star_rating"] == 5.0 and refreshed["fetched_at"]
    assert refreshed["ar"] == 9.5 and refreshed["bpm"] == 200 and refreshed["length_s"] == 120
    assert F.fetch_beatmap(Client(), 11, [])[0] == refreshed


def test_cli_refreshes_custom_rate(monkeypatch):
    from osucoach import cli

    calls = []
    monkeypatch.setattr(cli, "_client", lambda: object())

    def fetch(client, beatmap_id, mods, refresh):
        calls.append((beatmap_id, mods, refresh))
        return {"mods": ["DT"], "star_rating": 5, "ar": 9, "od": 8, "cs": 4, "bpm": 200}, True

    monkeypatch.setattr(F, "fetch_beatmap", fetch)
    runner = CliRunner()
    result = runner.invoke(app, ["fetch", "beatmap", "11", "--mods", "DT", "--rate", "1.3", "--refresh"])
    assert result.exit_code == 0, result.output
    assert calls[0][1] == M.from_score(["DT"], {"DT": {"speed_change": 1.3}}) and calls[0][2] is True
    assert runner.invoke(app, ["fetch", "beatmap", "11", "--rate", "1.3"]).exit_code != 0
    assert runner.invoke(app, ["fetch", "beatmap", "11", "--mods", "DT", "--rate", "nan"]).exit_code != 0


def test_cli_refreshes_difficulty_adjust(monkeypatch):
    from osucoach import cli

    calls = []
    monkeypatch.setattr(cli, "_client", lambda: object())

    def fetch(client, beatmap_id, mods, refresh):
        calls.append((mods, refresh))
        return {"mods": ["DA"], "star_rating": 5, "ar": 9, "od": 8, "cs": 4, "bpm": 200}, True

    monkeypatch.setattr(F, "fetch_beatmap", fetch)
    runner = CliRunner()
    result = runner.invoke(app, ["fetch", "beatmap", "11", "--mods", "DA", "--ar", "9", "--od", "8", "--refresh"])
    assert result.exit_code == 0, result.output
    assert calls[0][0][0]["settings"] == {"approach_rate": 9.0, "overall_difficulty": 8.0}
    assert calls[0][1] is True
    assert runner.invoke(app, ["fetch", "beatmap", "11", "--ar", "9"]).exit_code != 0
    assert runner.invoke(app, ["fetch", "beatmap", "11", "--mods", "DA", "--ar", "nan"]).exit_code != 0


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
    assert S.map_score({**RAW, "started_at": "2026-11-20T09:58:00Z"})["started_at"] == "2026-11-20T09:58:00Z"
    assert S.map_score({**RAW, "ended_at": None, "started_at": "2026-11-20T09:58:00Z"})["started_at"] is None


def test_map_score_fail_without_pp_or_weight():
    raw = {**RAW, "passed": False, "pp": None, "weight": None, "mods": []}
    sc = S.map_score(raw)
    assert sc["passed"] is False and sc["pp"] is None and sc["weight_pct"] is None
    assert "mod_settings" not in sc


def test_map_score_distinguishes_missing_statistics_from_sparse_counts():
    missing = S.map_score({**RAW, "statistics": None})
    assert all(missing[key] is None for key in ("n300", "n100", "n50", "miss"))
    sparse = S.map_score({**RAW, "statistics": {"great": 100}})
    assert (sparse["n300"], sparse["n100"], sparse["n50"], sparse["miss"]) == (100, 0, 0, 0)


def _sc(i, bid, t, passed=True, acc=0.95):
    return {"score_id": i, "beatmap_id": bid, "created_at": t, "passed": passed,
            "accuracy": acc, "pp": None, "title": "t", "version": "v", "mods": []}


def test_retry_index_counts_per_map_in_time_order():
    rows = [_sc(3, 1, "2026-01-01T00:03:00Z"), _sc(1, 1, "2026-01-01T00:01:00Z"),
            _sc(2, 2, "2026-01-01T00:02:00Z"), _sc(4, 1, "2026-01-01T00:04:00Z")]
    out = S.add_retry_index(rows)
    assert [(r["score_id"], r["retry_index"]) for r in out] == [(1, 1), (2, 1), (3, 2), (4, 3)]


def test_mixed_offsets_are_ordered_by_instant():
    rows = [_sc(2, 1, "2026-01-01T01:10:00+01:00", acc=0.90),
            _sc(1, 1, "2026-01-01T00:00:00Z", acc=0.98)]
    indexed = S.add_retry_index(rows)
    assert [(r["score_id"], r["retry_index"]) for r in indexed] == [(1, 1), (2, 2)]
    summary = ST.compute(list(reversed(indexed)))
    assert summary["span_min"] == 10
    assert summary["first_half"]["acc_passed"] == 98.0
    assert summary["second_half"]["acc_passed"] == 90.0


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
            assert offset == 0
            return [{**RAW, "id": offset + i} for i in range(100)]

    rows = F.fetch_recent(Client(), sess["session_id"], sess)
    sync = context.build(sess["session_id"])["scores_sync"]
    assert len(rows) == 100
    assert sync["page_limit_reached"] is True
    assert sync["limit_reached_ever"] is True
    assert sync["returned_scores"] == 100
    assert sync["oldest_returned_at"] == RAW["ended_at"]
    client = Client()
    monkeypatch.setattr(client, "get", lambda *args, **kwargs: [])
    F.fetch_recent(client, sess["session_id"], sess)
    later = F.recent_sync(sess["session_id"])
    assert later["page_limit_reached"] is False and later["limit_reached_ever"] is True


def test_old_full_sync_metadata_is_flagged(tmp_data):
    from osucoach.store import write_json

    sess = SS.start()
    sid = sess["session_id"]
    write_json(P.session_dir(sid) / "scores_sync.json",
               {"returned_scores": 100, "page_limit_reached": False})
    assert F.recent_sync(sid)["limit_reached_ever"] is True


def test_recent_fetch_exposes_limit_warning(tmp_data, monkeypatch):
    from osucoach import cli
    from osucoach.store import write_json

    sess = SS.start()
    sid = sess["session_id"]
    monkeypatch.setattr(cli, "_client", lambda: object())

    def fetch(client, session_id, session):
        write_json(P.session_dir(session_id) / "scores_sync.json",
                   {"returned_scores": 100, "page_limit_reached": True})
        return []

    monkeypatch.setattr(F, "fetch_recent", fetch)
    runner = CliRunner()
    result = runner.invoke(app, ["fetch", "recent", "--session", sid])
    assert result.exit_code == 0 and "coverage may be incomplete" in result.output
    result = runner.invoke(app, ["fetch", "recent", "--session", sid, "--json"])
    assert json.loads(result.output)["scores_sync"]["limit_reached_ever"] is True


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


def test_session_can_close_offline_after_failed_sync(tmp_data, monkeypatch):
    from osucoach import cli

    clock = datetime(2026, 11, 21, 10, 0, tzinfo=timezone.utc)
    monkeypatch.setattr(SS, "now_local", lambda: clock)
    sess = SS.start()
    clock += timedelta(minutes=30)

    def fail_client():
        raise OsuError("offline")

    monkeypatch.setattr(cli, "Client", fail_client)
    runner = CliRunner()
    assert runner.invoke(app, ["session", "end", "--fatigue", "3"]).exit_code != 0
    saved = SS.load(sess["session_id"])
    clock += timedelta(minutes=10)
    closed = runner.invoke(app, ["session", "end", "--offline", "--json"])
    assert closed.exit_code == 0, closed.output
    assert json.loads(closed.output)["synced"] is False
    assert SS.current() is None
    assert SS.load(sess["session_id"]) == saved
    assert not (P.session_dir(sess["session_id"]) / "scores.json").exists()
    assert SS.start()["session_id"] != sess["session_id"]


def test_complete_cli_session_handoff_with_simulated_api(tmp_data, monkeypatch):
    from osucoach import cli

    monkeypatch.setattr(P, "PROFILE_DIR", tmp_data / "profile")
    monkeypatch.setattr(P, "BEST", tmp_data / "best.json")
    monkeypatch.setattr(P, "BEATMAPS", tmp_data / "beatmaps")
    monkeypatch.setattr(P, "META_DIR", tmp_data / "beatmaps" / "_meta")
    clock = datetime(2026, 11, 20, 9, 0, tzinfo=timezone.utc)
    monkeypatch.setattr(SS, "now_local", lambda: clock)
    monkeypatch.setattr(F, "now_local", lambda: clock)
    raw = {**RAW, "started_at": "2026-11-20T09:03:00Z", "ended_at": "2026-11-20T09:05:00Z"}

    class Client:
        user_id = "test"

        def get(self, path, **params):
            return [raw]

        def post(self, path, body):
            return {"attributes": {"star_rating": 6.0, "max_combo": 600}}

    monkeypatch.setattr(cli, "_client", lambda: Client())
    runner = CliRunner()
    started = runner.invoke(app, ["session", "start", "--energy", "4", "--json"])
    assert started.exit_code == 0, started.output
    sid = json.loads(started.output)["session_id"]
    clock += timedelta(minutes=1)
    assert runner.invoke(app, ["session", "block", "focus"]).exit_code == 0
    clock += timedelta(minutes=9)
    ended = runner.invoke(app, ["session", "end", "--fatigue", "2", "--json"])
    assert ended.exit_code == 0, ended.output
    assert json.loads(ended.output)["scores"] == 1
    handoff = runner.invoke(app, ["context", sid])
    assert handoff.exit_code == 0, handoff.output
    context = json.loads(handoff.output)
    assert context["current_session_id"] is None
    assert context["session"]["after"]["fatigue_1_5"] == 2
    assert context["stats"]["blocks"][0]["plays"] == 1
    assert context["stats"]["avg_sr_passed"] == 6.0
    assert context["scores_sync"]["returned_scores"] == 1


def test_stats_distinguishes_missing_score_file_from_empty(tmp_data):
    from osucoach.store import write_json

    sess = SS.start()
    sid = sess["session_id"]
    runner = CliRunner()
    missing = runner.invoke(app, ["stats", sid, "--json"])
    assert missing.exit_code != 0
    assert "scores missing" in missing.output
    write_json(P.session_dir(sid) / "scores.json", [])
    empty = runner.invoke(app, ["stats", sid, "--json"])
    assert empty.exit_code == 0
    assert json.loads(empty.output)["plays"] == 0


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
        "fps": 480, "client": "lazer", "changed_since_last": None,
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


def test_setup_change_is_unknown_when_only_one_side_is_known(tmp_data, monkeypatch):
    clock = datetime(2026, 11, 21, tzinfo=timezone.utc)
    monkeypatch.setattr(SS, "now_local", lambda: clock)
    profile = P.COACH / "player.md"
    profile.parent.mkdir()
    profile.write_text("- input: tablet\n", encoding="utf-8")
    first = SS.start()
    assert first["setup"]["changed_since_last"] is None
    SS.close(first["session_id"])
    clock += timedelta(minutes=1)
    profile.write_text("- input: tablet\n- fps: 480\n", encoding="utf-8")
    second = SS.start()
    assert second["setup"]["changed_since_last"] is None


def test_apply_updates_validation():
    s = {"before": {}, "after": {}, "setup": {}}
    with pytest.raises(OsuError):
        SS.apply_updates(s, after={"bogus": 1})
    with pytest.raises(OsuError):
        SS.apply_updates(s, followed_plan="maybe")


def test_invalid_feedback_is_rejected_without_partial_updates(tmp_data):
    runner = CliRunner()
    rejected = runner.invoke(app, ["session", "start", "--pain", "11"])
    assert rejected.exit_code != 0
    assert SS.current() is None
    sess = SS.start()
    sid = sess["session_id"]
    rejected = runner.invoke(app, ["session", "update", "--energy", "4", "--pain-before", "-1"])
    assert rejected.exit_code != 0
    assert SS.load(sid)["before"]["energy_1_5"] is None
    rejected = runner.invoke(app, ["session", "update", "--sleep-h", "nan"])
    assert rejected.exit_code != 0
    accepted = runner.invoke(app, ["session", "update", "--energy", "4", "--pain-before", "0"])
    assert accepted.exit_code == 0, accepted.output
    assert SS.load(sid)["before"]["energy_1_5"] == 4


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


def test_context_keeps_recent_journal_headings(tmp_data, monkeypatch):
    from osucoach import context

    monkeypatch.setattr(P, "PROFILE_DIR", tmp_data / "profile")
    monkeypatch.setattr(P, "BEST", tmp_data / "best.json")
    P.COACH.mkdir()
    (P.COACH / "journal.md").write_text(
        "# Journal\n\n## first\n- old\n\n## second\n- two\n\n"
        "## third\n- three\n\n## fourth\n- four\n", encoding="utf-8")
    assert context.build()["recent_journal"] == (
        "## second\n- two\n\n## third\n- three\n\n## fourth\n- four\n"
    )


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
    assert [(entry["session_id"], entry["review"]) for entry in previous] == [
        (ids[0], "previous adjustment"), (ids[1], None),
    ]
    assert previous[0]["session"]["session_id"] == ids[0]
    assert previous[0]["scores_sync"] is None


def test_context_journal_does_not_include_future_or_undated_entries(tmp_data, monkeypatch):
    from osucoach import context

    monkeypatch.setattr(P, "PROFILE_DIR", tmp_data / "profile")
    monkeypatch.setattr(P, "BEST", tmp_data / "best.json")
    clock = datetime(2026, 11, 21, 10, 0, tzinfo=timezone.utc)
    monkeypatch.setattr(SS, "now_local", lambda: clock)
    ids = []
    for _ in range(3):
        sess = SS.start()
        ids.append(sess["session_id"])
        SS.close(sess["session_id"])
        clock += timedelta(minutes=1)
    P.COACH.mkdir(parents=True, exist_ok=True)
    (P.COACH / "journal.md").write_text(
        f"# Journal\n## {ids[0]} | focus: aim\nold\n"
        f"## {ids[1]} | focus: aim\nselected\n"
        f"## notes\nunknown date\n## {ids[2]} | focus: aim\nfuture\n", encoding="utf-8")
    result = context.build(ids[1])
    assert "old" in result["recent_journal"] and "selected" in result["recent_journal"]
    assert "future" not in result["recent_journal"] and "unknown date" not in result["recent_journal"]
    assert result["journal_undated_sections_omitted"] == 1


def test_context_flags_session_grace_overlap(tmp_data, monkeypatch):
    from osucoach import context

    monkeypatch.setattr(P, "PROFILE_DIR", tmp_data / "profile")
    monkeypatch.setattr(P, "BEST", tmp_data / "best.json")
    clock = datetime(2026, 11, 21, 10, 0, tzinfo=timezone.utc)
    monkeypatch.setattr(SS, "now_local", lambda: clock)
    first = SS.start()
    clock += timedelta(minutes=10)
    SS.stamp_end(first)
    SS.save(first)
    SS.close(first["session_id"])
    clock += timedelta(minutes=2)
    second = SS.start()
    assert context.build(first["session_id"])["overlapping_session_ids"] == [second["session_id"]]
    assert context.build(second["session_id"])["overlapping_session_ids"] == [first["session_id"]]
    assert context.build(first["session_id"])["overlapping_score_ids"] == {second["session_id"]: None}
    from osucoach.store import write_json
    score = _sc(11, 99, "2026-11-21T10:12:30Z")
    score["retry_index"] = 1
    write_json(P.session_dir(first["session_id"]) / "scores.json", [score])
    write_json(P.session_dir(second["session_id"]) / "scores.json", [])
    assert context.build(first["session_id"])["overlapping_score_ids"] == {second["session_id"]: []}
    write_json(P.session_dir(second["session_id"]) / "scores.json", [score])
    assert context.build(second["session_id"])["overlapping_score_ids"] == {first["session_id"]: [11]}


def test_map_history_keeps_conditions_and_missing_sessions(tmp_data, monkeypatch):
    from osucoach.store import write_json

    clock = datetime(2026, 11, 21, 10, 0, tzinfo=timezone.utc)
    monkeypatch.setattr(SS, "now_local", lambda: clock)
    first = SS.start()
    sid = first["session_id"]
    score = _sc(1, 99, "2026-11-21T10:01:00Z")
    score["mods"] = ["HD"]
    write_json(P.session_dir(sid) / "scores.json", [score])
    SS.close(sid)
    clock += timedelta(minutes=2)
    second = SS.start()
    runner = CliRunner()
    result = runner.invoke(app, ["history", "99"])
    assert result.exit_code == 0, result.output
    history = json.loads(result.output)
    assert history["missing_score_sessions"] == [second["session_id"]]
    assert history["sessions"][0]["scores"][0]["mods"] == ["HD"]
    assert history["sessions"][0]["setup"] == first["setup"]
    assert history["sessions"][0]["scores_sync"] is None


def test_maps_filter_recorded_cached_attributes(tmp_data, monkeypatch):
    from osucoach.store import write_json

    monkeypatch.setattr(P, "BEST", tmp_data / "best.json")
    monkeypatch.setattr(P, "BEATMAPS", tmp_data / "beatmaps")
    row = _sc(1, 99, "2026-11-21T10:01:00Z")
    row["mods"] = ["DT"]
    row["mod_settings"] = {"DT": {"speed_change": 1.3}}
    write_json(P.BEST, [row, row, _sc(2, 100, "2026-11-21T10:02:00Z")])
    write_json(F.beatmap_path(99, S.score_mods(row)),
               {"beatmap_id": 99, "star_rating": 6.5, "bpm": 220, "ar": 9.5, "length_s": 120})
    runner = CliRunner()
    result = runner.invoke(app, ["maps", "--min-sr", "6", "--max-bpm", "230"])
    assert result.exit_code == 0, result.output
    maps = json.loads(result.output)["maps"]
    assert len(maps) == 1 and maps[0]["beatmap_id"] == 99
    assert maps[0]["mod_settings"]["DT"]["speed_change"] == 1.3
    assert maps[0]["ranked_status"] is None and maps[0]["pattern_type"] is None
    assert runner.invoke(app, ["maps", "--min-sr", "7", "--max-sr", "6"]).exit_code != 0
    assert len(json.loads(runner.invoke(app, ["maps", "--min-ar", "9", "--max-length", "120"]).output)["maps"]) == 1
    assert json.loads(runner.invoke(app, ["maps", "--min-length", "121"]).output)["maps"] == []
    assert runner.invoke(app, ["maps", "--min-ar", "nan"]).exit_code != 0
    tagged = runner.invoke(app, ["map-tag", "99", "--tags", "Jumps, bursts, Jumps"])
    assert tagged.exit_code == 0, tagged.output
    found = json.loads(runner.invoke(app, ["maps", "--tag", "JUMPS"]).output)["maps"]
    assert found[0]["user_tags"] == ["bursts", "jumps"]
    assert json.loads(runner.invoke(app, ["maps", "--tag", "streams"]).output)["maps"] == []
    assert runner.invoke(app, ["map-tag", "99", "--tags", ""]).exit_code == 0
    assert json.loads(runner.invoke(app, ["maps", "--tag", "jumps"]).output)["maps"] == []


# ---- stats -----------------------------------------------------------------------
def test_stats_basic():
    rows = [_sc(1, 1, "2026-01-01T00:00:00Z", True, 0.96), _sc(2, 1, "2026-01-01T00:10:00Z", False),
            _sc(3, 2, "2026-01-01T00:20:00Z", True, 0.98), _sc(4, 2, "2026-01-01T00:30:00Z", True, 0.94)]
    S.add_retry_index(rows)
    s = ST.compute(rows, lambda r: 5.0 if r["beatmap_id"] == 1 else 6.0)
    assert (s["plays"], s["passes"], s["pass_rate"]) == (4, 3, 75.0)
    assert s["acc_passed"] == 96.0 and s["span_min"] == 30.0
    assert s["avg_sr_passed"] == pytest.approx(5.67, abs=0.01) and s["avg_sr_failed"] == 5.0
    assert s["avg_sr_passed_samples"] == 3 and s["avg_sr_failed_samples"] == 1
    rendered = ST.render("session", s)
    assert "acc (passes) 96.0% (n=3)" in rendered
    assert "avg SR passed 5.67 (n=3) vs failed 5.0 (n=1)" in rendered
    assert s["first_half"]["plays"] == 2 and s["second_half"]["plays"] == 2
    assert s["first_20min"] is None  # session too short for 20 min windows
    assert s["maps"][0]["plays"] == 2 and s["maps"][0]["max_retry"] in (2,)
    assert s["first_recorded_attempts"] == {"plays": 2, "pass_rate": 100.0, "acc_passed": 97.0, "acc_passed_samples": 2}
    assert s["later_recorded_attempts"] == {"plays": 2, "pass_rate": 50.0, "acc_passed": 94.0, "acc_passed_samples": 1}


def test_accuracy_sample_count_excludes_failed_and_unknown_scores():
    rows = S.add_retry_index([_sc(1, 1, "2026-01-01T00:00:00Z", acc=0.98),
                              _sc(2, 1, "2026-01-01T00:01:00Z", acc=None),
                              _sc(3, 1, "2026-01-01T00:02:00Z", passed=False, acc=0.5)])
    summary = ST.compute(rows)
    assert summary["acc_passed"] == 98.0 and summary["acc_passed_samples"] == 1
    assert summary["maps"][0]["acc_passed_samples"] == 1
    partial = ST.compute(rows, lambda row: 5.0 if row["score_id"] == 1 else None)
    assert partial["avg_sr_passed_samples"] == 1
    assert partial["avg_sr_failed_samples"] == 0 and partial["avg_sr_failed"] is None


def test_stats_empty_and_render():
    assert ST.compute([]) == {"plays": 0}
    assert "no plays" in ST.render("x", {"plays": 0})


def test_twenty_minute_windows_do_not_overlap_or_use_rounded_span():
    rows = S.add_retry_index([
        _sc(1, 1, "2026-01-01T00:00:00Z"),
        _sc(2, 2, "2026-01-01T00:20:00Z"),
        _sc(3, 3, "2026-01-01T00:40:00Z"),
    ])
    summary = ST.compute(rows)
    assert summary["first_20min"]["plays"] == 1
    assert summary["last_20min"]["plays"] == 2
    rows[-1]["created_at"] = "2026-01-01T00:39:59Z"
    summary = ST.compute(rows)
    assert summary["span_min"] == 40.0
    assert summary["first_20min"] is None and summary["last_20min"] is None


def test_map_stats_separate_custom_mod_settings():
    rows = [_sc(1, 11, "2026-01-01T00:00:00Z", acc=0.98),
            _sc(2, 11, "2026-01-01T00:10:00Z", acc=0.90)]
    for row, speed in zip(rows, (1.3, 1.5)):
        row["mods"] = ["DT"]
        row["mod_settings"] = {"DT": {"speed_change": speed}}
    S.add_retry_index(rows)
    summary = ST.compute(rows, lambda row: row["mod_settings"]["DT"]["speed_change"])
    assert [(m["mod_settings"]["DT"]["speed_change"], m["acc_passed"], m["sr"])
            for m in summary["maps"]] == [(1.3, 98.0, 1.3), (1.5, 90.0, 1.5)]
    assert '"speed_change": 1.3' in ST.render("test", summary)


def test_hit_result_totals_report_coverage():
    rows = [_sc(1, 11, "2026-01-01T00:00:00Z"),
            _sc(2, 11, "2026-01-01T00:10:00Z", passed=False)]
    rows[0].update(n300=100, n100=5, n50=1, miss=2)
    S.add_retry_index(rows)
    summary = ST.compute(rows)
    expected = {"scores_with_counts": 1, "scores_total": 2,
                "totals": {"n300": 100, "n100": 5, "n50": 1, "miss": 2}}
    assert summary["hit_results"] == expected
    assert summary["maps"][0]["hit_results"] == expected


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
    rows[2]["started_at"] = "2026-01-01T00:10:00Z"
    result = ST.compute(rows, blocks=blocks)
    assert result["plays_before_first_block"] == 1
    assert [(b["label"], b["plays"], b["pass_rate"]) for b in result["blocks"]] == [
        ("warmup", 1, 100.0), ("focus", 1, 0.0)
    ]
    assert result["blocks"][0]["unknown_start"] == 1
    assert result["blocks"][1]["crossing_start"] == 1
    assert runner.invoke(app, ["session", "block", "same second"]).exit_code != 0


def test_beatmap_fetch_keeps_all_mods_and_settings(tmp_path, monkeypatch):
    from osucoach.store import write_json

    monkeypatch.setattr(P, "BEATMAPS", tmp_path / "beatmaps")
    monkeypatch.setattr(P, "META_DIR", tmp_path / "meta")
    write_json(P.META_DIR / "11.json", {"cs": 4, "ar": 9, "accuracy": 8, "bpm": 180,
               "total_length": 100, "count_circles": 1, "count_sliders": 2, "count_spinners": 0})
    mods = [{"acronym": "HD"}, {"acronym": "FL", "settings": {"size_multiplier": 0.8}}]

    class Client:
        def post(self, path, body):
            self.body = body
            return {"attributes": {"star_rating": 6, "max_combo": 12}}

    client = Client()
    data, called = F.fetch_beatmap(client, 11, mods)
    assert called and client.body["mods"] == [
        {"acronym": "FL", "settings": {"size_multiplier": 0.8}}, {"acronym": "HD"}]
    assert data["mod_settings"]["FL"]["size_multiplier"] == 0.8
    assert F.beatmap_path(11, mods) != F.beatmap_path(11, [{"acronym": "HD"}])
    assert "_v3-" in F.beatmap_path(11, mods).name
    score = {"beatmap_id": 11, "mods": ["HD"], "map_max_combo": 999}
    F.fill_map_max_combo([score])
    assert score["map_max_combo"] is None


@pytest.mark.parametrize("mod", ["WU", "WD", "AS"])
def test_variable_speed_attributes_are_unknown(mod):
    adjusted = M.adjust(4, 9, 8, 180, 120, [{"acronym": mod}])
    assert adjusted == {"cs": 4, "ar": None, "od": None, "bpm": None, "length_s": None}
    assert M.key([{"acronym": mod}]).startswith("v4-")


def test_context_prior_sessions_show_plans_and_coverage(tmp_data, monkeypatch):
    from osucoach import context
    from osucoach.store import write_json

    monkeypatch.setattr(P, "PROFILE_DIR", tmp_data / "profile")
    monkeypatch.setattr(P, "BEST", tmp_data / "best.json")
    monkeypatch.setattr(P, "BEATMAPS", tmp_data / "beatmaps")
    clock = datetime(2026, 11, 20, 10, 0, tzinfo=timezone.utc)
    monkeypatch.setattr(SS, "now_local", lambda: clock)
    first = SS.start()
    SS.stamp_end(first)
    SS.save(first)
    SS.close(first["session_id"])
    plan = P.COACH / "plans" / f"{first['session_id']}.md"
    plan.parent.mkdir(parents=True, exist_ok=True)
    plan.write_text("focus: bursts", encoding="utf-8")
    write_json(P.session_dir(first["session_id"]) / "scores.json", [])
    write_json(P.session_dir(first["session_id"]) / "scores_sync.json", {"returned_scores": 100})
    clock += timedelta(hours=1)
    second = SS.start()
    SS.stamp_end(second)
    SS.save(second)
    SS.close(second["session_id"])
    clock += timedelta(hours=1)
    third = SS.start()
    result = context.build(third["session_id"])
    prior, missing = result["previous_reviews"]
    assert prior["plan"] == "focus: bursts" and prior["review"] is None
    assert prior["stats"]["plays"] == 0 and prior["scores_sync"]["limit_reached_ever"]
    assert missing["session_id"] == second["session_id"] and missing["stats"] is None
    assert missing["plan"] is None and missing["scores_sync"] is None


def test_difficulty_adjust_null_settings_keep_map_values(tmp_path, monkeypatch):
    from osucoach.store import write_json

    monkeypatch.setattr(P, "BEATMAPS", tmp_path / "beatmaps")
    monkeypatch.setattr(P, "META_DIR", tmp_path / "meta")
    write_json(P.META_DIR / "11.json", {"cs": 4, "ar": 9, "accuracy": 8, "bpm": 180,
               "total_length": 100, "count_circles": 1, "count_sliders": 2, "count_spinners": 0})
    mods = [{"acronym": "DA", "settings": {"circle_size": None, "approach_rate": 0,
                                             "overall_difficulty": None}}]

    class Client:
        def post(self, path, body):
            assert body["mods"] == mods
            return {"attributes": {"star_rating": 5, "max_combo": 10}}

    data, called = F.fetch_beatmap(Client(), 11, mods)
    assert called and (data["cs"], data["ar"], data["od"]) == (4, 0, 8)


def test_init_uses_current_directory_and_preserves_existing_files(tmp_path):
    env = dict(os.environ)
    env.pop("OSUCOACH_HOME", None)
    cmd = [sys.executable, "-c", "from osucoach.cli import app; app()", "init", "--json"]
    result = subprocess.run(cmd, cwd=tmp_path, env=env, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    data = json.loads(result.stdout)
    assert data["workspace"] == str(tmp_path)
    assert set(data["created"]) == {"coach/player.md", "coach/journal.md"}
    player = tmp_path / "coach" / "player.md"
    journal = tmp_path / "coach" / "journal.md"
    player.write_text("personal player data", encoding="utf-8")
    journal.write_text("personal journal", encoding="utf-8")
    result = subprocess.run(cmd, cwd=tmp_path, env=env, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    assert set(json.loads(result.stdout)["existing"]) == {"coach/player.md", "coach/journal.md"}
    assert player.read_text(encoding="utf-8") == "personal player data"
    assert journal.read_text(encoding="utf-8") == "personal journal"
    assert (tmp_path / "coach" / "plans").is_dir()
    assert (tmp_path / "coach" / "reviews").is_dir()


def test_account_binding_rejects_changed_user_and_marks_legacy_data(tmp_data, monkeypatch):
    from osucoach import account, context
    from osucoach.store import write_json

    monkeypatch.setattr(P, "BEST", tmp_data / "best.json")
    monkeypatch.setattr(P, "PROFILE_DIR", tmp_data / "profile")
    write_json(P.BEST, [{"score_id": 999}])
    class Client:
        user_id = "123"
        def get(self, path, **params):
            return []
    F.fetch_best(Client())
    assert account.binding()["user_id"] == "123"
    assert context.build()["account"]["legacy_data_present"] is True
    before = P.BEST.read_text(encoding="utf-8")
    Client.user_id = "456"
    with pytest.raises(OsuError, match="bound to another"):
        F.fetch_best(Client())
    assert P.BEST.read_text(encoding="utf-8") == before


def test_recent_fetch_refuses_known_foreign_scores(tmp_data, monkeypatch):
    from osucoach import account
    from osucoach.store import write_json

    monkeypatch.setattr(P, "BEST", tmp_data / "best.json")
    monkeypatch.setattr(P, "PROFILE_DIR", tmp_data / "profile")
    sess = SS.start()
    path = P.session_dir(sess["session_id"]) / "scores.json"
    score = {**_sc(1, 99, sess["started_at"]), "user_id": 456}
    write_json(path, [score])
    before = path.read_text(encoding="utf-8")
    class Client:
        user_id = "123"
        def get(self, path, **params):
            raise AssertionError("must reject saved mismatch before a network request")
    with pytest.raises(OsuError, match="another osu! user"):
        F.fetch_recent(Client(), sess["session_id"], sess)
    assert path.read_text(encoding="utf-8") == before and account.binding() is None


def test_score_mapping_preserves_api_user_id():
    assert S.map_score({**RAW, "user_id": 123})["user_id"] == 123
    assert S.map_score(RAW)["user_id"] is None


def test_consecutive_repeats_respect_map_mods_and_intervening_plays():
    rows = [
        _sc(1, 10, "2026-01-01T00:01:00Z"),
        _sc(2, 10, "2026-01-01T00:02:00Z"),
        _sc(3, 10, "2026-01-01T00:03:00Z"),
        _sc(4, 20, "2026-01-01T00:04:00Z"),
        _sc(5, 10, "2026-01-01T00:05:00Z"),
        _sc(6, 10, "2026-01-01T00:06:00Z"),
    ]
    rows[2]["mods"] = ["HD"]
    S.add_retry_index(rows)
    summary = ST.compute(rows)
    groups = {(m["beatmap_id"], tuple(m["mods"])): m for m in summary["maps"]}
    assert groups[(10, ())]["consecutive_recorded_repeats"] == 2
    assert groups[(10, ("HD",))]["consecutive_recorded_repeats"] == 0
    assert groups[(20, ())]["consecutive_recorded_repeats"] == 0
    assert "rept" in ST.render("session", summary)
