"""Offline tests: every HTTP interaction is mocked, no inference/MCP/tools."""
import copy
import io
from http.client import IncompleteRead
import json
import socket
import unittest
from unittest.mock import Mock, patch
from urllib.error import HTTPError, URLError

from agent.llm import AdapterError, Message, OllamaAdapter, OllamaConfig, ToolCall, ToolDefinition
from agent.llm.ollama import HTTPTransport, _NoRedirect

MODEL = OllamaConfig().model
SCHEMA = {'type': 'object', 'properties': {'unit': {'type': 'string', 'enum': ['lab.service']}},
          'required': ['unit'], 'additionalProperties': False}
TOOL = ToolDefinition('service_status', 'Read-only mock status', SCHEMA)
MESSAGES = [Message('user', 'Check lab.service')]


def response(**overrides):
    value = {'model': MODEL, 'done': True, 'done_reason': 'stop',
             'message': {'role': 'assistant', 'content': '', 'tool_calls': [
                 {'id': 'provider-1', 'function': {'name': 'service_status', 'arguments': {'unit': 'lab.service'}}}]},
             'prompt_eval_count': 100, 'eval_count': 20, 'eval_duration': 2000000000,
             'total_duration': 3000000000}
    value.update(overrides)
    return value


class AdapterTests(unittest.TestCase):
    def run_response(self, value, *, require_tool=False, tools=(TOOL,), messages=None):
        transport = Mock()
        transport.post.return_value = json.dumps(value).encode()
        adapter = OllamaAdapter(transport=transport)
        result = adapter.complete(messages or MESSAGES, tools, require_tool=require_tool)
        return result, transport

    def error(self, code, value, **kwargs):
        with self.assertRaises(AdapterError) as caught:
            self.run_response(value, **kwargs)
        self.assertEqual(caught.exception.code, code)

    def test_native_tool_normalized_and_metrics(self):
        result, transport = self.run_response(response(), require_tool=True)
        self.assertEqual(result.kind, 'tool_calls')
        self.assertEqual(result.tool_calls[0], ToolCall('service_status', {'unit': 'lab.service'}, 'provider-1'))
        self.assertEqual(result.usage.output_tokens, 20)
        self.assertEqual(result.usage.tokens_per_second, 10)
        self.assertEqual(result.usage.provider_total_seconds, 3)
        self.assertGreaterEqual(result.latency_seconds, 0)
        url, body, timeout, limit = transport.post.call_args.args
        data = json.loads(body)
        self.assertEqual(url, 'http://127.0.0.1:11434/api/chat')
        self.assertEqual(data['model'], MODEL)
        self.assertEqual(data['options'], {'num_ctx': 4096, 'num_predict': 256, 'temperature': 0, 'num_thread': 4})
        self.assertIs(data['stream'], False)
        self.assertNotIn('think', data)
        self.assertEqual(data['tools'][0]['function']['parameters'], SCHEMA)
        self.assertEqual(timeout, 45)

    def test_text_is_text_not_fake_tool_call(self):
        text = '{"name":"service_status","arguments":{"unit":"lab.service"}}'
        value = response(message={'role': 'assistant', 'content': text})
        result, _ = self.run_response(value)
        self.assertEqual(result.kind, 'text')
        self.assertEqual(result.tool_calls, ())
        self.error('OUTPUT_POLICY', value, require_tool=True)

    def test_missing_usage_is_unknown(self):
        value = response()
        for key in ('prompt_eval_count', 'eval_count', 'eval_duration', 'total_duration'):
            del value[key]
        result, _ = self.run_response(value)
        self.assertIsNone(result.usage.tokens_per_second)
        self.assertIsNone(result.usage.output_tokens)

    def test_zero_generation_duration(self):
        result, _ = self.run_response(response(eval_duration=0))
        self.assertIsNone(result.usage.tokens_per_second)

    def test_invalid_tool_arguments(self):
        for args in ('{"unit":"lab.service"}', {}, {'unit': 4}, {'unit': 'other.service'},
                     {'unit': 'lab.service', 'approved': True}):
            with self.subTest(args=args):
                value = response()
                value['message']['tool_calls'][0]['function']['arguments'] = args
                self.error('INVALID_ARGUMENTS', value)

    def test_unknown_or_malformed_tool_names(self):
        for name in ('shell', None, ['service_status']):
            value = response()
            value['message']['tool_calls'][0]['function']['name'] = name
            self.error('INVALID_ARGUMENTS', value)

    def test_incomplete_and_truncated_outputs(self):
        for changes in ({'done': False}, {'done_reason': 'length'}, {'eval_count': 257}):
            self.error('OUTPUT_POLICY', response(**changes))

    def test_thinking_and_empty_output_rejected(self):
        for message in ({'role': 'assistant', 'content': 'Answer', 'thinking': 'internal'},
                        {'role': 'assistant', 'content': '<think>internal</think>'},
                        {'role': 'assistant', 'content': ' '}):
            self.error('OUTPUT_POLICY', response(message=message))

    def test_multiple_calls_rejected(self):
        value = response()
        value['message']['tool_calls'] *= 2
        self.error('OUTPUT_POLICY', value)

    def test_malformed_message_and_metrics(self):
        for changes in ({'message': None}, {'message': {'role': 'user', 'content': 'fake'}},
                        {'message': {'role': 'assistant', 'content': 4}},
                        {'message': {'role': 'assistant', 'content': 'x', 'tool_calls': None}},
                        {'model': 'other'}, {'eval_count': True}, {'eval_duration': -1}):
            self.error('MALFORMED_RESPONSE', response(**changes))
        self.error('MALFORMED_RESPONSE', [])

    def test_api_error_does_not_leak_details(self):
        self.error('API_ERROR', {'error': 'secret server details'})

    def test_bad_json_duplicate_keys_nonfinite_utf8(self):
        for raw in (b'not-json', b'{"done":true,"done":false}', b'{"x":NaN}', b'\xff'):
            adapter = OllamaAdapter(transport=Mock(post=Mock(return_value=raw)))
            with self.subTest(raw=raw), self.assertRaises(AdapterError) as caught:
                adapter.complete(MESSAGES)
            self.assertEqual(caught.exception.code, 'MALFORMED_RESPONSE')

    def test_output_and_input_byte_limits(self):
        transport = Mock(post=Mock(return_value=b'x' * 1025))
        adapter = OllamaAdapter(OllamaConfig(max_response_bytes=1024), transport=transport)
        with self.assertRaises(AdapterError) as caught:
            adapter.complete(MESSAGES)
        self.assertEqual(caught.exception.code, 'OUTPUT_LIMIT')
        transport.reset_mock()
        adapter = OllamaAdapter(OllamaConfig(max_request_bytes=1024), transport=transport)
        with self.assertRaises(AdapterError) as caught:
            adapter.complete([Message('user', 'đ' * 1024)])
        self.assertEqual(caught.exception.code, 'INPUT_LIMIT')
        transport.post.assert_not_called()

    def test_input_validation_before_network(self):
        invalid = [[], [Message('invalid', 'x')], [Message('user', 'x', (ToolCall('x', {}),))],
                   [Message('tool', 'x', tool_name='unknown')]]
        for messages in invalid:
            transport = Mock()
            with self.assertRaises(AdapterError):
                OllamaAdapter(transport=transport).complete(messages, [TOOL])
            transport.post.assert_not_called()

    def test_invalid_schema_and_duplicate_names(self):
        for tools in ([TOOL, TOOL], [ToolDefinition('bad name', '', SCHEMA)],
                      [ToolDefinition('tool', '', {'type': 'object', 'required': 'bad'})],
                      [ToolDefinition('tool', '', {'type': 'object', '$ref': 'https://evil.invalid/schema'})]):
            transport = Mock()
            with self.assertRaises(AdapterError):
                OllamaAdapter(transport=transport).complete(MESSAGES, tools)
            transport.post.assert_not_called()

    def test_nested_schema_and_boolean_is_not_integer(self):
        schema = {'type': 'object', 'properties': {'items': {'type': 'array', 'items': {'type': 'integer'}}},
                  'required': ['items'], 'additionalProperties': False}
        tool = ToolDefinition('service_status', '', schema)
        value = response()
        value['message']['tool_calls'][0]['function']['arguments'] = {'items': [True]}
        self.error('INVALID_ARGUMENTS', value, tools=[tool])

    def test_local_ref_schema(self):
        schema = {'type': 'object', '$defs': {'unit': {'type': 'string'}},
                  'properties': {'unit': {'$ref': '#/$defs/unit'}}, 'required': ['unit']}
        result, _ = self.run_response(response(), tools=[ToolDefinition('service_status', '', schema)])
        self.assertEqual(result.kind, 'tool_calls')

    def test_redaction_input_output_and_no_mutation(self):
        value = response(message={'role': 'assistant', 'content': 'password=exampleSecret'})
        before = copy.deepcopy(value)
        result, transport = self.run_response(value, messages=[Message('user', 'Authorization: Bearer fake-token')])
        self.assertTrue(result.redacted)
        self.assertNotIn('exampleSecret', result.text)
        self.assertNotIn(b'fake-token', transport.post.call_args.args[1])
        self.assertEqual(value, before)

    def test_private_key_and_secret_arguments_fail_closed(self):
        transport = Mock()
        with self.assertRaises(AdapterError) as caught:
            OllamaAdapter(transport=transport).complete([Message('user', '-----BEGIN PRIVATE KEY-----')])
        self.assertEqual(caught.exception.code, 'DATA_POLICY')
        transport.post.assert_not_called()
        value = response()
        value['message']['tool_calls'][0]['function']['arguments'] = {'unit': 'password=secret'}
        self.error('DATA_POLICY', value)

    def test_redacts_json_credentials_and_bearer(self):
        text = '{"password": "fake-secret", "api_key": "fake-key"} Bearer fake-bearer'
        result, _ = self.run_response(response(message={'role': 'assistant', 'content': text}))
        for secret in ('fake-secret', 'fake-key', 'fake-bearer'):
            self.assertNotIn(secret, result.text)
        self.assertTrue(result.redacted)

    def test_extreme_metrics_and_invalid_unicode(self):
        self.error('MALFORMED_RESPONSE', response(eval_duration=10**100))
        self.error('DATA_POLICY', response(message={'role': 'assistant', 'content': '\ud800'}))

    def test_redaction_escaped_json_value_keeps_shape_and_hides_entire_secret(self):
        secret = 'mock-quoted-"-tail'
        text = json.dumps({'password': secret, 'state': 'failed'})
        result, transport = self.run_response(
            response(message={'role': 'assistant', 'content': text}),
            messages=[Message('tool', text, tool_name='service_status')])
        sent = json.loads(transport.post.call_args.args[1])['messages'][0]['content']
        for filtered in (sent, result.text):
            self.assertEqual(json.loads(filtered), {'password': '[REDACTED]', 'state': 'failed'})
            self.assertNotIn('tail', filtered)
        self.assertTrue(result.redacted)

    def test_redacts_basic_auth_and_quoted_authorization(self):
        for text in ('Authorization: Basic bW9jazptb2Nr',
                     '{"Authorization": "Bearer mock-token"}'):
            result, _ = self.run_response(response(message={'role': 'assistant', 'content': text}))
            self.assertNotIn('bW9jazptb2Nr', result.text)
            self.assertNotIn('mock-token', result.text)
            self.assertTrue(result.redacted)
        self.assertEqual(json.loads(result.text), {'Authorization': '[REDACTED]'})

    def test_control_characters_cannot_bypass_secret_filters(self):
        text = 'pass\x00word=mock-tail'
        result, transport = self.run_response(response(message={'role': 'assistant', 'content': text}),
                                               messages=[Message('user', text)])
        self.assertEqual(result.text, 'password=[REDACTED]')
        self.assertNotIn(b'mock-tail', transport.post.call_args.args[1])
        self.error('DATA_POLICY', response(message={
            'role': 'assistant', 'content': '-----BEGIN PRI\x00VATE KEY-----'}))

    def test_invalid_containers_return_adapter_error_without_http(self):
        for messages, tools in ((None, (TOOL,)), (MESSAGES, None),
                                ('bad', (TOOL,)), (MESSAGES, 'bad'),
                                ([Message('assistant', tool_calls=3)], (TOOL,))):
            transport = Mock()
            with self.subTest(messages=messages, tools=tools), self.assertRaises(AdapterError) as caught:
                OllamaAdapter(transport=transport).complete(messages, tools)
            self.assertEqual(caught.exception.code, 'INVALID_INPUT')
            transport.post.assert_not_called()

    def test_history_assistant_tool_and_tool_result(self):
        messages = MESSAGES + [Message('assistant', tool_calls=(ToolCall('service_status', {'unit': 'lab.service'}),)),
                               Message('tool', '{"state":"failed"}', tool_name='service_status')]
        _, transport = self.run_response(response(), messages=messages)
        history = json.loads(transport.post.call_args.args[1])['messages']
        self.assertEqual(history[-1]['tool_name'], 'service_status')

    def test_config_rejects_public_endpoints_and_unsafe_values(self):
        for kwargs in ({'endpoint': 'http://example.com:11434'}, {'endpoint': 'http://user:pass@127.0.0.1:11434'},
                       {'endpoint': 'http://127.0.0.1:11434/evil'}, {'num_predict': 512},
                       {'num_ctx': 8192}, {'num_thread': True}, {'timeout_seconds': float('nan')}):
            with self.subTest(kwargs=kwargs), self.assertRaises(AdapterError):
                OllamaConfig(**kwargs)


