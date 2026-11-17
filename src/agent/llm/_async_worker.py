"""Private one-request worker. stdout contains bounded JSON only; no diagnostics."""
from dataclasses import asdict
import json
import sys

from .async_adapter import MAX_IPC, SAFE_CODES
from .interface import AdapterError, Message, ToolCall, ToolDefinition
from .ollama import OllamaAdapter, OllamaConfig, _pairs


def serve(transport=None):
    try:
        raw = sys.stdin.buffer.read(MAX_IPC + 1)
        if len(raw) > MAX_IPC:
            raise AdapterError('INPUT_LIMIT')
        request = json.loads(raw, object_pairs_hook=_pairs)
        if set(request) != {'config', 'messages', 'tools', 'require_tool'}:
            raise AdapterError('INVALID_INPUT')
        messages = []
        for item in request['messages']:
            item = dict(item)
            item['tool_calls'] = tuple(ToolCall(**c) for c in item['tool_calls'])
            messages.append(Message(**item))
        tools = tuple(ToolDefinition(**t) for t in request['tools'])
        adapter = OllamaAdapter(OllamaConfig(**request['config']), transport=transport)
        completion = adapter.complete(tuple(messages), tools, require_tool=request['require_tool'])
        packet = {'ok': True, 'completion': asdict(completion)}
    except AdapterError as error:
        packet = {'ok': False, 'code': error.code if error.code in SAFE_CODES else 'WORKER_FAILURE'}
    except Exception:
        packet = {'ok': False, 'code': 'WORKER_FAILURE'}
    try:
        encoded = json.dumps(packet, ensure_ascii=False, allow_nan=False).encode()
        if len(encoded) > MAX_IPC:
            encoded = b'{"ok": false, "code": "OUTPUT_LIMIT"}'
        sys.stdout.buffer.write(encoded)
        sys.stdout.buffer.flush()
    except Exception:
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(serve())
