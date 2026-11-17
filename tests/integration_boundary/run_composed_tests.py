"""Read-only Git baseline + current Hiền modules, composed in a temporary tree.

Run using the prepared Python 3.12 venv. No install, checkout, network, inference
or production server. Unittest opt-in controls synthetic stdio as usual.
"""
import argparse
import io
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tarfile
import tempfile


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline-ref", default="origin/main")
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[2]
    commit = subprocess.check_output(["git", "rev-parse", "--verify", args.baseline_ref + "^{commit}"],
                                     cwd=root, text=True).strip()
    print("Read-only baseline commit:", commit, flush=True)
    archive = subprocess.check_output(["git", "archive", commit, "src/agent/__init__.py",
                                       "src/agent/llm", "tests/llm"], cwd=root)
    with tempfile.TemporaryDirectory(prefix="scrum22-composition-") as directory:
        overlay = Path(directory)
        with tarfile.open(fileobj=io.BytesIO(archive)) as bundle:
            bundle.extractall(overlay, filter="data")
        for package in ("mcp_client", "integration_boundary"):
            shutil.copytree(root / "src/agent" / package, overlay / "src/agent" / package,
                            ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
        env = dict(os.environ, PYTHONPATH=str(overlay / "src"), PYTHONDONTWRITEBYTECODE="1")
        for tests in (overlay / "tests/llm", root / "tests/mcp_client", root / "tests/integration_boundary"):
            print("Suite:", tests.name, flush=True)
            result = subprocess.run([sys.executable, "-m", "unittest", "discover", "-s", str(tests),
                                     "-p", "test_*.py", "-q"], cwd=root, env=env, check=False)
            if result.returncode:
                return result.returncode
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
