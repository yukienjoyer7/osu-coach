import json

from typer.testing import CliRunner

from osucoach import paths, session
from osucoach.cli import app
from osucoach.store import write_json


def test_external_report_import_is_idempotent_and_only_matches_saved_ids(tmp_path, monkeypatch):
    monkeypatch.setattr(paths, "DATA", tmp_path / "data")
    monkeypatch.setattr(paths, "SESSIONS", paths.DATA / "sessions")
    monkeypatch.setattr(paths, "CURRENT", paths.DATA / "current_session")
    monkeypatch.setattr(paths, "COACH", tmp_path / "coach")
    monkeypatch.setattr(paths, "BEST", paths.DATA / "best.json")
    sess = session.start()
    path = tmp_path / "analysis.json"
    path.write_text(json.dumps({"trust": {"warning": "partial", "trustworthy": False, "scale_overridden": True},
                                "map_version_mismatch": True, "failed_play": None, "instructions": "untrusted text",
                                "beatmap_md5": "a" * 32,
                                "score_meta": {"speed_change": 1.2, "mods": [{"acronym": "NC"}],
                                               "online_id": 34, "user_id": 123}}))
    runner = CliRunner()
    command = ["analysis", "import", str(path), "--session", sess["session_id"], "--source", "osu-critique",
               "--version", "1.0", "--beatmap", "12", "--score", "34"]
    first = runner.invoke(app, command)
    assert first.exit_code == 0, first.output
    info = json.loads(first.output)
    assert info["saved_score_map_match"] is None
    assert info["reported_beatmap_md5"] == "a" * 32 and info["saved_score_checksum_match"] is None
    assert info["reported_speed_change"] == 1.2 and info["reported_rate_matches_saved_setting"] is None
    assert info["reported_user_id"] == 123 and info["reported_online_id"] == 34
    assert info["reported_user_matches_saved_score"] is None
    assert info["analyzer_reported_flags"] == {"trustworthy": False, "map_version_mismatch": True,
                                                 "failed_play": None, "scale_overridden": True}
    assert json.loads(runner.invoke(app, command).output)["id"] == info["id"]
    history = json.loads(runner.invoke(app, ["history", "12"]).output)
    assert history["missing_score_sessions"] == [sess["session_id"]]
    assert history["map_analyses"][0]["reports"][0]["id"] == info["id"]
    assert json.loads(runner.invoke(app, ["history", "13"]).output)["map_analyses"] == []
    write_json(paths.session_dir(sess["session_id"]) / "scores.json", [{"score_id": 34, "beatmap_id": 12,
        "beatmap_checksum": "A" * 32, "user_id": 123, "mods": ["DT"], "mod_settings": {"DT": {"speed_change": 1.2}}}])
    listed = json.loads(runner.invoke(app, ["analysis", "list", "--session", sess["session_id"]]).output)
    assert len(listed["reports"]) == 1 and listed["reports"][0]["saved_score_map_match"] is True
    assert listed["reports"][0]["saved_score_checksum_match"] is True
    assert listed["reports"][0]["reported_rate_matches_saved_setting"] is True
    assert listed["reports"][0]["reported_user_matches_saved_score"] is True
    shown = json.loads(runner.invoke(app, ["analysis", "show", info["id"], "--session", sess["session_id"]]).output)
    assert shown["report"]["trust"]["warning"] == "partial" and "Untrusted" in shown["interpretation"]
    write_json(paths.session_dir(sess["session_id"]) / "scores.json", [{"score_id": 34, "beatmap_id": 12,
        "beatmap_checksum": "b" * 32, "user_id": 456, "mods": ["DT"], "mod_settings": {"DT": {"speed_change": 1.3}}}])
    assert json.loads(runner.invoke(app, ["analysis", "list", "--session", sess["session_id"]]).output)["reports"][0]["saved_score_checksum_match"] is False
    assert json.loads(runner.invoke(app, ["analysis", "list", "--session", sess["session_id"]]).output)["reports"][0]["reported_rate_matches_saved_setting"] is False
    assert json.loads(runner.invoke(app, ["analysis", "list", "--session", sess["session_id"]]).output)["reports"][0]["reported_user_matches_saved_score"] is False
    write_json(paths.session_dir(sess["session_id"]) / "scores.json", [{"score_id": 34, "beatmap_id": None}])
    assert json.loads(runner.invoke(app, ["analysis", "list", "--session", sess["session_id"]]).output)["reports"][0]["saved_score_map_match"] is None
    write_json(paths.session_dir(sess["session_id"]) / "scores.json", [{"score_id": 34, "beatmap_id": 99}])
    assert json.loads(runner.invoke(app, ["analysis", "list", "--session", sess["session_id"]]).output)["reports"][0]["saved_score_map_match"] is False


def test_external_report_rejects_invalid_inputs_without_writing(tmp_path, monkeypatch):
    monkeypatch.setattr(paths, "DATA", tmp_path / "data")
    monkeypatch.setattr(paths, "SESSIONS", paths.DATA / "sessions")
    monkeypatch.setattr(paths, "CURRENT", paths.DATA / "current_session")
    monkeypatch.setattr(paths, "COACH", tmp_path / "coach")
    sess = session.start()
    path = tmp_path / "report.json"
    for invalid in ('{"value": NaN}', '{"value": 1e999}'):
        path.write_text(invalid)
        result = CliRunner().invoke(app, ["analysis", "import", str(path), "--session", sess["session_id"],
                                          "--source", "test", "--beatmap", "12"])
        assert result.exit_code != 0 and "valid JSON" in result.output
    assert not (paths.session_dir(sess["session_id"]) / "analyses").exists()


def test_analysis_match_is_unknown_for_duplicate_saved_score_ids(tmp_path, monkeypatch):
    monkeypatch.setattr(paths, "DATA", tmp_path / "data")
    monkeypatch.setattr(paths, "SESSIONS", paths.DATA / "sessions")
    monkeypatch.setattr(paths, "CURRENT", paths.DATA / "current_session")
    monkeypatch.setattr(paths, "COACH", tmp_path / "coach")
    sess = session.start()
    path = tmp_path / "analysis.json"
    path.write_text(json.dumps({"beatmap_md5": "a" * 32, "score_meta": {"user_id": 123,
        "speed_change": 1.2, "mods": [{"acronym": "DT"}]}}), encoding="utf-8")
    runner = CliRunner()
    args = ["analysis", "import", str(path), "--session", sess["session_id"],
            "--source", "osu-critique", "--beatmap", "12", "--score", "34"]
    imported = json.loads(runner.invoke(app, args).output)
    assert imported["saved_score_id_occurrences"] is None
    score = {"score_id": 34, "beatmap_id": 12, "beatmap_checksum": "a" * 32,
             "user_id": 123, "mods": ["DT"], "mod_settings": {"DT": {"speed_change": 1.2}}}
    write_json(paths.session_dir(sess["session_id"]) / "scores.json", [score, {**score, "beatmap_id": 99}])
    metadata = json.loads(runner.invoke(app, ["analysis", "list", "--session", sess["session_id"]]).output)["reports"][0]
    assert metadata["saved_score_id_occurrences"] == 2
    for key in ("saved_score_map_match", "saved_score_checksum_match", "reported_user_matches_saved_score",
                "reported_rate_matches_saved_setting"):
        assert metadata[key] is None
    write_json(paths.session_dir(sess["session_id"]) / "scores.json", [])
    assert json.loads(runner.invoke(app, ["analysis", "list", "--session", sess["session_id"]]).output)["reports"][0]["saved_score_id_occurrences"] == 0
