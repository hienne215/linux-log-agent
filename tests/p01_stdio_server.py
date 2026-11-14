"""Official SDK fault server for P01 tests; no production fault flags."""
import asyncio
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from src.mcp_server.runner import WorkerRunner
from src.mcp_server.server import ServerScaffold

tool = "query_journal" if sys.argv[1] == "partial" else "service_status"
profile = (sys.executable, "-I", str(ROOT / "tests/p01_worker_fixture.py"), sys.argv[1])
asyncio.run(ServerScaffold(backends={tool: profile}, runner=WorkerRunner(timeout=0.3 if sys.argv[1] == "sleep" else 10)).serve())
