"""Render internal coordinator outcomes; no MCP contract fields or I/O.

The injected reviewed redactor is mandatory even for already-sanitized outcomes.
Rendering cannot verify provenance/diagnosis or turn citations into factual proof.
"""
import html
import re
from agent.coordinator import Report, Evidence

_ANSI = re.compile(r'\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)|\x1b\[[0-?]*[ -/]*[@-~]')
_CONTROL = re.compile(r'[\x00-\x08\x0b-\x1f\x7f-\x9f\u202a-\u202e\u2066-\u2069]')
_MARKDOWN = re.compile(r'([\\`*_{}\[\]()#+.!|>~:/-])')
_STATUSES = frozenset({'concluded','limited','budget_exhausted','cancelled',
                      'policy_denied','cleanup_pending','insufficient_evidence',
                      'invalid_evidence','invalid_arguments','invalid_decision',
                      'data_policy','output_limit','tool_error','tool_timeout',
                      'disconnected','repeated_call'})


class ReportError(Exception):
    def __init__(self, code):
        self.code=code
        super().__init__(code)


def render_markdown(report: Report, *, redact, max_bytes=65536) -> str:
    """Fail closed; never write artifacts, truncate references, or log payloads."""
    if type(report) is not Report or not callable(redact) or type(max_bytes) is not int or not 1024 <= max_bytes <= 65536:
        raise ReportError('INVALID_REPORT')

    def clean(value):
        if type(value) is not str or len(value.encode('utf-8')) > max_bytes:
            raise ReportError('OUTPUT_LIMIT')
        value=redact(value)
        if type(value) is not str or len(value.encode('utf-8')) > max_bytes:
            raise ReportError('DATA_POLICY')
        return value

    def escape(value):
        text=_CONTROL.sub('',_ANSI.sub('',value))
        return _MARKDOWN.sub(r'\\\1',html.escape(text,quote=True))

    try:
        if (type(report.status) is not str or report.status not in _STATUSES
                or type(report.evidence) is not tuple or type(report.citations) is not tuple
                or type(report.cleanup_pending) is not bool or type(report.conclusion_verified) is not bool
                or report.execution_state not in {'unknown','not_dispatched','response_received'}
                or type(report.tool_calls) is not int or not 0 <= report.tool_calls <= 8
                or type(report.llm_turns) is not int or not 0 <= report.llm_turns <= 6):
            raise ReportError('INVALID_REPORT')
        evidence={}
        for item in report.evidence:
            if (type(item) is not Evidence or type(item.id) is not str or not item.id
                    or len(item.id)>128 or item.id in evidence
                    or type(item.source) is not str or not item.source or len(item.source)>256):
                raise ReportError('INVALID_EVIDENCE')
            # IDs cannot be silently rewritten into broken citations.
            if (clean(item.id)!=item.id or _CONTROL.search(item.id)
                    or any(c in item.id for c in '\n\r')):
                raise ReportError('INVALID_EVIDENCE')
            evidence[item.id]=(clean(item.source),clean(item.text))
        if any(type(ref) is not str or ref not in evidence for ref in report.citations):
            raise ReportError('INVALID_CITATION')
        if report.status == 'concluded' and (not evidence or not report.citations):
            raise ReportError('INVALID_CITATION')
        summary=clean(report.conclusion)
        lines=['# Incident Report — read-only', '',
               'Báo cáo nội bộ; không xác nhận đã sửa lỗi hoặc thực hiện hành động ghi.', '',
               f'- Status: {report.status}', f'- Tool attempts: {report.tool_calls}',
               f'- LLM turns: {report.llm_turns}',
               f'- Execution outcome: {report.execution_state}',
               f'- Cleanup pending: {str(report.cleanup_pending).lower()}', '',
               '## Evidence / provenance', '']
        if not evidence:
            lines.append('Không có evidence đã được kiểm tra để trích dẫn.')
        for identifier,(source,text) in evidence.items():
            lines.extend([f'### {escape(identifier)}', 'Source (untrusted data):',
                          *('> '+line for line in escape(source).splitlines()), '',
                          *('> '+line for line in escape(text).splitlines()),''])
        lines.extend(['', '## Nhận định', '',
                      'Văn bản model chưa được xác minh về ngữ nghĩa.' if not report.conclusion_verified
                      else 'Nhận định do ứng dụng đánh dấu đã kiểm tra; renderer không tự xác minh.', ''])
        if report.status in {'concluded','limited'} and summary:
            # A limited report must not render an asserted cause/action from a model.
            if report.status=='limited':
                lines.append('Không đủ bằng chứng để kết luận; dữ liệu bị giới hạn.')
            else:
                lines.extend('> '+line for line in escape(summary).splitlines())
        else:
            lines.append('Không đưa ra kết luận chắc chắn từ trạng thái hiện tại.')
        lines.extend(['','## Citations','',', '.join(escape(ref) for ref in report.citations) or 'Không có citation.', '',
                      '## Limitations','',
                      '- Citation hợp lệ chỉ chứng minh ID tồn tại, không chứng minh kết luận đúng.',
                      '- Timeout/disconnect không chứng minh tool chưa thực thi hoặc đã rollback.',
                      '- Nguồn evidence và độ bao phủ redaction cần được kiểm chứng riêng.'])
        if report.cleanup_pending:
            lines.append('- Cleanup chưa được xác nhận. Giữ coordinator và gọi finish_cleanup để rejoin.')
        result='\n'.join(lines)+'\n'
        if len(result.encode('utf-8'))>max_bytes:
            raise ReportError('OUTPUT_LIMIT')
        return result
    except ReportError:
        raise
    except Exception:
        raise ReportError('DATA_POLICY') from None
