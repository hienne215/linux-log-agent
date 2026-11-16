"""Trusted synthetic worker; HTTPTransport is replaced, no network/inference."""
import json
import os
from pathlib import Path
import signal
import sys
import time

from agent.llm._async_worker import serve
from agent.llm.interface import AdapterError


class MockHTTP:
    def __init__(self, mode):
        self.mode = mode

    def post(self, url, body, timeout, limit):
        request = json.loads(body)
        if self.mode == 'assert-redacted' and (b'synthetic-secret' in body or 'LLA_TEST_SECRET' in os.environ):
            raise AdapterError('DATA_POLICY')
        if self.mode == 'timeout':
            raise AdapterError('TIMEOUT')
        if self.mode == 'disconnect':
            raise AdapterError('CONNECTION_ERROR')
        if self.mode == 'missing-model':
            raise AdapterError('MODEL_NOT_FOUND')
        if self.mode == 'malformed':
            return b'{broken'
        if self.mode in ('late', 'stubborn'):
            if self.mode == 'stubborn':
                signal.signal(signal.SIGTERM, signal.SIG_IGN)
            if len(sys.argv) > 2:
                Path(sys.argv[2]).write_text('ready')
            time.sleep(5)
        tool = 'service_status'
        args = {'unit': 'nginx.service'}
        if self.mode == 'write':
            tool = 'restart_service'
        if self.mode == 'bad-args':
            args = {'unit': 'ssh.service'}
        message = {'role': 'assistant', 'content': '', 'tool_calls': [
            {'function': {'name': tool, 'arguments': args}}]}
        if self.mode == 'conclude' or (self.mode == 'auto' and 'e1' in request['messages'][-1]['content']):
            message = {'role': 'assistant', 'content': json.dumps(
                {'conclusion': 'nginx.service active; chưa kiểm tra HTTP', 'citations': ['e1']})}
        if self.mode == 'forged-citation':
            message = {'role': 'assistant', 'content': '{"conclusion":"fixed","citations":["invented"]}'}
        if self.mode == 'text-tool':
            message = {'role': 'assistant', 'content': '{"tool":"restart_service","arguments":{}}'}
        return json.dumps({'model': request['model'], 'done': True, 'done_reason': 'stop',
                           'message': message, 'prompt_eval_count': 20, 'eval_count': 12,
                           'eval_duration': 100000000, 'total_duration': 200000000}).encode()


if __name__ == '__main__':
    if sys.argv[1] == 'flood':
        sys.stdout.buffer.write(b'x' * (1024*1024 + 1))
        sys.stdout.buffer.flush()
    elif sys.argv[1] == 'crash':
        print('password=synthetic-secret', file=sys.stderr)
        raise SystemExit(2)
    else:
        raise SystemExit(serve(MockHTTP(sys.argv[1])))
