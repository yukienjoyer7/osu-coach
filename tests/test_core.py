"""Offline tests: no network, no real data/ writes."""
import pytest

from osucoach import fetch as F
from osucoach import gap as G
from osucoach import mods as M
from osucoach import paths as P
from osucoach import scores as S
from osucoach import session as SS
from osucoach import stats as ST
from osucoach.api import OsuError


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


@pytest.fixture
def tmp_data(tmp_path, monkeypatch):
    monkeypatch.setattr(P, "DATA", tmp_path)
    monkeypatch.setattr(P, "SESSIONS", tmp_path / "sessions")
    monkeypatch.setattr(P, "CURRENT", tmp_path / "current_session")
    return tmp_path


def test_session_lifecycle(tmp_data):
    assert SS.current() is None
    s = SS.start({"sleep_h": 7, "hand_pain_0_10": 1})
    assert SS.current() == s["session_id"] and s["before"]["sleep_h"] == 7
    assert s["before"]["energy_1_5"] is None and s["setup"]["client"] == "lazer"
    with pytest.raises(OsuError):
        SS.start()
    SS.stamp_end(s)
    SS.apply_updates(s, after={"fatigue_1_5": 2}, followed_plan="partly")
    SS.save(s)
    SS.close(s["session_id"])
    assert SS.current() is None
    assert SS.resolve() == s["session_id"]  # falls back to last
    assert SS.load(s["session_id"])["after"]["fatigue_1_5"] == 2


def test_apply_updates_validation():
    s = {"before": {}, "after": {}, "setup": {}}
    with pytest.raises(OsuError):
        SS.apply_updates(s, after={"bogus": 1})
    with pytest.raises(OsuError):
        SS.apply_updates(s, followed_plan="maybe")


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
