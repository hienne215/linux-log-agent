"""Compose exact unmerged synthetic dependencies without modifying worktrees."""
import argparse
import io
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tarfile
import tempfile

DEPENDENCY = 'ab05df010a5dc95faf2adbfa6ee997387d2dbc1e'


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--suite', choices=('async_llm', 'all'), default='all')
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[2]
    archive = subprocess.check_output(['git', 'archive', DEPENDENCY, 'src/agent', 'tests'], cwd=root)
    with tempfile.TemporaryDirectory(prefix='async-llm-composition-') as directory:
        tree = Path(directory)
        with tarfile.open(fileobj=io.BytesIO(archive)) as bundle:
            bundle.extractall(tree, filter='data')
        for source, target in ((root/'src/agent/llm', tree/'src/agent/llm'),
                               (root/'tests/llm', tree/'tests/llm'),
                               (root/'tests/async_llm', tree/'tests/async_llm')):
            shutil.copytree(source, target, ignore=shutil.ignore_patterns('__pycache__', '*.pyc'))
        env = dict(os.environ, PYTHONPATH=str(tree/'src'), PYTHONDONTWRITEBYTECODE='1')
        suites = ('async_llm',) if args.suite == 'async_llm' else (
            'llm','mcp_client','integration_boundary','agent_acceptance','agent_loop','cli','async_llm')
        for suite in suites:
            print('Suite:', suite, flush=True)
            result = subprocess.run([sys.executable, '-B', '-m', 'unittest', 'discover',
                '-s', str(tree/'tests'/suite), '-p', 'test_*.py', '-q'], cwd=tree, env=env)
            if result.returncode:
                return result.returncode
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
