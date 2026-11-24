"""Synthetic disk API behind unchanged canonical worker; NEVER a Linux observation."""
import asyncio
import json
from pathlib import Path
import sys
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
if sys.argv[1:] == ['worker']:
    from src.mcp_server import disk_usage_worker as worker
    def disk(*args,_inode_details=None):
        if _inode_details is not None:_inode_details.update(total=100,free=20,available=10)
        return dict(filesystem='root',mount_point='/',used_percent=37.5,available_bytes=8*1024**3,inodes_included=True)
    worker.collect_disk_usage = disk
    raise SystemExit(worker.main())
from src.mcp_server.server import ServerScaffold
asyncio.run(ServerScaffold(backends={'disk_usage':(
    sys.executable, '-I', str(Path(__file__).resolve()), 'worker')}).serve())
