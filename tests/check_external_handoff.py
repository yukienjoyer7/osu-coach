"""Optional integration smoke test against a locally installed osu-critique checkout."""
import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--analyzer-root", type=Path, required=True, help="osu-critique checkout with synthetic fixtures")
    parser.add_argument("--analyzer-bin", type=Path, required=True, help="installed osu-critique executable")
    args = parser.parse_args()
    root, analyzer = args.analyzer_root.resolve(), args.analyzer_bin.resolve()
    with tempfile.TemporaryDirectory() as temp:
        temp = Path(temp)
        output = temp / "out"
        env = {key: value for key, value in os.environ.items()
               if not key.startswith("OSU_") and key != "OSUCOACH_HOME"}
        subprocess.run([str(analyzer), "analyze", str(root / "tests/fixtures/synth_clean.osr"),
                        str(root / "tests/fixtures/synth_clean.osu"), "handoff", "--no-objects",
                        "--outdir", str(output)], cwd=temp, env=env, check=True, capture_output=True, text=True)
        env["OSUCOACH_HOME"] = str(temp / "workspace")
        command = [sys.executable, "-c", "from osucoach.cli import app; app()"]

        def run(*values):
            return subprocess.run(command + list(values), cwd=temp, env=env, check=True,
                                  capture_output=True, text=True).stdout

        run("init")
        sid = json.loads(run("session", "start", "--json"))["session_id"]
        result = json.loads(run("analysis", "import", str(output / "handoff_metrics.json"), "--session", sid,
                                "--source", "osu-critique", "--beatmap", "1"))
        reports = json.loads(run("history", "1"))["map_analyses"]
        shown = json.loads(run("analysis", "show", result["id"], "--session", sid))
        assert reports[0]["reports"][0]["id"] == result["id"]
        assert result["saved_score_map_match"] is None
        assert isinstance(shown["report"], dict) and "Untrusted" in shown["interpretation"]
        print("Synthetic analyzer report imported, found in history and retrieved; score match remains unknown.")


if __name__ == "__main__":
    main()
