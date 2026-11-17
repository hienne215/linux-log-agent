"""P03-02 opt-in binding/lifetime regressions. Synthetic source only."""
import asyncio
import json
import sys
import threading
from pathlib import Path
from types import SimpleNamespace

import pytest
from mcp import Client, StdioServerParameters
from mcp.types import CallToolRequestParams

import mock_mcp_server as contracts
from src.mcp_server.oom_server import OomServer
from src.mcp_server.oom_pagination import TOOL
from test_p03_02_oom_events import REQ
from test_p03_02_pagination import response

ROOT=Path(__file__).resolve().parents[1]


def call(app,req):
    return app.call_tool(SimpleNamespace(request_id=1),CallToolRequestParams(name=TOOL,arguments=req))


def test_persistent_binding_acquires_once_across_calls_and_closes():
    async def exercise():
        acquired=[]
        def source(req):
            acquired.append(1)
            return response(req,5)
        app=OomServer('s',acquire=source,access_check=lambda:True)
        first=(await call(app,{**REQ,'limit':1})).structured_content
        second=(await call(app,{**REQ,'limit':1,'cursor':first['next_cursor']})).structured_content
        assert len(acquired)==1 and second['data']['events'][0]['pid']==2
        assert await app.close() and app.pending is None and not app.pager._tokens
    asyncio.run(exercise())


def test_cancellation_retains_acquisition_and_refuses_new_work_until_closed():
    async def exercise():
        entered,release=threading.Event(),threading.Event()
        def source(req):
            entered.set()
            release.wait(5)
            return response(req)
        app=OomServer('s',acquire=source,access_check=lambda:True)
        task=asyncio.create_task(call(app,{**REQ,'limit':1}))
        try:
            assert await asyncio.to_thread(entered.wait,2)
            task.cancel()
            with pytest.raises(asyncio.CancelledError): await task
            assert app.closing and app.pending is not None and not app.pending.done()
            refused=(await call(app,REQ)).structured_content
            assert refused['error_code']=='POLICY_DENIED'
            cleanup=asyncio.create_task(app.close())
            await asyncio.sleep(0)
            assert not cleanup.done()
            release.set()
            assert await asyncio.wait_for(cleanup,5)
            assert app.pending is None and not app.pager._tokens
        finally:
            release.set()
            await app.close()
    asyncio.run(exercise())


def test_session_binding_and_invalid_input_never_acquire():
    def never(req): pytest.fail('Invalid request reached source')
    async def exercise():
        app=OomServer('operator-session',acquire=never,access_check=lambda:True)
        result=(await call(app,REQ)).structured_content
        assert result['error_code']=='INVALID_INPUT'
        assert app.pending is None
        await app.close()
    asyncio.run(exercise())


def test_official_sdk_pages_all_events_with_consistent_native_payloads():
    async def exercise():
        params=StdioServerParameters(command=sys.executable,args=[str(ROOT/'tests/p03_02_stdio_server.py')])
        async with Client(params,read_timeout_seconds=30) as client:
            tools=await client.list_tools()
            assert len(tools.tools)==12
            req={**REQ,'limit':200}
            ids=[]
            while True:
                result=await client.call_tool(TOOL,req)
                body=result.structured_content
                contracts.TOOLS[TOOL][4].validate(body)
                assert not result.is_error and json.loads(result.content[0].text)==body
                assert body['data']['count']<=200
                ids.extend(e['evidence_id'] for e in body['data']['events'])
                if body['next_cursor'] is None: break
                req={**req,'cursor':body['next_cursor']}
            assert len(ids)==len(set(ids))==450
            bad=await client.call_tool(TOOL,{**REQ,'cursor':'forged'})
            assert bad.is_error and bad.structured_content['error_code']=='INVALID_INPUT'
    asyncio.run(exercise())
