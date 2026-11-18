"""Scripted mock HTTP only; observations still come from the Linux collector.

This tests plumbing, not natural-language understanding or live inference.
"""
import json
from agent.llm._async_worker import serve


class MockHTTP:
    def post(self, url, body, timeout, limit):
        request = json.loads(body)
        observations = json.loads(request['messages'][-1]['content'].split('\n', 1)[1])
        if not observations:
            message = {'role': 'assistant', 'content': '', 'tool_calls': [
                {'function': {'name': 'service_status', 'arguments': {'unit': 'nginx.service'}}}]}
        else:
            data = json.loads(observations[-1]['note'])['data']
            message = {'role': 'assistant', 'content': json.dumps({
                'conclusion': 'Systemd trả trạng thái ' + data['active_state'] + '/' + data['sub_state']
                              + ' cho nginx.service; chưa kiểm tra HTTP health hoặc sửa lỗi.',
                'citations': [e['id'] for o in observations for e in o['evidence']]})}
        return json.dumps({'model': request['model'], 'done': True, 'done_reason': 'stop',
                           'message': message}).encode()


if __name__ == '__main__':
    raise SystemExit(serve(MockHTTP()))
