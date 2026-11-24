"""Expanded capability acceptance, not renamed narrow test fixtures."""
import json,os,tempfile,unittest
from datetime import datetime,timezone,timedelta
from pathlib import Path
import sys
from unittest.mock import AsyncMock,patch
from zoneinfo import ZoneInfo
from agent import diagnostic_worker as worker
from agent.diagnostics import system_diagnostics as sources
from agent.diagnostics.journal_policy import window,parse_stamp
from agent.service_binding import ServiceBinding,DiagnosticBinding
from agent.llm.redaction import _clean
from agent.mcp_client import StdioMCPClient,StdioConfig,MCPClientError
from src.mcp_server import service_status as service
ROOT=Path(__file__).resolve().parents[2]


def request(**extra):
    return dict(schema_version='1.0.0',session_id='synthetic-session',trace_id='trace',logical_tool_call_id='call',attempt_id='attempt',**extra)


class ExpandedTests(unittest.IsolatedAsyncioTestCase):
    async def test_inode_exhaustion_not_confused_with_available_disk_bytes(self):
        from types import SimpleNamespace
        from src.mcp_server import disk_usage,disk_usage_worker
        from agent.service_binding import inode_snapshot
        stats=SimpleNamespace(f_blocks=100,f_bfree=90,f_bavail=80,f_frsize=4096,f_bsize=4096,f_files=10,f_ffree=0,f_favail=0)
        with patch.object(disk_usage.os,'statvfs',return_value=stats):
            r=disk_usage_worker.collect_response(request(filesystem='root',include_inodes=True))
        self.assertEqual(r['status'],'ok');self.assertEqual(r['data']['used_percent'],10)
        self.assertEqual(inode_snapshot(r),dict(total=10,free=0,available=0,used_percent=100))

    async def test_ports_ssh_udp_and_unfiltered_are_real_supported_inputs(self):
        for filters,row in [({'protocol':'tcp','port':22},('tcp',22)),({'protocol':'udp','port':53},('udp',53)),({},('tcp',443))]:
            data=dict(ports=[dict(protocol=row[0],port=row[1],address='127.0.0.1',process='',unit=None)],count=1)
            with patch.object(worker,'collect_listening_ports',AsyncMock(return_value=data)) as collect:
                result=await worker.collect_response('listening_ports',request(**filters))
            self.assertEqual(result['status'],'ok');self.assertEqual(result['data']['ports'][0]['port'],row[1])
            collect.assert_awaited_once_with(protocol=filters.get('protocol'),port=filters.get('port'))
    async def test_service_other_allowlisted_units_and_missing_not_failed(self):
        for unit in ('ssh.service','cron.service'):
            async def execute(argv):
                self.assertEqual(argv[-1],unit);self.assertEqual(argv[0],'/usr/bin/systemctl')
                return 0,f'Id={unit}\nLoadState=loaded\nActiveState=inactive\nSubState=dead\n'.encode(),b''
            e=await service.collect(request(unit=unit),execute=execute,platform_name='linux')
            self.assertEqual(e['status'],'ok');self.assertEqual(e['data']['unit'],unit)
            self.assertEqual(e['data']['active_state'],'inactive');self.assertEqual(e['evidence'][0]['unit'],unit)
    async def test_sdk_journal_debug_level_and_one_hour_window(self):
        fixture=ROOT/'tests/canonical_agent/journal_fixture.py'
        c=StdioMCPClient(StdioConfig(sys.executable,('-B',str(fixture),'info'),frozenset({'query_journal'})))
        await c.connect();await c.list_tools();b=DiagnosticBinding(c,ROOT,redact=_clean,tool='query_journal')
        try:
            e=(await b.call_tool('query_journal',dict(unit='nginx.service',priority=7,lookback_minutes=60))).structured_content
            self.assertEqual(e['data']['entries'][0]['priority'],7)
            self.assertEqual((parse_stamp(b.window['until'])-parse_stamp(b.window['since'])).total_seconds(),3600)
        finally:await c.disconnect()
        self.assertIsNone(c._owner)
    async def test_sdk_arbitrary_literal_query_not_regex_or_four_preselected_answers(self):
        fixture=ROOT/'tests/canonical_agent/journal_fixture.py'
        c=StdioMCPClient(StdioConfig(sys.executable,('-B',str(fixture),'upstream'),frozenset({'search_log'})))
        await c.connect();await c.list_tools();b=DiagnosticBinding(c,ROOT,redact=_clean,tool='search_log')
        try:
            e=(await b.call_tool('search_log',dict(source_id='nginx_journal',query='upstream timed out'))).structured_content
            self.assertEqual(e['data']['count'],1);self.assertEqual(e['data']['query'],'upstream timed out')
            self.assertNotIn('connecting to backend',json.dumps(e))
        finally:await c.disconnect()
    async def test_secret_query_and_bad_units_rejected_before_transport(self):
        for tool,args in [('search_log',dict(source_id='nginx_journal',query='MYSQL_PWD=synthetic-private-canary')),
                          ('listening_ports',dict(port=65536)),('service_status',dict(unit='unapproved.service'))]:
            c=AsyncMock();b=ServiceBinding(c,ROOT,redact=_clean) if tool=='service_status' else DiagnosticBinding(c,ROOT,redact=_clean,tool=tool)
            with self.assertRaises(MCPClientError):await b.call_tool(tool,args)
            c.call_tool.assert_not_awaited()


