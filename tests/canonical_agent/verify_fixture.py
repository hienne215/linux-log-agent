"""Synthetic systemctl output under the existing collector, parser and SDK.

The source label emulates the required production profile for validation only;
these fixtures do not observe Linux or prove remediation.
"""
import asyncio
from pathlib import Path
import sys

ROOT=Path(__file__).resolve().parents[2]
sys.path[:0]=[str(ROOT/'src'),str(ROOT)]
if len(sys.argv)>1 and sys.argv[1]=='worker':
    from src.mcp_server import service_status as service
    from agent import diagnostic_worker as worker
    from agent.diagnostics import read_gateway
    read_gateway.enabled=lambda:False  # Fixture IO only, even on an opted-in lab VM.
    fault=sys.argv[2]
    async def run(argv):
        if fault=='permission':return 1,b'',b'Permission denied synthetic-private-canary'
        if fault=='timeout':raise service.CollectionFailure('TIMEOUT')
        if fault=='oversized':raise service.CollectionFailure('OUTPUT_LIMIT')
        if fault=='malformed':return 0,b'Id=nginx.service\nMYSQL_PWD=synthetic-private-canary',b''
        if fault=='missing':return 1,b'Id=nginx.service\nLoadState=not-found\nActiveState=inactive\nSubState=dead\n',b''
        active,sub={'active':('active','running'),'inactive':('inactive','dead'),
                    'failed':('failed','failed'),'transition':('activating','start')}[fault]
        return 0,f'Id=nginx.service\nLoadState=loaded\nActiveState={active}\nSubState={sub}\n'.encode(),b''
    if fault=='child':
        pidfile=sys.argv[3]
        service.command=lambda unit:(sys.executable,'-B','-c',
            'import os,time;from pathlib import Path;Path('+repr(pidfile)+').write_text(str(os.getpid()));time.sleep(60)')
        service.TIMEOUT=60
        original=service.collect
        async def collect(a):return await original(a,execute=service.run)
    else:
        original=service.collect
        async def collect(a):return await original(a,execute=run)
    service.collect=collect
    sys.argv=[sys.argv[0],'verify_fix']
    raise SystemExit(worker.main())

from src.mcp_server.server import ServerScaffold
fault=sys.argv[1] if len(sys.argv)>1 else 'active'
extra=sys.argv[2:]
asyncio.run(ServerScaffold(backends={'verify_fix':(sys.executable,'-I',str(Path(__file__).resolve()),'worker',fault,*extra)}).serve())
