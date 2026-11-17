"""Test-only exact-revision audit runner. No Linux reader or MCP dispatch.

Requires the three referenced Git commits locally. Git is used only to read
trusted, pinned source/test/schema blobs, never to alter refs or the worktree.
"""
from functools import lru_cache
from pathlib import Path
import subprocess
import sys
import tempfile
from types import ModuleType

ROOT = Path(__file__).resolve().parents[1]
PARSER_SHA = "dd110951821330c3612cb70d321e597ac07d5304"
MATCHER_SHA = "c36c6fa8d624918f1ef1addaf5711f2eb795745c"
CONTRACT_SHA = "dc53ff6c3afd038f66e987bf1c7f7ad17f4b7829"


def read_revision(sha, path):
    return subprocess.run(
        ["git", "show", f"{sha}:{path}"], cwd=ROOT, shell=False,
        check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=10,
    ).stdout


@lru_cache(maxsize=1)
def load_cores():
    modules = []
    for sha, name in ((PARSER_SHA, "journal_parser"),
                      (MATCHER_SHA, "log_search_matcher")):
        path = f"src/mcp_server/{name}.py"
        module = ModuleType(f"offline_audit_{name}")
        # Execution is only of the fixed, reviewed source revisions above.
        exec(compile(read_revision(sha, path), f"{sha}:{path}", "exec"), module.__dict__)
        modules.append(module)
    return tuple(modules)


def main():
    # Import using the same module identity as the integration tests.
    sys.path.insert(0, str(ROOT))
    from scripts.p01_02_offline_audit import load_cores as pinned_cores
    import pytest

    parser, matcher = pinned_cores()
    sys.modules["src.mcp_server.journal_parser"] = parser
    sys.modules["src.mcp_server.log_search_matcher"] = matcher
    with tempfile.TemporaryDirectory(prefix="p01-02-offline-") as directory:
        snapshot = Path(directory)
        files = (
            (PARSER_SHA, "tests/test_journal_parser.py"),
            (MATCHER_SHA, "tests/test_log_search_matcher.py"),
            (CONTRACT_SHA, "schemas/tools/query_journal.response.schema.json"),
            (CONTRACT_SHA, "schemas/tools/search_log.response.schema.json"),
        )
        for sha, path in files:
            target = snapshot / path
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(read_revision(sha, path))
        return pytest.main([
            "-q", str(snapshot / "tests/test_journal_parser.py"),
            str(snapshot / "tests/test_log_search_matcher.py"),
            str(ROOT / "tests/test_p01_02_offline_composition.py"),
            f"--basetemp={snapshot / 'pytest-temp'}",
        ])


if __name__ == "__main__":
    raise SystemExit(main())
