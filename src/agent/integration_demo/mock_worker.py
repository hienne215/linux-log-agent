"""Owned Adapter worker with fake HTTP transport; never opens a network socket."""
import json
import sys
from .fixtures import SCENARIOS, steps, conclusion
from agent.llm._async_worker import serve
from agent.llm.interface import AdapterError


class MockHTTP:
    def __init__(self, scenario, fault='none'):
        self.scenario, self.fault = scenario, fault

    def post(self, url, body, timeout, limit):
        request = json.loads(body)
        if self.fault == 'timeout':
            raise AdapterError('TIMEOUT')
        if self.fault == 'late':
            import time
            time.sleep(30)
        if self.fault == 'malformed':
            return b'{invalid'
        observations = json.loads(request['messages'][-1]['content'].split('\n', 1)[1])
        refs = [e['id'] for o in observations for e in o['evidence']]
        sequence = steps(self.scenario)
        if self.fault == 'write':
            name, arguments = 'restart_service', {'unit': 'nginx.service'}
        elif self.fault == 'arguments':
            name, arguments = 'service_status', {'unit': 'ssh.service'}
        elif self.fault == 'unknown':
            name, arguments = 'unknown_tool', {'unit': 'nginx.service'}
        elif len(observations) < len(sequence):
            name, arguments = sequence[len(observations)], {'unit': 'nginx.service'}
        else:
            message = {'role': 'assistant', 'content': json.dumps(
                {'conclusion': conclusion(self.scenario), 'citations': refs})}
            return self.response(request, message)
        message = {'role': 'assistant', 'content': '', 'tool_calls': [
            {'function': {'name': name, 'arguments': arguments}}]}
        return self.response(request, message)

    @staticmethod
    def response(request, message):
        return json.dumps({'model': request['model'], 'done': True, 'done_reason': 'stop',
                           'message': message}).encode()


if __name__ == '__main__':
    if len(sys.argv) != 3 or sys.argv[1] not in SCENARIOS or sys.argv[2] not in {
            'none', 'timeout', 'late', 'malformed', 'write', 'arguments', 'unknown'}:
        raise SystemExit(2)
    raise SystemExit(serve(MockHTTP(sys.argv[1], sys.argv[2])))
