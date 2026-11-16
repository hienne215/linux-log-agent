"""Harness regression checks only: no real Ollama/model/tools required."""
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import threading
import time
import unittest
import benchmark_ollama as bench

SUITE = json.loads((Path(__file__).parent / 'cases.json').read_text())
TOOLS = SUITE['tools']
EXPECTED = SUITE['cases'][0]['expected']


def native(name='service_status', arguments=None):
    return {'done': True, 'message': {'role': 'assistant', 'content': '', 'tool_calls': [
        {'function': {'name': name, 'arguments': {'unit': 'nginx.service'} if arguments is None else arguments}}]}}


class ScoringTests(unittest.TestCase):
    def score(self, response, expected=EXPECTED):
        return bench.evaluate(response, expected, TOOLS)

    def test_correct_native_call(self):
        self.assertTrue(self.score(native())['joint_success'])

    def test_wrong_tool_and_matching_args_are_separate(self):
        row = self.score(native('query_journal'))
        self.assertFalse(row['tool_correct'])
        self.assertTrue(row['arguments_correct'])
        self.assertFalse(row['joint_success'])
        self.assertEqual(row['classification'], 'wrong_tool')

    def test_wrong_arguments(self):
        row = self.score(native(arguments={'unit': 'ssh.service'}))
        self.assertTrue(row['tool_correct'])
        self.assertFalse(row['arguments_correct'])
        self.assertEqual(row['classification'], 'wrong_arguments')

    def test_json_in_prose_is_not_native_tool_call(self):
        row = self.score({'done': True, 'message': {'role': 'assistant', 'content': json.dumps(EXPECTED)}})
        self.assertEqual(row['classification'], 'text_only')
        self.assertFalse(row['joint_success'])

    def test_string_arguments_are_invalid_structure(self):
        self.assertEqual(self.score(native(arguments='{"unit":"nginx.service"}'))['classification'], 'invalid_structure')

    def test_multiple_tool_calls_fail(self):
        response = native()
        response['message']['tool_calls'] *= 2
        self.assertFalse(self.score(response)['valid_structure'])

    def test_incomplete_response_cannot_pass(self):
        response = native()
        response['done'] = False
        self.assertFalse(self.score(response)['joint_success'])
        self.assertEqual(self.score(response)['classification'], 'incomplete_response')

    def test_extra_and_wrong_typed_arguments(self):
        for args in ({'unit': 'nginx.service', 'shell': 'ignore'}, {'unit': 3}):
            self.assertFalse(self.score(native(arguments=args))['argument_schema_valid'])
        expected = SUITE['cases'][3]['expected']
        for port in ('80', True, 0, 65536):
            row = self.score(native('listening_ports', {'port': port, 'protocol': 'tcp'}), expected)
            self.assertFalse(row['argument_schema_valid'])
            self.assertFalse(row['joint_success'])

    def test_thinking_is_observation_not_tool_call(self):
        row = self.score({'message': {'content': '<think>ignored</think>', 'thinking': 'ignored'}})
        self.assertEqual(row['classification'], 'text_only')
        self.assertTrue(row['content_think_marker'])
        self.assertEqual(row['thinking_chars'], 7)
        self.assertNotIn('thinking', row)

    def test_all_expected_calls_fit_schemas(self):
        self.assertEqual(len(SUITE['cases']), 6)
        for case in SUITE['cases']:
            self.assertTrue(self.score(native(case['expected']['name'], case['expected']['arguments']), case['expected'])['joint_success'])

    def test_loopback_restriction(self):
        self.assertEqual(bench.local_url('http://127.0.0.1:11434'), 'http://127.0.0.1:11434')
        for value in ['https://example.com:11434', 'http://0.0.0.0:11434', 'http://localhost:11435',
                      'http://localhost:11434/api/chat', 'http://user:pass@localhost:11434',
                      'http://localhost:11434?next=http://example.com']:
            with self.assertRaises(ValueError):
                bench.local_url(value)

    def test_error_in_denominator(self):
        good = {**self.score(native()), 'think': False, 'wall_latency_s': 1, 'response_received': True}
        bad = {**self.score(None), 'classification': 'timeout', 'think': False,
               'wall_latency_s': 3, 'response_received': False}
        summary = bench.summarize([good, bad])['false']
        self.assertEqual(summary['joint_success_rate'], 0.5)
        self.assertEqual(summary['latency_mean_s_all_attempts'], 2)
        self.assertEqual(summary['latency_mean_s_responses_only'], 1)


class Handler(BaseHTTPRequestHandler):
    def do_POST(self):
        self.rfile.read(int(self.headers.get('Content-Length', 0)))
        if self.path == '/slow':
            time.sleep(1.5)
        payload = b'not valid json' if self.path == '/invalid' else json.dumps(native()).encode()
        self.send_response(200)
        self.end_headers()
        try:
            self.wfile.write(payload)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def log_message(self, *args):
        pass


class HTTPClientTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.url = f'http://127.0.0.1:{cls.server.server_port}'

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join()

    def test_http_json_error(self):
        result, _ = bench.bounded_chat(self.url + '/invalid', {}, 3)
        self.assertEqual(result['error_kind'], 'json_error')

    def test_hard_timeout(self):
        result, latency = bench.bounded_chat(self.url + '/slow', {}, 0.3)
        self.assertEqual(result['error_kind'], 'timeout')
        self.assertLess(latency, 1.3)

    def test_native_response(self):
        result, _ = bench.bounded_chat(self.url + '/ok', {}, 3)
        self.assertTrue(bench.evaluate(result['response'], EXPECTED, TOOLS)['joint_success'])


if __name__ == '__main__':
    unittest.main()
