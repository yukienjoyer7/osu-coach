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


def test_api_invalid_json_and_redirect_are_clear_errors():
    from osucoach.api import Client

    class Transport:
        def __init__(self, response):
            self.response = response
        def request(self, *args, **kwargs):
            return self.response

    client = Client.__new__(Client)
    client._token = "token"
    client._calls = []
    client._http = Transport(httpx.Response(200, text="not json"))
    with pytest.raises(OsuError, match="invalid JSON response") as error:
        client.get("/test")
    assert "not json" not in str(error.value)
    client._http = Transport(httpx.Response(302))
    with pytest.raises(OsuError, match="HTTP 302"):
        client.get("/test")


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


def test_discover_returns_candidates_and_cursor_with_filter_caveats(tmp_data, monkeypatch):
    monkeypatch.setattr(P, "BEST", tmp_data / "best.json")
    from osucoach import cli
    class Client:
        def get(self, path, **params):
            assert path == "/beatmapsets/search"
            assert params == {"m": 0, "s": "ranked", "q": "stars>=5", "cursor_string": "next"}
            return {"beatmapsets": [{"id": 42, "beatmaps": [{"id": 11, "mode": "taiko"}]}],
                    "total": 10, "cursor_string": "later", "error": None}
    monkeypatch.setattr(cli, "_client", lambda: Client())
    result = CliRunner().invoke(app, ["discover", "stars>=5", "--cursor", "next"])
    assert result.exit_code == 0, result.output
    data = json.loads(result.output)
    assert data["beatmapsets"][0]["beatmaps"][0]["mode"] == "taiko"
    assert data["cursor_string"] == "later"
    assert any("permissions" in item for item in data["selection_limits"])


def test_discover_rejects_unexpected_response(monkeypatch):
    from osucoach import cli
    class Client:
        def get(self, path, **params):
            return {"error": "not exposed"}
    monkeypatch.setattr(cli, "_client", lambda: Client())
    result = CliRunner().invoke(app, ["discover", "test"])
    assert result.exit_code != 0 and "unexpected response" in result.output


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
        user_id = "123"

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


@pytest.mark.parametrize("pp", [None, float("nan"), float("inf"), -1, True, "20000"])
def test_rank_threshold_rejects_invalid_pp_without_overwriting(tmp_path, monkeypatch, pp):
    from osucoach.store import read_json, write_json

    monkeypatch.setattr(P, "PROFILE_DIR", tmp_path / "profile")
    original = {"pp": 8000, "rank_thresholds": {"100": {"pp": 19000}}}
    write_json(F.profile_path(), original)

    class Client:
        def get(self, path, **params):
            return {"ranking": [{"global_rank": 100, "pp": pp}]}

    with pytest.raises(OsuError, match="invalid pp"):
        F.fetch_threshold(Client(), 100)
    assert read_json(F.profile_path()) == original


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


def test_gap_identifies_conflicting_and_unknown_player_data(tmp_path, monkeypatch):
    from osucoach.store import write_json

    monkeypatch.setattr(P, "DATA", tmp_path)
    monkeypatch.setattr(P, "PROFILE_DIR", tmp_path / "profile")
    monkeypatch.setattr(P, "BEST", tmp_path / "best.json")
    write_json(F.profile_path(), {"user_id": 123, "pp": 8000, "fetched_at": "2026-01-01T00:00:00Z",
                                  "rank_threshold_10k_pp": 9000})
    write_json(P.BEST, [{"pp": 300, "user_id": 456}])
    runner = CliRunner()
    result = runner.invoke(app, ["gap", "--json"])
    assert result.exit_code != 0 and "different users" in result.output

    write_json(P.BEST, [{"pp": 300, "user_id": 123}, {"pp": 250}])
    result = runner.invoke(app, ["gap", "--json"])
    evidence = json.loads(result.output)["identity_evidence"]
    assert evidence["profile_user_id"] == 123
    assert evidence["best_scores_with_user_id"] == 1 and evidence["best_scores_total"] == 2
    assert evidence["workspace_account"] is None


@pytest.mark.parametrize("invalid", [float("nan"), float("inf"), -1, True, "5000"])
def test_gap_rejects_invalid_saved_pp_values(tmp_path, monkeypatch, invalid):
    from osucoach.store import write_json

    monkeypatch.setattr(P, "PROFILE_DIR", tmp_path / "profile")
    monkeypatch.setattr(P, "BEST", tmp_path / "best.json")
    profile = {"pp": 8000, "fetched_at": "2026-01-01T00:00:00Z", "rank_threshold_10k_pp": 9000}
    runner = CliRunner()
    for field in ("pp", "rank_threshold_10k_pp"):
        write_json(F.profile_path(), {**profile, field: invalid})
        write_json(P.BEST, [{"pp": 300}])
        result = runner.invoke(app, ["gap"])
        assert result.exit_code != 0 and "invalid pp" in result.output
    write_json(F.profile_path(), profile)
    write_json(P.BEST, [{"pp": invalid}])
    result = runner.invoke(app, ["gap"])
    assert result.exit_code != 0 and "invalid pp" in result.output


# ---- mods ------------------------------------------------------------------
def test_parse_mods():
    assert [m["acronym"] for m in M.parse_mods("HD,DT")] == ["HD", "DT"]
    assert [m["acronym"] for m in M.parse_mods("hddt")] == ["HD", "DT"]
    assert M.parse_mods("NM") == [] and M.parse_mods(None) == []


