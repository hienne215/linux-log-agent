"""Run repository-contained SCRUM-24 tests, including with python -I.

No network, installation, environment mutation, external fixtures or inference.
"""
from pathlib import Path
import sys
import unittest


def main():
    root = Path(__file__).resolve().parents[2]
    sys.path.insert(0, str(root / 'src'))
    suite = unittest.TestSuite()
    for directory in ('agent_acceptance', 'agent_loop'):
        loader = unittest.TestLoader()
        suite.addTests(loader.discover(str(root / 'tests' / directory), pattern='test_*.py'))
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    return 0 if result.wasSuccessful() else 1


if __name__ == '__main__':
    raise SystemExit(main())
