"""Explicit synthetic disk worker/SDK fixture; never calls real statvfs."""
import asyncio
from pathlib import Path
import sys
import time
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from src.mcp_server import disk_usage as disk
from src.mcp_server import disk_usage_worker as worker
from src.mcp_server.runner import WorkerRunner
from src.mcp_server.server import ServerScaffold


def main():
    mode = sys.argv[-1]
    if mode not in {"ok", "denied", "missing", "unsupported", "slow", "flood"}:
        return 2
    if "--worker" not in sys.argv:
        backend = (sys.executable, "-I", str(Path(__file__).resolve()), "--worker", mode)
        asyncio.run(ServerScaffold(backends={"disk_usage": backend},
                    runner=WorkerRunner(timeout=5)).serve())
        return 0
    if mode == "slow":
        time.sleep(60)
        return 2
    if mode == "flood":
        sys.stdout.buffer.write(b"x" * 65537)
        sys.stdout.buffer.flush()
        return 0
    def statvfs(path):
        assert path == "/"
        if mode == "denied":
            raise PermissionError("PRIVATE_MARKER /private")
        if mode == "missing":
            raise FileNotFoundError("PRIVATE_MARKER /private")
        if mode == "unsupported":
            raise NotImplementedError("PRIVATE_MARKER")
        return SimpleNamespace(f_blocks=100, f_bfree=30, f_bavail=20,
            f_frsize=4096, f_bsize=8192, f_files=50, f_ffree=20, f_favail=10)
    disk.os.statvfs = statvfs
    worker.sys.platform = "linux"  # Only this disposable test process.
    worker.SOURCE = "synthetic.disk_usage.statvfs.test.v1"
    return worker.main()


if __name__ == "__main__":
    raise SystemExit(main())
