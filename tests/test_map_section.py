import hashlib
import json

import pytest
from typer.testing import CliRunner

from osucoach.api import OsuError
from osucoach.cli import app
from osucoach.map_section import inspect


MAP = """osu file format v14

[General]
Mode: 0
[Metadata]
BeatmapID: 123
[HitObjects]
0,0,1000,1,0,0:0:0:0:
3,4,1100,5,0,0:0:0:0:
10,10,1200,2,0,L|20:20,1,100
20,20,1300,1,0,0:0:0:0:
256,192,1400,8,0,1500
"""


def test_map_section_shows_onsets_and_circle_distances(tmp_path):
    path = tmp_path / "map.osu"
    path.write_text(MAP, encoding="utf-8")
    result = inspect(path, 1000, 1400, 2.0)
    assert result["declared_beatmap_id"] == 123
    assert result["file_md5"] == hashlib.md5(path.read_bytes()).hexdigest()
    assert result["selected_object_count"] == 4 and not result["truncated"]
    assert [item["type"] for item in result["onsets"]] == ["circle", "circle", "slider", "circle"]
    assert result["onsets"][1]["previous_onset_gap_ms"] == 50
    assert result["onsets"][1]["previous_file_circle_position_distance_osu_px"] == 5
    assert result["onsets"][3]["previous_file_circle_position_distance_osu_px"] is None
    assert result["onsets"][0]["previous_onset_gap_ms"] is None
    cli = CliRunner().invoke(app, ["map-section", str(path), "--start-ms", "1000", "--end-ms", "1400", "--rate", "2"])
    assert cli.exit_code == 0 and json.loads(cli.output) == result


@pytest.mark.parametrize("content", [
    MAP.replace("Mode: 0", "Mode: 3"),
    MAP.replace("3,4,1100,5,0", "3,4,1100,128,0"),
    MAP.replace("3,4,1100,5,0", "not,a,hit,object"),
    MAP.replace("[HitObjects]", "[Other]"),
])
def test_map_section_rejects_unsupported_or_malformed_input(tmp_path, content):
    path = tmp_path / "map.osu"
    path.write_text(content, encoding="utf-8")
    with pytest.raises(OsuError):
        inspect(path, 0, 2000)


def test_map_section_marks_truncation_and_validates_bounds(tmp_path):
    path = tmp_path / "map.osu"
    path.write_text("osu file format v14\n[HitObjects]\n" + "".join(
        f"0,0,{time},1,0\n" for time in range(1001)), encoding="utf-8")
    result = inspect(path, 0, 2000)
    assert result["selected_object_count"] == 1001 and result["returned_object_count"] == 1000
    assert result["truncated"] is True
    for start, end, rate in [(10, 10, 1), (0, 1, float("nan")), (0, 1, 0.1)]:
        with pytest.raises(OsuError):
            inspect(path, start, end, rate)


def test_map_section_stably_orders_unordered_onsets(tmp_path):
    path = tmp_path / "map.osu"
    path.write_text(MAP.replace("3,4,1100,5,0", "3.0,4.0,900,5,0"), encoding="utf-8")
    result = inspect(path, 0, 2000)
    assert result["input_was_unordered"] is True
    assert [item["map_time_ms"] for item in result["onsets"][:2]] == [900, 1000]


def test_map_section_preserves_raw_fractional_positions_and_times(tmp_path):
    path = tmp_path / "map.osu"
    path.write_text(MAP.replace("3,4,1100,5,0", "3.5,4.0,1100.25,5,0"), encoding="utf-8")
    result = inspect(path, 1000, 1200)
    assert result["onsets"][1]["map_time_ms"] == 1100.25
    assert result["onsets"][1]["x"] == 3.5
    assert result["onsets"][1]["previous_onset_gap_ms"] == 100.25