class FileTests(unittest.TestCase):
    def test_fixed_file_reader_no_follow_and_partial_record_fail_closed(self):
        with tempfile.TemporaryDirectory(prefix='fixed-source-') as name:
            folder=Path(name);good=folder/'good';good.write_text('synthetic line\n')
            link=folder/'link';link.symlink_to(good)
            self.assertEqual(sources.fixed_lines((str(good),)),['synthetic line'])
            with self.assertRaises(OSError):sources.fixed_lines((str(link),))
            good.write_text('incomplete')
            with self.assertRaises(sources.SourceError):sources.fixed_lines((str(good),))
    def test_nginx_current_rotated_file_dedup_and_literal_matching(self):
        a=dict(since='2026-11-19T00:00:00Z',until='2026-11-19T00:15:00Z',query='upstream timed out')
        line='2026/11/19 00:01:00 [error] 123#123: upstream timed out; MYSQL_PWD=synthetic-private-canary 192.0.2.23'
        with patch.object(sources,'fixed_lines',return_value=[line,line]),patch.object(sources,'local_zone',return_value=timezone.utc):
            result=sources.nginx_file(a)
        self.assertEqual(len(result),1);self.assertNotIn('synthetic-private-canary',json.dumps(result))
        self.assertNotIn('192.0.2.23',json.dumps(result))
    def test_auth_rotation_year_and_trusted_timezone_without_invented_utc(self):
        a=dict(since='2026-12-31T23:59:00Z',until='2027-01-01T00:01:00Z')
        lines=['Dec 31 23:59:30 lab sshd[1]: Failed password for root from 192.0.2.23 port 4 ssh2',
               'Jan  1 00:00:30 lab sshd[2]: Failed password for root from 192.0.2.23 port 4 ssh2']
        with patch.object(sources,'fixed_lines',return_value=lines+lines),patch.object(sources,'local_zone',return_value=timezone.utc):
            result=sources.auth_files(a)
        self.assertEqual(len(result),2);self.assertTrue(result[0][0].startswith('2026-12-31'));self.assertTrue(result[1][0].startswith('2027-01-01'))
        zone=ZoneInfo('Asia/Ho_Chi_Minh')
        at=sources.utc_candidate('2026/11/19 07:01:00','%Y/%m/%d %H:%M:%S',parse_stamp('2026-11-19T00:00:00Z'),parse_stamp('2026-11-19T00:15:00Z'),zone)
        self.assertEqual(at.hour,0)
