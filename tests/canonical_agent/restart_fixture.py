"""SDK-only synthetic proposal peer. No real socket, systemd or approval."""
import asyncio
from pathlib import Path
import sys
ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT/'src'));sys.path.insert(0,str(ROOT))
from agent import proposal_server
import mock_mcp_server as contracts


async def exchange(r):
    if r['op']=='propose':
        return dict(action_id='b'*32,session_id=r['session_id'],trace_id=r['trace_id'],
            environment_id='synthetic-lab',unit=r['unit'],evidence_ids=r['evidence_ids'],state='pending',
            expires_at='2099-01-01T00:00:00.000000Z',remaining_ms=60000,execution_enabled=False,executed=None)
    raise ValueError('Synthetic fixture supports proposal only')


proposal_server.exchange=exchange
asyncio.run(proposal_server.ProposalServer().serve())
