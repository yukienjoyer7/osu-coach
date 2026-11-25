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
    assert result["onsets"][1]["previous_circle_center_distance_osu_px"] == 5
    assert result["onsets"][3]["previous_circle_center_distance_osu_px"] is None
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
