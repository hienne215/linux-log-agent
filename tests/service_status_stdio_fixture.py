"""Test-only Linux API simulation behind the real SDK/server/worker boundary."""
import asyncio
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'src'), str(ROOT)]
from src.mcp_server import service_status as collector
from src.mcp_server.server import ServerScaffold

worker_mode = len(sys.argv) > 1 and sys.argv[1] == 'worker'
fault = (sys.argv[2] if len(sys.argv)>2 else 'normal') if worker_mode else (sys.argv[1] if len(sys.argv)>1 else 'normal')
async def execute(argv):
    return 0, b'Id=nginx.service\nLoadState=loaded\nActiveState=active\nSubState=running\n', b''


if worker_mode:
    async def main():
        request = json.loads(sys.stdin.buffer.read(16385))
        collector.command = lambda unit: ('/usr/bin/systemctl', 'show', '--', unit)
        response = await collector.collect(request, execute=execute, platform_name='linux')
        if fault == 'state_mismatch':
            # Deliberately schema-valid, semantically inconsistent peer payload.
            response['data'].update(active_state='failed',sub_state='failed')
        sys.stdout.buffer.write(json.dumps(response).encode('utf-8'))
    asyncio.run(main())
else:
    asyncio.run(ServerScaffold(backends={'service_status':(
        sys.executable, '-I', str(Path(__file__).resolve()), 'worker', fault)}).serve())
