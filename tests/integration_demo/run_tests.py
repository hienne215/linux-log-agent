"""Run merged-tree regressions and integration tests; no live inference."""
import os
from pathlib import Path
import subprocess
import sys

root = Path(__file__).resolve().parents[2]
env = dict(os.environ, PYTHONPATH=str(root/'src'), PYTHONDONTWRITEBYTECODE='1', RUN_MCP_STDIO_SMOKE='1')
if __name__ == '__main__':
    for suite in ('llm', 'mcp_client', 'integration_boundary', 'agent_acceptance',
                  'agent_loop', 'cli', 'async_llm', 'integration_demo'):
        print('Suite:', suite, flush=True)
        result = subprocess.run([sys.executable, '-B', '-m', 'unittest', 'discover',
            '-s', str(root/'tests'/suite), '-p', 'test_*.py', '-v'], cwd=root, env=env)
        if result.returncode:
            raise SystemExit(result.returncode)