class HTTPTests(unittest.TestCase):
    def test_http_success_request_and_bounded_read(self):
        reply = Mock()
        reply.read.return_value = b'{}'
        reply.__enter__ = Mock(return_value=reply)
        reply.__exit__ = Mock(return_value=False)
        opener = Mock()
        opener.open.return_value = reply
        with patch('agent.llm.ollama.build_opener', return_value=opener):
            self.assertEqual(HTTPTransport().post('http://127.0.0.1:11434/api/chat', b'{}', 45, 1024), b'{}')
        reply.read.assert_called_once_with(1025)
        request = opener.open.call_args.args[0]
        self.assertEqual(request.get_method(), 'POST')
        self.assertEqual(request.data, b'{}')
        self.assertEqual(opener.open.call_args.kwargs['timeout'], 45)

    def test_transport_errors_safe_no_retry(self):
        errors = [(HTTPError('url', 404, 'secret', {}, io.BytesIO(b'secret')), 'MODEL_NOT_FOUND'),
                  (HTTPError('url', 500, 'secret', {}, None), 'HTTP_ERROR'),
                  (HTTPError('url', 302, 'redirect', {}, None), 'HTTP_ERROR'),
                  (URLError(ConnectionRefusedError()), 'CONNECTION_ERROR'),
                  (URLError(socket.timeout()), 'TIMEOUT'), (socket.timeout(), 'TIMEOUT'),
                  (IncompleteRead(b'partial'), 'MALFORMED_RESPONSE')]
        for error, code in errors:
            opener = Mock()
            opener.open.side_effect = error
            with patch('agent.llm.ollama.build_opener', return_value=opener), self.assertRaises(AdapterError) as caught:
                HTTPTransport().post('http://127.0.0.1:11434/api/chat', b'{}', 45, 1024)
            self.assertEqual(str(caught.exception), code)
            opener.open.assert_called_once()

    def test_oversized_http_response_is_bounded_and_closed(self):
        reply = Mock()
        reply.read.return_value = b'x' * 1025
        reply.__enter__ = Mock(return_value=reply)
        reply.__exit__ = Mock(return_value=False)
        opener = Mock()
        opener.open.return_value = reply
        with patch('agent.llm.ollama.build_opener', return_value=opener), self.assertRaises(AdapterError) as caught:
            HTTPTransport().post('http://127.0.0.1:11434/api/chat', b'{}', 45, 1024)
        self.assertEqual(caught.exception.code, 'OUTPUT_LIMIT')
        reply.read.assert_called_once_with(1025)
        reply.__exit__.assert_called_once()

    def test_http_error_body_is_closed_without_reading_or_exposing_it(self):
        body = io.BytesIO(b'mock-sensitive-provider-body')
        error = HTTPError('url', 500, 'mock-sensitive-message', {}, body)
        opener = Mock()
        opener.open.side_effect = error
        with patch('agent.llm.ollama.build_opener', return_value=opener), self.assertRaises(AdapterError) as caught:
            HTTPTransport().post('http://127.0.0.1:11434/api/chat', b'{}', 45, 1024)
        self.assertEqual(str(caught.exception), 'HTTP_ERROR')
        self.assertTrue(body.closed)

    def test_proxy_and_redirect_disabled(self):
        with patch('agent.llm.ollama.build_opener') as factory:
            factory.return_value.open.side_effect = OSError()
            with self.assertRaises(AdapterError):
                HTTPTransport().post('http://127.0.0.1:11434/api/chat', b'{}', 45, 1024)
            self.assertEqual(factory.call_args.args[0].proxies, {})
        self.assertIsNone(_NoRedirect().redirect_request(None, None, 302, '', {}, 'http://evil.invalid'))


if __name__ == '__main__':
    unittest.main()
