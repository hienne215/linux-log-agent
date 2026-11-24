"""Synthetic HTTP/systemctl/journal IO beneath real health-check logic/SDK."""
import asyncio,json
from pathlib import Path
from datetime import timedelta
import sys
ROOT=Path(__file__).resolve().parents[2];sys.path[:0]=[str(ROOT/'src'),str(ROOT)]
if len(sys.argv)>1 and sys.argv[1]=='worker':
    from agent import diagnostic_worker as worker
    from agent.diagnostics import read_gateway
    read_gateway.enabled=lambda:False  # Fixture IO only, even on an opted-in lab VM.
    from agent.diagnostics import health_verification as h, journal_reader as journal
    from agent.diagnostics.journal_policy import parse_stamp
    from src.mcp_server import service_status as service
    fault=sys.argv[2]
    original=service.collect
    async def status_run(argv):
        if fault=='permission':return 1,b'',b'Permission denied fixture-private-canary'
        state='failed' if fault=='service_failed' else 'active'
        sub='failed' if state=='failed' else 'running'
        return 0,f'Id=nginx.service\nLoadState=loaded\nActiveState={state}\nSubState={sub}\n'.encode(),b''
    async def collect(a):return await original(a,execute=status_run)
    service.collect=collect
    class Reader:
        async def readuntil(self,separator):
            if fault=='http_timeout':raise TimeoutError()
            if fault=='http_malformed':return b'private-canary not-http\r\n\r\n'
            status=500 if fault=='http_500' else 200
            return f'HTTP/1.1 {status} Fixture\r\nSet-Cookie: secret=fixture-private-canary\r\n\r\n'.encode()
    class Writer:
        def write(self,data):
            assert data==b'HEAD / HTTP/1.1\r\nHost: localhost\r\nConnection: close\r\n\r\n'
        async def drain(self):pass
        def close(self):pass
        async def wait_closed(self):pass
    async def connection(host,port,**kwargs):
        assert (host,port)==('127.0.0.1',80)
        return Reader(),Writer()
    h.asyncio.open_connection=connection
    async def journal_run(a,**kwargs):
        if fault=='permission':return 1,b'',b'Permission denied fixture-private-canary'
        if fault not in ('old_log','new_log'):return 0,b'',b''
        at=parse_stamp(a['since'])-timedelta(seconds=1) if fault=='old_log' else parse_stamp(a['until'])-timedelta(seconds=1)
        row=dict(__REALTIME_TIMESTAMP=str(int(at.timestamp()*1000000)),PRIORITY='3',_SYSTEMD_UNIT='nginx.service',MESSAGE='error MYSQL_PWD=fixture-private-canary')
        return 0,(json.dumps(row)+'\n').encode(),b''
    journal._run=journal_run
    sys.argv=[sys.argv[0],'verify_fix'];raise SystemExit(worker.main())
from src.mcp_server.server import ServerScaffold
fault=sys.argv[1] if len(sys.argv)>1 else 'active'
asyncio.run(ServerScaffold(backends={'verify_fix':(sys.executable,'-I',str(Path(__file__).resolve()),'worker',fault)}).serve())