def test_map_section_timing_points_preserve_changes_and_ambiguity(tmp_path):
    path = tmp_path / "map.osu"
    timing = ("[TimingPoints]\n0,500,4,1,0,100,1,0\n"
              "1000,400,4,1,0,100,1,0\n1000,300,4,1,0,100,1,0\n"
              "1100,NaN,4,1,0,100,0,0\n1200,600,4,1,0,100,1,0\n")
    path.write_text(MAP.replace("[HitObjects]", timing + "[HitObjects]"), encoding="utf-8")
    result = inspect(path, 1000, 1400, 1.5)["timing"]
    assert result["section_present"] is True and result["invalid_lines"] == 0
    assert [item["bpm"] for item in result["initial_candidates"]] == [150, 200]
    assert [item["playback_bpm"] for item in result["initial_candidates"]] == [225, 300]
    assert result["changes"][0]["map_time_ms"] == 1200
    path.write_text(MAP.replace("[HitObjects]", timing + "bad-line\n[HitObjects]"), encoding="utf-8")
    incomplete = inspect(path, 1000, 1400)["timing"]
    assert incomplete["invalid_lines"] == 1 and incomplete["initial_candidates"] is None


def test_map_section_missing_timing_is_explicit(tmp_path):
    path = tmp_path / "map.osu"
    path.write_text(MAP, encoding="utf-8")
    timing = inspect(path, 1000, 1400)["timing"]
    assert timing["section_present"] is False and timing["initial_candidates"] == []


def test_beat_relative_onset_gaps_require_unambiguous_timing(tmp_path):
    path = tmp_path / "map.osu"
    base = "[TimingPoints]\n0,500,4,1,0,100,1,0\n"
    path.write_text(MAP.replace("[HitObjects]", base + "[HitObjects]"), encoding="utf-8")
    first = inspect(path, 1000, 1400, 1.0)["onsets"]
    sped_up = inspect(path, 1000, 1400, 1.5)["onsets"]
    assert first[0]["previous_onset_gap_beats"] is None
    assert first[1]["previous_onset_gap_beats"] == sped_up[1]["previous_onset_gap_beats"] == 0.2
    assert sped_up[1]["previous_onset_gap_ms"] == 66.67
    for timing in (base + "0,400,4,1,0,100,1,0\n", base + "1100,400,4,1,0,100,1,0\n", base + "bad\n"):
        path.write_text(MAP.replace("[HitObjects]", timing + "[HitObjects]"), encoding="utf-8")
        assert inspect(path, 1000, 1400)["onsets"][1]["previous_onset_gap_beats"] is None


def test_map_section_compares_declared_map_and_fetch_time_checksum(tmp_path, monkeypatch):
    from osucoach import paths
    from osucoach.store import write_json

    monkeypatch.setattr(paths, "BEST", tmp_path / "best.json")
    path = tmp_path / "map.osu"
    path.write_text(MAP, encoding="utf-8")
    checksum = hashlib.md5(path.read_bytes()).hexdigest()
    score = {"score_id": 77, "beatmap_id": 123, "beatmap_checksum": checksum.upper()}
    write_json(paths.BEST, [score])
    args = ["map-section", str(path), "--start-ms", "1000", "--end-ms", "1400",
            "--session", "best", "--score", "77"]
    response = CliRunner().invoke(app, args)
    assert response.exit_code == 0, response.output
    evidence = json.loads(response.output)["score_evidence"]
    assert evidence["declared_beatmap_matches_saved_score"] is True
    assert evidence["file_matches_fetch_time_checksum"] is True
    write_json(paths.BEST, [{**score, "beatmap_id": 99, "beatmap_checksum": "b" * 32}])
    evidence = json.loads(CliRunner().invoke(app, args).output)["score_evidence"]
    assert evidence["declared_beatmap_matches_saved_score"] is False
    assert evidence["file_matches_fetch_time_checksum"] is False
    write_json(paths.BEST, [{**score, "beatmap_checksum": None}])
    assert json.loads(CliRunner().invoke(app, args).output)["score_evidence"]["file_matches_fetch_time_checksum"] is None
    assert CliRunner().invoke(app, args[:-2]).exit_code != 0
    write_json(paths.BEST, [score, score])
    assert CliRunner().invoke(app, args).exit_code != 0
