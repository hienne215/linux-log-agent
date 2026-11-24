"""Application incident artifact: existing renderer plus validated canonical ledger.

No wire fields, inferred execution, approval or fabricated correlation/evidence.
"""
import json
from agent.incident_report import render_markdown
from agent.cli.report_store import StoreError
from agent.llm.redaction import _clean


def render_session_report(report, results, label):
    try:
        import mock_mcp_server as contracts
        if type(results) is not dict or len(results)>12:
            raise ValueError()
        encoded=json.dumps(results,ensure_ascii=False,indent=2,allow_nan=False)
        if len(encoded.encode())>65536 or _clean(encoded)!=encoded:
            raise ValueError()
        rows=[]
        for name,result in results.items():
            if name not in contracts.TOOLS or not contracts.TOOLS[name][4].is_valid(result) or result['tool']!=name:
                raise ValueError()
            placeholder=(result['status']=='error' and result['session_id'] is None
                and all(result[k]=='invalid-input' for k in ('trace_id','logical_tool_call_id','attempt_id')))
            if report.session is not None and not placeholder and (
                    result['session_id']!=report.session.session_id or result['trace_id']!=report.session.trace_id):
                raise ValueError()
            # Fixed-schema fields only; raw diagnostics never become table markup.
            for key in ('logical_tool_call_id','attempt_id'):
                if not result[key].isprintable() or any(c in result[key] for c in '|`<>'):
                    raise ValueError()
            rows.append(f"| {name} | {result['status']} | {result['duration_ms']} | {result['logical_tool_call_id']} | {result['attempt_id']} |")
        text=render_markdown(report,redact=_clean)
        if rows:
            text+='\n## Received canonical tool responses\n\n'
            text+='Only responses received and validated locally are listed. Missing response is not proof of nonexecution.\n\n'
            text+='| Tool | Status | Duration (ms) | Logical call | Attempt |\n|---|---|---:|---|---|\n'+'\n'.join(rows)+'\n'
        text+='\n## Full canonical ledger (validated, untrusted tool data)\n\n```json\n'+encoded+'\n```\n'
        # Label is application configuration, not prompt/model/tool text.
        if label not in ('LIVE OLLAMA + REAL LINUX MCP','MOCK LLM — scripted, không hiểu câu hỏi + REAL LINUX MCP',
                          'NO MODEL — canonical status read + REAL LINUX MCP'):
            raise ValueError()
        text='# '+label+'\n\n'+text
        if len(text.encode())>70000:raise ValueError()
        return text
    except Exception:
        raise StoreError('UNSAFE_SESSION_REPORT') from None
