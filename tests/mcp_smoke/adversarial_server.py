"""Fault fixtures for SDK integration only; production server has no test switches."""
import asyncio
import copy
import os
import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[2]))
import mock_mcp_server as server

mode=sys.argv[1]
if mode in {'coverage-denied', 'coverage-partial', 'coverage-unavailable'}:
    def fixture_coverage(name):
        states = {'coverage-denied':['permission_denied'],
                  'coverage-unavailable':['unavailable'],
                  'coverage-partial':['ok','permission_denied']}[mode]
        return [dict(source=server.LOG_SOURCES[name][i], status=state,
                     oldest_available_at=None) for i,state in enumerate(states)]
    server.log_coverage=fixture_coverage
async def injected(ctx,params):
    if mode=='slow' and params.name=='service_status': await asyncio.sleep(10)
    if mode=='disconnect' and params.name=='service_status': os._exit(0)
    return await server.call_tool(ctx,params)

if mode=='expired': server.CURSOR_TTL_SECONDS=0.01
if mode=='partial':
    record=server.TOOLS['query_journal'];original=record[0]
    def many_records(**kwargs):
        response=original(**kwargs)
        response['data']['entries']=[{'timestamp':'2026-11-02T09:30:00Z','priority':3,'message':'whole-record-'+'漢'*300} for _ in range(100)]
        response['data']['count']=100
        return response
    server.TOOLS['query_journal']=(many_records,*record[1:])
if mode in {'huge','unicode','nested','near'}:
    record=server.TOOLS['service_status'];original=record[0]
    def oversized(**kwargs):
        response=original(**kwargs)
        if mode=='nested': response['evidence'][0]['excerpt']='漢🙂'*30000
        else: response['data']['description']=('漢🙂'*20000 if mode=='unicode' else 'A'*(31000 if mode=='near' else 70000))
        if mode=='near':
            # Reach the transport boundary using schema-valid field sizes.
            # One 31 KiB description no longer satisfies the 2048-character cap.
            response['data']['description']='A'*2048
            template=response['evidence'][0]
            response['evidence']=[dict(copy.deepcopy(template), evidence_id=f'near-{i:02}') for i in range(16)]
            def distribute(size):
                for i,item in enumerate(response['evidence']):
                    item['excerpt']='A'*min(2048,max(0,size-i*2048))
            lo,hi=0,16*2048
            while lo<hi:
                mid=(lo+hi+1)//2
                distribute(mid)
                if server.wire_size(server.native_result(response)) <= 65520: lo=mid
                else: hi=mid-1
            distribute(lo)
        return response
    server.TOOLS['service_status']=(oversized,*record[1:])
server.mcp=server.Server('audit-fault-fixture',on_list_tools=server.list_tools,on_call_tool=injected)
server.mcp.middleware.append(server.protocol_guard)
asyncio.run(server.run())