@pytest.mark.parametrize("mods", ["DTHT", "DTNC", "HTDC", "DTWU", "HRDA", "EZHR", "EZDA"])
def test_parse_mods_rejects_known_incompatible_combinations(mods):
    with pytest.raises(OsuError, match="incompatible"):
        M.parse_mods(mods)


@pytest.mark.parametrize("mods", ["HDD", "D", "HD,", ",DT", "HD,DTX", "HD DT", "NM,HD", "HDHD", "H!"])
def test_invalid_mod_strings_fail_before_api_call(mods, monkeypatch):
    from osucoach import cli

    def unexpected_client():
        raise AssertionError("invalid mods must not reach the API")
    monkeypatch.setattr(cli, "_client", unexpected_client)
    result = CliRunner().invoke(app, ["fetch", "beatmap", "11", "--mods", mods])
    assert result.exit_code != 0
    assert "two-character acronyms" in result.output


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


@pytest.mark.parametrize("mod,valid,invalid", [
    ("DT", (1.01, 2.0), (0.99, 2.01)), ("NC", (1.01, 2.0), (1.0, 2.01)),
    ("HT", (0.5, 0.99), (0.49, 1.0)), ("DC", (0.5, 0.99), (0.49, 1.01)),
])
def test_custom_rate_matches_selected_mod_range(mod, valid, invalid, monkeypatch):
    from osucoach import cli

    monkeypatch.setattr(cli, "_client", lambda: (_ for _ in ()).throw(AssertionError("unexpected client")))
    for rate in valid:
        selected = M.parse_mods(mod)
        M.set_rate(selected, rate)
        assert selected[0]["settings"]["speed_change"] == rate
    for rate in invalid:
        for command in (["fetch", "beatmap", "11"], ["map-attributes", "11"]):
            result = CliRunner().invoke(app, [*command, "--mods", mod, "--rate", str(rate)])
            assert result.exit_code != 0 and "must be between" in result.output


