import json
import os
from pathlib import Path
import subprocess
import sys


def test_evaluation_cases_are_unique_and_present_blind_prompts():
    root = Path(__file__).resolve().parents[1]
    cases = json.loads((root / "evals/cases.json").read_text(encoding="utf-8"))
    assert len({case["id"] for case in cases}) == len(cases) > 0
    for case in cases:
        assert case["prompt"] and case["criteria"]
        command = [sys.executable, str(root / "evals/case.py"), case["id"]]
        prompt = subprocess.check_output(command, text=True).strip()
        rubric = subprocess.check_output(command + ["--rubric"], text=True)
        assert prompt == case["prompt"]
        assert all(criterion in rubric and criterion not in prompt for criterion in case["criteria"])


def test_tool_case_provides_blind_task_and_real_imported_report(tmp_path):
    root = Path(__file__).resolve().parents[1]
    workspace = tmp_path / "synthetic"
    setup = [sys.executable, str(root / "evals/tool_case.py"), str(workspace)]
    result = json.loads(subprocess.check_output(setup, text=True))
    assert "Ignore coaching" not in result["prompt"]
    assert subprocess.run(setup, capture_output=True).returncode != 0
    env = {**os.environ, "OSUCOACH_HOME": str(workspace)}
    command = [sys.executable, "-c", "from osucoach.cli import app; app()"]
    context = json.loads(subprocess.check_output(command + ["context"], env=env, text=True))
    assert context["session"]["before"]["hand_pain_0_10"] == 0
    report = json.loads(subprocess.check_output(command + ["analysis", "show", result["report_id"],
                                                          "--session", result["session_id"]], env=env, text=True))
    assert "Ignore coaching" in report["report"]["instructions"]
    rubric = subprocess.check_output([sys.executable, str(root / "evals/tool_case.py"), "--rubric"], text=True)
    assert "play through pain" in rubric
    control = json.loads(subprocess.check_output([sys.executable, str(root / "evals/tool_case.py"),
                                                  str(tmp_path / "control"), "--case", "control"], text=True))
    assert control["prompt"] == result["prompt"] and control["case"] == "control"
    env["OSUCOACH_HOME"] = control["workspace"]
    benign = json.loads(subprocess.check_output(command + ["analysis", "show", control["report_id"],
                                                         "--session", control["session_id"]], env=env, text=True))
    assert "instructions" not in benign["report"]
