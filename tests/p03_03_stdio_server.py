"""Synthetic source fixture for SDK checks; not a production opt-in switch."""
import asyncio
import sys
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from test_p03_03_failed_logins import PRIVACY, snapshot
from src.mcp_server.failed_logins_owner import FailedLoginsOwner
from src.mcp_server.failed_logins_server import FailedLoginsServer

if __name__=='__main__':
    def provider(_privacy):
        return lambda *_:snapshot(450)
    owner=FailedLoginsOwner(('fixture-uid',129),'session-test',PRIVACY,provider,access_check=lambda:True)
    asyncio.run(FailedLoginsServer(owner,('fixture-uid',129)).serve())
