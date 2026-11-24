"""Explicit scripted HTTP fixture; tool results are supplied by the selected SDK profiles."""
import json
from agent.llm._async_worker import serve


class MockHTTP:
    def post(self, url, body, timeout, limit):
        request = json.loads(body)
        observations = json.loads(request['messages'][-1]['content'].split('\n', 1)[1])
        observed = [json.loads(o['note'])['data'] for o in observations]
        remaining = [t['function'] for t in request.get('tools', []) if not any(
            (t['function']['name'] == 'service_status' and 'active_state' in d)
            or (t['function']['name'] == 'disk_usage' and 'filesystem' in d)
            or (t['function']['name'] == 'list_failed_units' and 'failed_units' in d)
            or (t['function']['name'] == 'listening_ports' and 'ports' in d)
            or (t['function']['name'] == 'query_journal' and 'entries' in d)
            or (t['function']['name'] == 'search_log' and 'matches' in d)
            or (t['function']['name'] == 'detect_anomaly' and 'anomalies' in d)
            or (t['function']['name'] == 'verify_fix' and 'checks' in d)
            or (t['function']['name'] == 'memory_oom_events' and any(c['source']=='journal.kernel' for c in d.get('coverage',[])))
            or (t['function']['name'] == 'failed_logins' and any(c['source'] in ('journal.auth','auth.log') for c in d.get('coverage',[])))
            or (t['function']['name'] == 'check_config' and 'profile' in d) for d in observed)]
        if remaining:
            calls=[]
            for item in remaining:
                tool=item['name']
                arguments={'service_status': {'unit':'nginx.service'}, 'disk_usage': {'filesystem':'root'},
                           'list_failed_units': {}, 'listening_ports': {'protocol':'tcp', 'port':80},
                           'query_journal': {'unit':'nginx.service'},
                           'search_log': {'source_id':'nginx_journal','query':'error'},
                           'detect_anomaly': {'unit':'nginx.service'},
                           'verify_fix': {'unit':'nginx.service'},'memory_oom_events':{},'failed_logins':{},
                           'check_config':{'profile':'nginx'},'restart_service':{'unit':'nginx.service','mode':'propose'}}[tool]
                calls.append({'function':{'name':tool,'arguments':arguments}})
                if len(calls) == 5:
                    break
            message={'role':'assistant','content':'','tool_calls':calls}
        else:
            notes = []
            for d in observed:
                if 'coverage' in d:
                    notes.append('Visible system records='+str(d['count'])+'; source coverage='+json.dumps(d['coverage'])+'; not complete history')
                elif 'profile' in d:
                    notes.append('Configuration syntax valid='+str(d['valid'])+'; no reload/restart')
                elif 'checks' in d:
                    notes.append('Read-only checks='+json.dumps({c['check_type']:c['result'] for c in d['checks']})+
                        '; aggregate='+d['overall_result']+'; no remediation or complete source coverage verified')
                elif 'unit' in d:
                    notes.append('nginx.service: '+d['active_state']+'/'+d['sub_state'])
                elif 'filesystem' in d:
                    notes.append('root filesystem used_percent='+str(d['used_percent']))
                elif 'failed_units' in d:
                    notes.append('failed units count='+str(d['count']))
                elif 'ports' in d:
                    notes.append('TCP port 80 socket count='+str(d['count'])+'; ownership unknown')
                elif 'anomalies' in d:
                    notes.append('Visible-journal rate findings='+str(len(d['anomalies']))+'; incomplete history, uncalibrated rule')
                else:
                    notes.append('Visible journal records='+str(d['count'])+'; raw text withheld; incomplete visibility')
            message = {'role':'assistant','content':json.dumps({
                'conclusion':'; '.join(notes)+'. Không kết luận nguyên nhân website hoặc sửa lỗi.',
                'citations':[e['id'] for o in observations for e in o['evidence']]})}
        return json.dumps({'model':request['model'],'done':True,'done_reason':'stop','message':message}).encode()


if __name__ == '__main__':
    raise SystemExit(serve(MockHTTP()))