def test_refresh_beatmap_replaces_cached_attributes(tmp_path, monkeypatch):
    from osucoach.store import write_json

    monkeypatch.setattr(P, "BEATMAPS", tmp_path / "beatmaps")
    monkeypatch.setattr(P, "META_DIR", tmp_path / "meta")
    write_json(P.META_DIR / "11.json", {"id": 11, "cs": 4, "ar": 9, "accuracy": 8, "bpm": 180,
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


def test_map_score_preserves_additional_raw_hit_results():
    statistics = {"great": 100, "slider_tail_hit": 5, "large_tick_miss": 2}
    maximum = {"great": 101, "slider_tail_hit": 8}
    score = S.map_score({**RAW, "statistics": statistics, "maximum_statistics": maximum})
    assert score["raw_statistics"] == statistics and score["raw_maximum_statistics"] == maximum
    assert score["raw_statistics"] is not statistics
    assert S.map_score({**RAW, "statistics": None, "maximum_statistics": None})["raw_statistics"] is None
    assert S.map_score({**RAW, "statistics": {}, "maximum_statistics": {}})["raw_maximum_statistics"] == {}


@pytest.mark.parametrize("invalid", [None, -1, True, 1.5, "2"])
def test_invalid_hit_counts_remain_unknown_and_do_not_break_totals(invalid):
    from osucoach import stats

    score = S.map_score({**RAW, "statistics": {"great": 100, "miss": invalid}})
    assert score["miss"] is None and score["n100"] == 0
    valid = {"n300": 50, "n100": 2, "n50": 0, "miss": 1}
    legacy_invalid = {**valid, "miss": invalid}
    result = stats._hit_results([score, valid, legacy_invalid])
    assert result["scores_with_counts"] == 1 and result["scores_total"] == 3
    assert result["totals"] == valid


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
        user_id = "123"

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
    monkeypatch.setattr(F, "fetch_beatmaps_for", lambda client, scores: (0, 0, []))
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
        user_id = "123"

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
        "- rapid trigger: on, 0.15 mm\n"
        "- actuation: 0.8 mm\n"
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
        "rapid_trigger": "on, 0.15 mm", "actuation": "0.8 mm",
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
    assert third["setup"]["changed_since_last"] is None
    SS.close(third["session_id"])
    started_at += timedelta(minutes=1)
    assert SS.start(setup_changed=False)["setup"]["changed_since_last"] is False


def test_setup_records_rapid_trigger_changes(tmp_data, monkeypatch):
    clock = datetime(2026, 11, 21, tzinfo=timezone.utc)
    monkeypatch.setattr(SS, "now_local", lambda: clock)
    P.COACH.mkdir()
    profile = P.COACH / "player.md"
    profile.write_text("- input: mouse\n- rapid trigger: off\n", encoding="utf-8")
    first = SS.start()
    SS.close(first["session_id"])
    clock += timedelta(minutes=1)
    profile.write_text("- input: mouse\n- rapid trigger: on, 0.2 mm\n", encoding="utf-8")
    second = SS.start()
    assert second["setup"]["rapid_trigger"] == "on, 0.2 mm"
    assert second["setup"]["changed_since_last"] is True
    assert SS.load(first["session_id"])["setup"]["rapid_trigger"] == "off"


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


def test_live_observations_preserve_reports_and_recording_time(tmp_data, monkeypatch):
    from osucoach import context

    clock = datetime(2026, 11, 21, tzinfo=timezone.utc)
    monkeypatch.setattr(SS, "now_local", lambda: clock)
    session = SS.start()
    runner = CliRunner()
    first = runner.invoke(app, ["session", "observe", "  streams felt tense  ", "--beatmap", "99", "--json"])
    assert first.exit_code == 0, first.output
    assert json.loads(first.output) == {"reported_at": clock.isoformat(timespec="seconds"),
                                        "text": "streams felt tense", "beatmap_id": 99}
    clock += timedelta(minutes=5)
    assert runner.invoke(app, ["session", "observe", "aim felt better"]).exit_code == 0
    assert runner.invoke(app, ["session", "observe", "   "]).exit_code != 0
    observations = context.build(session["session_id"])["session"]["observations"]
    assert [item["text"] for item in observations] == ["streams felt tense", "aim felt better"]
    assert observations[1]["reported_at"] == clock.isoformat(timespec="seconds")
    history = json.loads(runner.invoke(app, ["history", "99"]).output)
    assert history["missing_score_sessions"] == [session["session_id"]]
    assert history["map_observations"] == [{"session_id": session["session_id"],
                                             "observations": [observations[0]]}]
    assert json.loads(runner.invoke(app, ["history", "100"]).output)["map_observations"] == []
    session = SS.load(session["session_id"])
    session["ended_at"] = clock.isoformat(timespec="seconds")
    SS.save(session)
    assert runner.invoke(app, ["session", "observe", "too late"]).exit_code != 0


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
    context = json.loads(result.stdout)
    assert context["player"] == "- goal: test workspace\n"
    assert context["workspace"] == str(workspace.resolve())


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


def test_context_selects_recent_journal_by_session_not_append_order(tmp_data, monkeypatch):
    from osucoach import context

    monkeypatch.setattr(P, "PROFILE_DIR", tmp_data / "profile")
    monkeypatch.setattr(P, "BEST", tmp_data / "best.json")
    clock = datetime(2026, 11, 21, 10, 0, tzinfo=timezone.utc)
    monkeypatch.setattr(SS, "now_local", lambda: clock)
    ids = []
    for _ in range(5):
        sess = SS.start()
        ids.append(sess["session_id"])
        SS.close(sess["session_id"])
        clock += timedelta(minutes=1)
    P.COACH.mkdir(parents=True, exist_ok=True)
    (P.COACH / "journal.md").write_text(
        "# Journal\n" + "".join(f"## {ids[i]} | focus: aim\nentry-{i}\n" for i in [1, 2, 3, 0]),
        encoding="utf-8")
    journal = context.build(ids[4])["recent_journal"]
    assert "entry-0" not in journal
    assert journal.index("entry-1") < journal.index("entry-2") < journal.index("entry-3")


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
    marker = SS.mark_block(first, "benchmark")
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
    assert history["sessions"][0]["session_started_at"] == first["started_at"]
    assert history["sessions"][0]["session_ended_at"] is None
    assert history["sessions"][0]["blocks"] == [marker]


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
    assert maps[0]["recorded_score_ids"] == [1] and maps[0]["recorded_score_count"] == 1
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


def test_maps_count_distinct_scores_across_mod_order(tmp_data, monkeypatch):
    from osucoach import maps
    from osucoach.store import write_json

    monkeypatch.setattr(P, "BEST", tmp_data / "best.json")
    monkeypatch.setattr(P, "BEATMAPS", tmp_data / "beatmaps")
    first = _sc(1, 99, "2026-11-21T10:01:00Z")
    second = _sc(2, 99, "2026-11-21T10:02:00Z")
    first["mods"], second["mods"] = ["HD", "DT"], ["DT", "HD"]
    write_json(P.BEST, [first, second, first])
    write_json(F.beatmap_path(99, S.score_mods(first)), {"star_rating": 6.5})
    result = maps.find()
    assert len(result) == 1
    assert result[0]["recorded_score_ids"] == [1, 2] and result[0]["recorded_score_count"] == 2


def test_maps_reports_missing_history_and_account_provenance(tmp_data, monkeypatch):
    from osucoach.store import write_json

    monkeypatch.setattr(P, "BEST", tmp_data / "best.json")
    write_json(P.DATA / "account.json", {"user_id": "123", "legacy_data_present": True})
    session = SS.start()
    result = CliRunner().invoke(app, ["maps"])
    assert result.exit_code == 0, result.output
    history = json.loads(result.output)["recorded_history"]
    assert history["account"]["legacy_data_present"] is True
    assert history["best_file_missing"] and history["best_sync"] is None
    assert history["missing_score_sessions"] == [session["session_id"]]


def test_maps_excludes_invalid_cached_values_and_reports_coverage(tmp_data, monkeypatch):
    from osucoach.store import write_json

    monkeypatch.setattr(P, "BEST", tmp_data / "best.json")
    monkeypatch.setattr(P, "BEATMAPS", tmp_data / "beatmaps")
    rows = [_sc(1, 11, "2026-11-21T10:01:00Z"), _sc(2, 12, "2026-11-21T10:02:00Z"),
            _sc(3, 13, "2026-11-21T10:03:00Z")]
    write_json(P.BEST, rows)
    write_json(F.beatmap_path(11, []), {"star_rating": 5.5, "bpm": "unknown"})
    write_json(F.beatmap_path(12, []), {"star_rating": -1})
    runner = CliRunner()
    result = runner.invoke(app, ["maps"])
    assert result.exit_code == 0, result.output
    data = json.loads(result.output)
    assert data["maps"][0]["attributes"]["bpm"] is None
    assert data["excluded_score_records"] == {"missing_attributes": 1, "invalid_star_rating": 1,
                                                "unknown_filter_value": 0}
    filtered = json.loads(runner.invoke(app, ["maps", "--min-bpm", "100"]).output)
    assert filtered["maps"] == [] and filtered["excluded_score_records"]["unknown_filter_value"] == 1


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
    assert s["first_recorded_attempts"] == {"plays": 2, "pass_rate": 100.0, "pass_rate_samples": 2, "acc_passed": 97.0, "acc_passed_samples": 2}
    assert s["later_recorded_attempts"] == {"plays": 2, "pass_rate": 50.0, "pass_rate_samples": 2, "acc_passed": 94.0, "acc_passed_samples": 1}


def test_unknown_pass_status_is_not_a_failure():
    raw = {**RAW, "passed": None}
    assert S.map_score(raw)["passed"] is None
    assert S.map_score({**RAW, "passed": "false"})["passed"] is None
    rows = S.add_retry_index([_sc(1, 1, "2026-01-01T00:00:00Z", passed=True),
                              _sc(2, 1, "2026-01-01T00:01:00Z", passed=None)])
    summary = ST.compute(rows, lambda row: 5.0)
    assert summary["passes"] == 1 and summary["pass_rate"] == 100.0
    assert summary["pass_rate_samples"] == 1 and summary["plays"] == 2
    assert summary["avg_sr_failed"] is None and summary["avg_sr_failed_samples"] == 0
    assert summary["maps"][0]["pass_rate_samples"] == 1
    assert ST.compute(S.add_retry_index([rows[1]]))["pass_rate"] is None


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


@pytest.mark.parametrize("invalid", [None, -0.1, float("nan"), float("inf"), True, "5"])
def test_invalid_star_rating_is_excluded_from_summary(invalid):
    rows = S.add_retry_index([_sc(1, 1, "2026-01-01T00:00:00Z"),
                              _sc(2, 2, "2026-01-01T00:01:00Z")])
    summary = ST.compute(rows, lambda row: 5.0 if row["score_id"] == 1 else invalid)
    assert summary["avg_sr_passed"] == 5.0 and summary["avg_sr_passed_samples"] == 1
    assert next(item for item in summary["maps"] if item["beatmap_id"] == 2)["sr"] is None


@pytest.mark.parametrize("invalid", [None, -0.1, 1.01, float("nan"), float("inf"), True, "0.98"])
def test_invalid_accuracy_is_excluded_from_all_summary_denominators(invalid):
    rows = S.add_retry_index([_sc(1, 1, "2026-01-01T00:00:00Z", acc=0.98),
                              _sc(2, 1, "2026-01-01T00:01:00Z", acc=invalid)])
    summary = ST.compute(rows, blocks=[{"label": "focus", "started_at": "2026-01-01T00:00:00Z"}])
    for part in (summary, summary["maps"][0], summary["blocks"][0]):
        assert part["acc_passed"] == 98.0 and part["acc_passed_samples"] == 1


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
    focus_map = result["blocks"][1]["maps"][0]
    assert focus_map["beatmap_id"] == 3 and focus_map["score_ids"] == [3]
    assert focus_map["crossing_start"] == 1 and focus_map["unknown_start"] == 0
    assert focus_map["passes"] == 0 and focus_map["acc_passed_samples"] == 0
    assert runner.invoke(app, ["session", "block", "same second"]).exit_code != 0


def test_beatmap_fetch_keeps_all_mods_and_settings(tmp_path, monkeypatch):
    from osucoach.store import write_json

    monkeypatch.setattr(P, "BEATMAPS", tmp_path / "beatmaps")
    monkeypatch.setattr(P, "META_DIR", tmp_path / "meta")
    write_json(P.META_DIR / "11.json", {"id": 11, "cs": 4, "ar": 9, "accuracy": 8, "bpm": 180,
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
    write_json(P.META_DIR / "11.json", {"id": 11, "cs": 4, "ar": 9, "accuracy": 8, "bpm": 180,
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


@pytest.mark.parametrize("user_id", ["name", "0", "-5", "１２３", "1/other"])
def test_player_fetch_rejects_non_numeric_identity_before_request(tmp_data, user_id):
    from osucoach import account

    class Client:
        def get(self, *args, **kwargs):
            raise AssertionError("invalid identity must not reach the API")

    client = Client()
    client.user_id = user_id
    with pytest.raises(OsuError, match="positive numeric"):
        F.fetch_best(client)
    assert account.binding() is None


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


def test_score_mapping_retains_score_variants_without_inventing_them():
    score = S.map_score({**RAW, "total_score": 998000, "classic_total_score": 973000,
                         "legacy_total_score": 912345})
    assert (score["total_score"], score["classic_total_score"], score["legacy_total_score"]) == (
        998000, 973000, 912345)
    missing = S.map_score(RAW)
    assert all(missing[field] is None for field in ("total_score", "classic_total_score", "legacy_total_score"))


def test_score_mapping_retains_processing_and_combo_flags():
    score = S.map_score({**RAW, "processed": False, "ranked": True,
                         "is_perfect_combo": True, "legacy_perfect": False})
    assert [score[field] for field in ("processed", "ranked", "is_perfect_combo", "legacy_perfect")] == [False, True, True, False]
    missing = S.map_score({**RAW, "processed": "false", "ranked": 1})
    assert all(missing[field] is None for field in ("processed", "ranked", "is_perfect_combo", "legacy_perfect"))


def test_score_with_no_end_time_is_not_assigned_to_block():
    from osucoach import stats

    row = S.map_score({**RAW, "ended_at": None, "started_at": "2026-11-20T10:00:00Z"})
    row["retry_index"] = 1
    assert row["timestamp_source"] == "started_at"
    summary = stats.compute([row], blocks=[{"label": "focus", "started_at": "2026-11-20T09:59:00Z"}])
    assert summary["plays"] == 1
    assert summary["plays_with_unknown_end_time"] == 1
    assert summary["blocks"][0]["plays"] == 0
    assert summary["plays_before_first_block"] == 0


def test_stats_report_timestamp_ordering_limits():
    from osucoach import stats

    rows = [_sc(1, 99, "2026-11-20T10:00:00Z"),
            _sc(2, 99, "2026-11-20T11:00:00+01:00"),
            _sc(3, 99, "2026-11-20T10:01:00Z")]
    rows[0]["timestamp_source"] = "ended_at"
    rows[1]["timestamp_source"] = "started_at"
    for row in rows:
        row["retry_index"] = 1
    evidence = stats.compute(rows)["ordering_evidence"]
    assert evidence == {"start_time_fallbacks": 1, "unknown_timestamp_source": 1,
                        "tied_timestamp_groups": 1, "scores_in_tied_groups": 2}


def test_session_boundary_evidence_distinguishes_known_and_unknown_times():
    rows = [_sc(1, 99, "2026-11-20T10:01:00Z"),
            _sc(2, 99, "2026-11-20T10:11:00Z"),
            _sc(3, 99, "2026-11-20T10:02:00Z")]
    rows[0].update(timestamp_source="ended_at", started_at="2026-11-20T09:59:00Z")
    rows[1].update(timestamp_source="ended_at", started_at="2026-11-20T10:09:00Z")
    rows[2].update(timestamp_source="started_at", started_at=None)
    S.add_retry_index(rows)
    evidence = ST.compute(rows, session_started_at="2026-11-20T10:00:00Z",
                          session_ended_at="2026-11-20T10:10:00Z")["session_boundary_evidence"]
    assert evidence == {"started_before_session": 1, "unknown_start": 0,
                        "ended_after_session": 1, "unknown_end": 1}
    live = ST.compute(rows, session_started_at="2026-11-20T10:00:00Z")["session_boundary_evidence"]
    assert live["ended_after_session"] is None


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


def test_score_mapping_preserves_source_identifiers_without_inference():
    known = S.map_score({**RAW, "legacy_score_id": 12345, "build_id": None})
    assert known["legacy_score_id"] == 12345 and known["build_id"] is None
    current = S.map_score({**RAW, "legacy_score_id": None, "build_id": 800})
    assert current["legacy_score_id"] is None and current["build_id"] == 800
    unknown = S.map_score(RAW)
    assert unknown["legacy_score_id"] is None and unknown["build_id"] is None


def test_block_maps_keep_mod_settings_and_unknown_start_separate():
    rows = S.add_retry_index([
        _sc(1, 10, "2026-01-01T00:01:00Z", acc=0.95),
        _sc(2, 10, "2026-01-01T00:02:00Z", acc=0.98),
        _sc(3, 10, "2026-01-01T00:03:00Z", passed=False),
    ])
    rows[0]["mods"] = ["DT"]
    rows[0]["mod_settings"] = {"DT": {"speed_change": 1.2}}
    rows[1]["mods"] = ["DT"]
    rows[1]["mod_settings"] = {"DT": {"speed_change": 1.5}}
    rows[2]["mods"] = ["DT"]
    rows[2]["mod_settings"] = {"DT": {"speed_change": 1.5}}
    rows[2]["started_at"] = "2025-12-31T23:59:00Z"
    block = [{"label": "focus", "started_at": "2026-01-01T00:00:00Z"}]
    groups = ST.compute(rows, blocks=block)["blocks"][0]["maps"]
    assert len(groups) == 2
    assert groups[0]["score_ids"] == [1] and groups[0]["unknown_start"] == 1
    assert groups[1]["score_ids"] == [2, 3] and groups[1]["crossing_start"] == 1
    assert groups[1]["passes"] == 1 and groups[1]["acc_passed"] == 98.0
    assert groups[1]["fully_contained"]["score_ids"] == []
    rows[1]["started_at"] = "2026-01-01T00:00:30Z"
    focus = ST.compute(rows, blocks=block)["blocks"][0]
    assert focus["fully_contained"]["score_ids"] == [2]
    assert focus["fully_contained"]["acc_passed"] == 98.0
    assert focus["maps"][1]["fully_contained"]["score_ids"] == [2]


def test_block_containment_excludes_post_session_grace_scores(tmp_data, monkeypatch):
    from osucoach import context
    from osucoach.store import write_json

    monkeypatch.setattr(P, "PROFILE_DIR", tmp_data / "profile")
    monkeypatch.setattr(P, "BEST", tmp_data / "best.json")
    monkeypatch.setattr(P, "BEATMAPS", tmp_data / "beatmaps")
    clock = datetime(2026, 1, 1, tzinfo=timezone.utc)
    monkeypatch.setattr(SS, "now_local", lambda: clock)
    sess = SS.start()
    clock += timedelta(minutes=1)
    SS.mark_block(sess, "focus")
    clock += timedelta(minutes=9)
    SS.stamp_end(sess)
    SS.save(sess)
    rows = S.add_retry_index([
        _sc(1, 10, "2026-01-01T00:10:00Z"),
        _sc(2, 10, "2026-01-01T00:11:00Z"),
    ])
    rows[0]["started_at"] = "2026-01-01T00:09:00Z"
    rows[1]["started_at"] = "2026-01-01T00:09:30Z"
    write_json(P.session_dir(sess["session_id"]) / "scores.json", rows)
    block = context.build(sess["session_id"])["stats"]["blocks"][0]
    assert block["plays"] == 2 and block["after_session_end"] == 1
    assert block["fully_contained"]["score_ids"] == [1]
    assert block["maps"][0]["after_session_end"] == 1
    assert block["maps"][0]["fully_contained"]["score_ids"] == [1]
    result = CliRunner().invoke(app, ["stats", sess["session_id"], "--json"])
    assert json.loads(result.output)["blocks"][0]["fully_contained"]["score_ids"] == [1]


def test_map_search_exposes_tempo_and_length_limitations(tmp_data, monkeypatch):
    monkeypatch.setattr(P, "BEST", tmp_data / "best.json")
    result = CliRunner().invoke(app, ["maps"])
    assert result.exit_code == 0, result.output
    limits = json.loads(result.output)["selection_limits"]
    assert any("tapping speed" in item for item in limits)
    assert any("includes breaks" in item for item in limits)


def test_session_scores_command_exposes_raw_rows_and_missing_coverage(tmp_data):
    from osucoach.store import write_json

    sess = SS.start()
    sid = sess["session_id"]
    runner = CliRunner()
    result = runner.invoke(app, ["scores", sid])
    assert result.exit_code == 0, result.output
    data = json.loads(result.output)
    assert data["scores"] is None and data["scores_sync"] is None
    assert data["started_at"] == sess["started_at"] and data["ended_at"] is None
    score = {**_sc(12, 34, sess["started_at"]), "user_id": 123, "legacy_score_id": 7}
    write_json(P.session_dir(sid) / "scores.json", [score])
    write_json(P.session_dir(sid) / "scores_sync.json", {"returned_scores": 100})
    result = runner.invoke(app, ["scores", sid])
    data = json.loads(result.output)
    assert data["scores"] == [score] and data["scores_sync"]["limit_reached_ever"] is True
    write_json(P.session_dir(sid) / "scores.json", [])
    assert json.loads(runner.invoke(app, ["scores", sid]).output)["scores"] == []


def test_map_history_includes_best_scores_without_implying_complete_history(tmp_data, monkeypatch):
    from osucoach import history
    from osucoach.store import write_json

    monkeypatch.setattr(P, "BEST", tmp_data / "best.json")
    sess = SS.start()
    sid = sess["session_id"]
    recorded = _sc(5, 99, sess["started_at"])
    write_json(P.session_dir(sid) / "scores.json", [recorded])
    missing = history.map_history(99)
    assert missing["best_scores"] is None and missing["best_session_duplicate_score_ids"] is None
    write_json(P.BEST, [recorded, _sc(6, 99, "2025-01-01T00:00:00Z"),
                        _sc(7, 100, "2025-01-01T00:00:00Z")])
    write_json(P.BEST.with_name("best_sync.json"), {"fetched_at": "2026-01-01T00:00:00Z"})
    result = history.map_history(99)
    assert [score["score_id"] for score in result["best_scores"]] == [6, 5]
    assert result["best_session_duplicate_score_ids"] == [5]
    assert result["best_sync"]["fetched_at"] == "2026-01-01T00:00:00Z"
    assert history.map_history(101)["best_scores"] == []


def test_map_history_identifies_duplicate_plays_across_sessions(tmp_data, monkeypatch):
    from osucoach import history
    from osucoach.store import write_json

    monkeypatch.setattr(P, "BEST", tmp_data / "best.json")
    clock = datetime(2026, 11, 21, 10, 0, tzinfo=timezone.utc)
    monkeypatch.setattr(SS, "now_local", lambda: clock)
    first = SS.start()
    SS.close(first["session_id"])
    clock += timedelta(minutes=1)
    second = SS.start()
    shared = _sc(10, 99, "2026-11-21T10:01:30Z")
    unique = _sc(11, 99, "2026-11-21T10:01:40Z")
    write_json(P.session_dir(first["session_id"]) / "scores.json", [shared])
    write_json(P.session_dir(second["session_id"]) / "scores.json", [shared, unique])
    result = history.map_history(99)
    assert result["session_duplicate_scores"] == [{"score_id": 10,
        "session_ids": [first["session_id"], second["session_id"]]}]
    assert result["unique_recorded_session_score_count"] == 2
    assert [len(item["scores"]) for item in result["sessions"]] == [1, 2]


def test_discover_locally_filters_difficulties_and_counts_missing_fields(tmp_data, monkeypatch):
    monkeypatch.setattr(P, "BEST", tmp_data / "best.json")
    from osucoach import maps
    class Client:
        def get(self, path, **params):
            return {"beatmapsets": [
                {"id": 1, "title": "set", "beatmaps": [
                    {"id": 11, "mode": "osu", "status": "ranked", "difficulty_rating": 5.5,
                     "lazer_only": True, "total_length": 180},
                    {"id": 12, "mode": "taiko", "status": "ranked", "difficulty_rating": 5.5},
                    {"id": 13, "mode": "osu", "status": "pending", "difficulty_rating": 5.5},
                    {"id": 14, "mode": "osu", "status": "ranked", "difficulty_rating": 7},
                    {"id": 15, "mode": "osu", "status": "ranked"}]},
                {"id": 2, "title": "missing difficulties"}]}
    result = maps.discover(Client(), "streams", min_sr=5, max_sr=6)
    assert [item["beatmap_id"] for item in result["ranked_osu_candidates"]] == [11]
    assert result["ranked_osu_candidates"][0]["lazer_only"] is True
    assert result["ranked_osu_candidates"][0]["total_length_s"] == 180
    assert result["incomplete_difficulties"] == 1 and result["sets_without_difficulties"] == 1
    assert len(result["beatmapsets"]) == 2


def test_discover_reports_only_recorded_history_without_claiming_unplayed(tmp_data, monkeypatch):
    from osucoach import maps
    from osucoach.store import write_json

    monkeypatch.setattr(P, "BEST", tmp_data / "best.json")
    clock = datetime(2026, 11, 21, 10, 0, tzinfo=timezone.utc)
    monkeypatch.setattr(SS, "now_local", lambda: clock)
    first = SS.start()
    SS.close(first["session_id"])
    clock += timedelta(minutes=1)
    missing = SS.start()
    recorded = _sc(1, 11, "2026-11-21T10:00:00Z")
    write_json(P.session_dir(first["session_id"]) / "scores.json", [recorded])
    write_json(P.BEST, [recorded])
    class Client:
        def get(self, path, **params):
            return {"beatmapsets": [{"id": 1, "beatmaps": [
                {"id": 11, "mode": "osu", "status": "ranked", "difficulty_rating": 5.5},
                {"id": 12, "mode": "osu", "status": "ranked", "difficulty_rating": 5.6}]}]}
    result = maps.discover(Client(), "")
    assert [c["recorded_score_count"] for c in result["ranked_osu_candidates"]] == [1, 0]
    assert result["recorded_history"]["missing_score_sessions"] == [missing["session_id"]]
    assert result["recorded_history"]["best_file_missing"] is False
    assert any("zero does not mean" in note for note in result["selection_limits"])


def test_discover_filters_player_tags_without_claiming_pattern(tmp_data, monkeypatch):
    from osucoach import maps
    from osucoach.store import write_json

    monkeypatch.setattr(P, "BEST", tmp_data / "best.json")
    write_json(P.DATA / "map_tags" / "11.json", {"beatmap_id": 11, "tags": ["bursts"]})
    class Client:
        def get(self, path, **params):
            return {"beatmapsets": [{"id": 1, "beatmaps": [
                {"id": 11, "mode": "osu", "status": "ranked", "difficulty_rating": 5},
                {"id": 12, "mode": "osu", "status": "ranked", "difficulty_rating": 5}]}]}
    result = maps.discover(Client(), "", tag="BURSTS")
    assert [item["beatmap_id"] for item in result["ranked_osu_candidates"]] == [11]
    assert result["ranked_osu_candidates"][0]["user_tags"] == ["bursts"]
    assert any("subjective" in item for item in result["selection_limits"])


def test_map_attributes_batch_preserves_successes_and_errors(monkeypatch):
    from osucoach import cli
    monkeypatch.setattr(cli, "_client", lambda: object())
    calls = []
    def fetch(client, beatmap_id, mods, refresh):
        calls.append((beatmap_id, mods, refresh))
        if beatmap_id == 12:
            raise OsuError("map unavailable")
        return {"star_rating": 5.5, "mods": ["DT"]}, True
    monkeypatch.setattr(F, "fetch_beatmap", fetch)
    result = CliRunner().invoke(app, ["map-attributes", "11,12,11", "--mods", "DT", "--rate", "1.3"])
    assert result.exit_code == 0, result.output
    output = json.loads(result.output)
    assert [row["beatmap_id"] for row in output["results"]] == [11, 12]
    assert output["results"][0]["attributes"]["star_rating"] == 5.5
    assert output["results"][1]["attributes"] is None and output["results"][1]["error"] == "map unavailable"
    assert calls[0][1] == [{"acronym": "DT", "settings": {"speed_change": 1.3}}]


def test_map_attributes_invalid_batch_fails_before_client(monkeypatch):
    from osucoach import cli
    monkeypatch.setattr(cli, "_client", lambda: (_ for _ in ()).throw(AssertionError("unexpected client")))
    for ids in ("1,,2", "-1", "0", ",".join(str(i) for i in range(1, 27))):
        result = CliRunner().invoke(app, ["map-attributes", "--", ids])
        assert result.exit_code != 0 and "positive comma-separated" in result.output


def test_batch_reports_malformed_attributes_and_continues(tmp_data, monkeypatch):
    from osucoach import cli
    from osucoach.store import write_json

    monkeypatch.setattr(P, "BEATMAPS", tmp_data / "beatmaps")
    monkeypatch.setattr(P, "META_DIR", tmp_data / "meta")
    for bid in (11, 12):
        write_json(P.META_DIR / f"{bid}.json", {"id": bid, "cs": 4, "ar": 9, "accuracy": 8,
                   "bpm": 180, "total_length": 100, "count_circles": 1, "count_sliders": 2,
                   "count_spinners": 0})
    class Client:
        def post(self, path, body):
            if "/11/" in path:
                return {"attributes": {"star_rating": None}}
            return {"attributes": {"star_rating": 5.2, "max_combo": 50}}
    monkeypatch.setattr(cli, "_client", lambda: Client())
    result = CliRunner().invoke(app, ["map-attributes", "11,12"])
    assert result.exit_code == 0, result.output
    first, second = json.loads(result.output)["results"]
    assert first["attributes"] is None and "invalid difficulty attributes" in first["error"]
    assert second["attributes"]["star_rating"] == 5.2 and second["error"] is None


def test_session_map_fetch_isolates_missing_and_malformed_maps(tmp_data, monkeypatch):
    from osucoach.api import HttpError

    monkeypatch.setattr(P, "BEATMAPS", tmp_data / "beatmaps")
    rows = [_sc(1, 11, "2026-11-20T10:00:00Z"), _sc(2, 12, "2026-11-20T10:01:00Z"),
            _sc(3, 13, "2026-11-20T10:02:00Z")]

    def get_map(client, beatmap_id, mods):
        if beatmap_id == 11:
            raise HttpError("GET", "/beatmaps/11", 404)
        if beatmap_id == 12:
            raise F.MapDataError("beatmap 12 returned invalid difficulty attributes")
        return {}, True

    monkeypatch.setattr(F, "fetch_beatmap", get_map)
    count, fetched, errors = F.fetch_beatmaps_for(object(), rows)
    assert (count, fetched) == (3, 1)
    assert [error["beatmap_id"] for error in errors] == [11, 12]
    assert all(row["map_max_combo"] is None for row in rows)
    monkeypatch.setattr(F, "fetch_beatmap", lambda *args: (_ for _ in ()).throw(HttpError("GET", "/beatmaps/11", 503)))
    with pytest.raises(HttpError):
        F.fetch_beatmaps_for(object(), rows)


def test_session_end_closes_when_individual_map_is_unavailable(tmp_data, monkeypatch):
    from osucoach import cli
    from osucoach import context

    session = SS.start()
    error = {"beatmap_id": 11, "mods": [], "error": "not found"}
    monkeypatch.setattr(cli, "_client", lambda: object())
    monkeypatch.setattr(F, "fetch_recent", lambda *args: S.add_retry_index([_sc(1, 11, session["started_at"])]))
    monkeypatch.setattr(F, "fetch_beatmaps_for", lambda *args: (1, 0, [error]))
    result = CliRunner().invoke(app, ["session", "end", "--json"])
    assert result.exit_code == 0, result.output
    assert json.loads(result.output)["map_errors"] == [error]
    assert SS.current() is None and SS.load(session["session_id"])["ended_at"] is not None
    saved = context.build(session["session_id"])["map_sync"]
    assert saved["errors"] == [error] and saved["maps"] == 1 and saved["fetched"] == 0
    monkeypatch.setattr(F, "fetch_beatmaps_for", lambda *args: (1, 1, []))
    retry = CliRunner().invoke(app, ["fetch", "beatmaps", "--session", session["session_id"], "--json"])
    assert retry.exit_code == 0, retry.output
    saved = context.build(session["session_id"])["map_sync"]
    assert saved["errors"] == [] and saved["fetched"] == 1


def test_invalid_cached_map_metadata_is_refetched(tmp_data, monkeypatch):
    from osucoach.store import write_json

    monkeypatch.setattr(P, "BEATMAPS", tmp_data / "beatmaps")
    monkeypatch.setattr(P, "META_DIR", tmp_data / "meta")
    write_json(P.META_DIR / "11.json", {"id": 11, "cs": None})
    class Client:
        def get(self, path):
            return {"id": 11, "cs": 4, "ar": 9, "accuracy": 8, "bpm": 180,
                    "total_length": 100, "count_circles": 1, "count_sliders": 2, "count_spinners": 0}
        def post(self, path, body):
            return {"attributes": {"star_rating": 5}}
    data, fetched = F.fetch_beatmap(Client(), 11, [])
    assert fetched and data["cs"] == 4


def test_negative_cached_and_returned_stars_are_rejected(tmp_data, monkeypatch):
    from osucoach.store import write_json

    monkeypatch.setattr(P, "BEATMAPS", tmp_data / "beatmaps")
    monkeypatch.setattr(P, "META_DIR", tmp_data / "meta")
    write_json(F.beatmap_path(11, []), {"star_rating": -0.0001})
    write_json(P.META_DIR / "11.json", {"id": 11, "cs": 4, "ar": 9, "accuracy": 8, "bpm": 180,
                                         "total_length": 100, "count_circles": 1,
                                         "count_sliders": 2, "count_spinners": 0})
    class Client:
        def post(self, path, body):
            return {"attributes": {"star_rating": -0.0001}}

    with pytest.raises(F.MapDataError, match="invalid difficulty"):
        F.fetch_beatmap(Client(), 11, [])


def test_nullable_map_bpm_preserves_other_attributes(tmp_data, monkeypatch):
    monkeypatch.setattr(P, "BEATMAPS", tmp_data / "beatmaps")
    monkeypatch.setattr(P, "META_DIR", tmp_data / "meta")

    class Client:
        def get(self, path):
            return {"id": 11, "cs": 4, "ar": 9, "accuracy": 8, "bpm": None,
                    "total_length": 120, "count_circles": 1, "count_sliders": 2, "count_spinners": 0}

        def post(self, path, body):
            return {"attributes": {"star_rating": 5}}

    data, fetched = F.fetch_beatmap(Client(), 11, [{"acronym": "DT"}])
    assert fetched and data["star_rating"] == 5 and data["bpm"] is None
    assert data["length_s"] == 80 and data["ar"] > 9
    assert F._valid_map_meta({"id": 11, "cs": 4, "ar": 9, "accuracy": 8,
                              "bpm": None, "total_length": 120, "count_circles": 1,
                              "count_sliders": 2, "count_spinners": 0}, 11)
