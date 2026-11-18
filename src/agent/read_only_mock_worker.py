"""Explicit scripted HTTP fixture; tool results are supplied by the selected SDK profiles."""
import json
from agent.llm._async_worker import serve


class MockHTTP:
    def post(self, url, body, timeout, limit):
        request = json.loads(body)
        observations = json.loads(request['messages'][-1]['content'].split('\n', 1)[1])
        observed = [json.loads(o['note'])['data'] for o in observations]
        remaining = [t['function'] for t in request['tools'] if not any(
            (t['function']['name'] == 'service_status' and 'unit' in d)
            or (t['function']['name'] == 'disk_usage' and 'filesystem' in d)
            or (t['function']['name'] == 'list_failed_units' and 'failed_units' in d)
            or (t['function']['name'] == 'listening_ports' and 'ports' in d) for d in observed)]
        if remaining:
            calls=[]
            for item in remaining:
                tool=item['name']
                arguments={'service_status': {'unit':'nginx.service'}, 'disk_usage': {'filesystem':'root'},
                           'list_failed_units': {}, 'listening_ports': {'protocol':'tcp', 'port':80}}[tool]
                calls.append({'function':{'name':tool,'arguments':arguments}})
            message={'role':'assistant','content':'','tool_calls':calls}
        else:
            notes = []
            for d in observed:
                if 'unit' in d:
                    notes.append('nginx.service: '+d['active_state']+'/'+d['sub_state'])
                elif 'filesystem' in d:
                    notes.append('root filesystem used_percent='+str(d['used_percent']))
                elif 'failed_units' in d:
                    notes.append('failed units count='+str(d['count']))
                else:
                    notes.append('TCP port 80 socket count='+str(d['count'])+'; ownership unknown')
            message = {'role':'assistant','content':json.dumps({
                'conclusion':'; '.join(notes)+'. Không kết luận nguyên nhân website hoặc sửa lỗi.',
                'citations':[e['id'] for o in observations for e in o['evidence']]})}
        return json.dumps({'model':request['model'],'done':True,'done_reason':'stop','message':message}).encode()


if __name__ == '__main__':
    raise SystemExit(serve(MockHTTP()))
