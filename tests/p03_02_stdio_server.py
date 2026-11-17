"""Synthetic OOM SDK launcher only; never a production or fault-generation flag."""
import asyncio
import sys
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from src.mcp_server.oom_server import OomServer
from test_p03_02_pagination import response

if __name__=='__main__':
    asyncio.run(OomServer('s',acquire=lambda req:response(req,450),access_check=lambda:True).serve())
