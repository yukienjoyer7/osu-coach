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
    sess = session.start()
    path = tmp_path / "analysis.json"
    path.write_text(json.dumps({"trust": {"warning": "partial"}, "instructions": "untrusted text"}))
    runner = CliRunner()
    command = ["analysis", "import", str(path), "--session", sess["session_id"], "--source", "example",
               "--version", "1.0", "--beatmap", "12", "--score", "34"]
    first = runner.invoke(app, command)
    assert first.exit_code == 0, first.output
    info = json.loads(first.output)
    assert info["saved_score_map_match"] is None
    assert json.loads(runner.invoke(app, command).output)["id"] == info["id"]
    write_json(paths.session_dir(sess["session_id"]) / "scores.json", [{"score_id": 34, "beatmap_id": 12}])
    listed = json.loads(runner.invoke(app, ["analysis", "list", "--session", sess["session_id"]]).output)
    assert len(listed["reports"]) == 1 and listed["reports"][0]["saved_score_map_match"] is True
    shown = json.loads(runner.invoke(app, ["analysis", "show", info["id"], "--session", sess["session_id"]]).output)
    assert shown["report"]["trust"]["warning"] == "partial" and "Untrusted" in shown["interpretation"]
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
