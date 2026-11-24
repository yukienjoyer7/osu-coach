import json
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
