"""Build and smoke-test a wheel outside the checkout (requires pip build dependencies)."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile


def main():
    root = Path(__file__).resolve().parents[1]
    with tempfile.TemporaryDirectory() as directory:
        temp = Path(directory)
        source = temp / "source"
        source.mkdir()
        shutil.copy(root / "pyproject.toml", source)
        shutil.copytree(root / "src", source / "src", ignore=shutil.ignore_patterns("__pycache__", "*.egg-info"))
        wheels = temp / "wheels"
        wheels.mkdir()
        subprocess.run([sys.executable, "-m", "pip", "wheel", "--no-deps", "-w", str(wheels), str(source)], check=True)
        installed = temp / "installed"
        subprocess.run([sys.executable, "-m", "pip", "install", "--no-deps", "--target", str(installed),
                        str(next(wheels.glob("*.whl")))], check=True)
        env = {**os.environ, "PYTHONPATH": str(installed), "OSUCOACH_HOME": str(temp / "workspace")}
        module_path = subprocess.run([sys.executable, "-c", "import osucoach; print(osucoach.__file__)"],
                                     cwd=temp, env=env, check=True, capture_output=True, text=True).stdout.strip()
        assert Path(module_path).resolve().is_relative_to(installed.resolve())
        command = [sys.executable, "-c", "from osucoach.cli import app; app()"]

        def run(*args):
            return subprocess.run(command + list(args), cwd=temp, env=env, check=True,
                                  capture_output=True, text=True).stdout

        run("init")
        assert "Always load first" in run("guide")
        assert "data contract" in run("schema").lower()
        context = json.loads(run("context"))
        assert context["workspace"] == str(temp / "workspace") and context["player"]
        print("Installed wheel: init, guide, schema, and workspace context passed.")


if __name__ == "__main__":
    main()
