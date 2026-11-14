"""Synthetic credential regressions. Assertion messages never include values."""
import copy
import json
import unittest
from unittest.mock import Mock

from agent.llm import AdapterError, Message, OllamaAdapter, ToolCall, ToolDefinition
from agent.llm.ollama import _clean

FAKE = 'synthetic-value-42'
KEYS = ('db_password', 'client_secret', 'AWS_SECRET_ACCESS_KEY', 'secret_key',
        'token', 'refresh_token', 'passwd', 'access_token', 'api_key')
TOOL = ToolDefinition('mock_inspect', 'Mock only', {'type': 'object'})


def text_response():
    return json.dumps({'model': 'qwen3:4b-instruct-2507-q4_K_M', 'done': True,
                       'done_reason': 'stop', 'message': {'role': 'assistant', 'content': 'ok'}}).encode()


class RedactionTests(unittest.TestCase):
    def assert_safe_equal(self, actual, expected):
        self.assertTrue(actual == expected, 'expected redacted structure; values omitted')

    def assert_hidden(self, text):
        self.assertTrue(FAKE not in text, 'synthetic credential must not survive')

    def assert_policy(self, call):
        with self.assertRaises(AdapterError) as caught:
            call()
        self.assert_safe_equal(str(caught.exception), 'DATA_POLICY')

    def test_reviewer_key_assignments(self):
        for key in KEYS:
            for separator in ('=', ': ', '=\"'):
                text = key + separator + FAKE + ('\"' if separator.endswith('"') else '')
                with self.subTest(key=key, format=separator):
                    self.assert_hidden(_clean(text))

    def test_prefix_suffix_and_camel_case_keys(self):
        for key in ('app_password_backup', 'dbPassword', 'refreshToken', 'clientSecret',
                    'AWS_SECRET_ACCESS_KEY_backup', 'service_api_key_id', 'dbpassword', '1_password', 'clientSECRETValue'):
            with self.subTest(key=key):
                self.assert_hidden(_clean(key + '=' + FAKE))

    def test_password_is_value(self):
        self.assert_hidden(_clean('password is ' + FAKE))

    def test_nested_json_preserves_valid_structure(self):
        data = {'safe': 'Thông tin', 'items': [{'client_secret': FAKE}, {'refresh_token': FAKE}], 'count': 2}
        result = json.loads(_clean(json.dumps(data, ensure_ascii=False)))
        self.assert_safe_equal(result['safe'], data['safe'])
        self.assert_safe_equal(result['count'], 2)
        self.assert_safe_equal(result['items'], [{'client_secret': '[REDACTED]'}, {'refresh_token': '[REDACTED]'}])

    def test_unicode_escaped_json_keys(self):
        text = '{"db_\\u0070assword":' + json.dumps(FAKE) + '}'
        self.assert_hidden(_clean(text))
        self.assert_safe_equal(json.loads(_clean(text))['db_password'], '[REDACTED]')

    def test_json_encoded_json_strings(self):
        text = json.dumps(json.dumps({'token': FAKE, 'safe': 'yes'}))
        cleaned = _clean(text)
        self.assert_hidden(cleaned)
        self.assert_safe_equal(json.loads(json.loads(cleaned)), {'token': '[REDACTED]', 'safe': 'yes'})

    def test_bare_escaped_json_fragment(self):
        text = json.dumps(json.dumps({'refresh_token': FAKE}))[1:-1]
        cleaned = _clean(text)
        self.assert_hidden(cleaned)
        decoded = json.loads('"' + cleaned + '"')
        self.assert_safe_equal(json.loads(decoded)['refresh_token'], '[REDACTED]')

    def test_json_embedded_in_log(self):
        text = 'INFO payload=' + json.dumps({'db_password': FAKE, 'state': 'failed'}) + '\nnext=ok'
        result = _clean(text)
        self.assert_hidden(result)
        self.assertTrue(result.startswith('INFO payload='))
        self.assertTrue(result.endswith('\nnext=ok'))

    def test_no_credential_in_http_messages(self):
        for key in KEYS:
            for text in (key + '=' + FAKE, json.dumps({key: FAKE}),
                         json.dumps(json.dumps({key: FAKE}))):
                transport = Mock(post=Mock(return_value=text_response()))
                OllamaAdapter(transport=transport).complete([Message('user', text)])
                body = transport.post.call_args.args[1]
                self.assertTrue(FAKE.encode() not in body, 'HTTP body must not contain synthetic credential')

    def test_tool_arguments_secret_keys_rejected_without_mutation_or_http(self):
        for key in KEYS:
            arguments = {'nested': {key: FAKE}}
            original = copy.deepcopy(arguments)
            transport = Mock()
            call = ToolCall('mock_inspect', arguments)
            self.assert_policy(lambda: OllamaAdapter(transport=transport).complete(
                [Message('assistant', tool_calls=(call,))], [TOOL]))
            self.assertTrue(arguments == original, 'arguments must not be rewritten')
            transport.post.assert_not_called()

    def test_tool_arguments_secret_text_rejected(self):
        transport = Mock()
        call = ToolCall('mock_inspect', {'detail': 'password is ' + FAKE})
        self.assert_policy(lambda: OllamaAdapter(transport=transport).complete(
            [Message('assistant', tool_calls=(call,))], [TOOL]))
        transport.post.assert_not_called()

    def test_native_response_secret_arguments_rejected(self):
        for key in KEYS:
            value = json.loads(text_response())
            value['message'] = {'role': 'assistant', 'content': '', 'tool_calls': [
                {'function': {'name': 'mock_inspect', 'arguments': {key: FAKE}}}]}
            transport = Mock(post=Mock(return_value=json.dumps(value).encode()))
            self.assert_policy(lambda: OllamaAdapter(transport=transport).complete(
                [Message('user', 'Inspect mock state')], [TOOL]))
            transport.post.assert_called_once()

    def test_cli_and_prose_filtered_before_http(self):
        for text in ('mysql -p' + FAKE, 'password is ' + FAKE):
            transport = Mock(post=Mock(return_value=text_response()))
            OllamaAdapter(transport=transport).complete([Message('user', text)])
            self.assertTrue(FAKE.encode() not in transport.post.call_args.args[1], 'HTTP body sanitized')
        transport = Mock()
        self.assert_policy(lambda: OllamaAdapter(transport=transport).complete([Message('user', '-p' + FAKE)]))
        transport.post.assert_not_called()

    def test_depth_duplicates_and_private_key_fail_closed(self):
        nested = 'normal'
        for _ in range(20):
            nested = json.dumps(nested)
        self.assert_policy(lambda: _clean(nested))
        self.assert_policy(lambda: _clean('{"token":"","token":' + json.dumps(FAKE) + '}'))
        self.assert_policy(lambda: _clean('{"detail":"-----BEGIN PRIVATE KEY-----"}'))

    def test_multiline_text_and_quoted_empty_values(self):
        text = 'client_secret="' + FAKE + '\nsecond-line"\nstatus=ok'
        filtered = _clean(text)
        self.assert_hidden(filtered)
        self.assertTrue('second-line' not in filtered, 'entire multiline credential removed')
        self.assertTrue(filtered.endswith('\nstatus=ok'))
        self.assert_safe_equal(_clean('password is ""\nstatus=ok'), 'password is ""\nstatus=ok')

    def test_container_values_assigned_to_secret_text_keys(self):
        for text in ('client_secret=' + json.dumps({'value': FAKE}),
                     'token: ' + json.dumps([FAKE]),
                     '"db_\\u0070assword":' + json.dumps({'value': FAKE})):
            self.assert_hidden(_clean(text))

    def test_long_secret_cli_assignment(self):
        self.assert_hidden(_clean('mysql --password=' + FAKE))
        self.assert_safe_equal(_clean('server --port=80'), 'server --port=80')

    def test_escaped_field_in_text_log(self):
        field = json.dumps('"refresh_token":' + json.dumps(FAKE))[1:-1]
        self.assert_hidden(_clean('INFO ' + field))

    def test_sensitive_empty_argument_key_still_rejected(self):
        transport = Mock()
        call = ToolCall('mock_inspect', {'refresh_token': ''})
        self.assert_policy(lambda: OllamaAdapter(transport=transport).complete(
            [Message('assistant', tool_calls=(call,))], [TOOL]))
        transport.post.assert_not_called()

    def test_normal_mentions_and_noncredential_json_unchanged(self):
        for text in ('The token and password fields are documented.', 'Use -p for a port.',
                     '{"tokenizer":"enabled","passwordless":true,"state":"failed"}'):
            self.assert_safe_equal(_clean(text), text)

    def test_empty_values_do_not_consume_next_line(self):
        for text in ('db_password=\nstate=healthy', 'token: \nstatus=ready',
                     'refresh_token=""', '{"token":"","secret":null,"state":"ok"}'):
            self.assert_safe_equal(_clean(text), text)

    def test_multiline_and_unicode_json_values(self):
        data = {'password': FAKE + '\nDòng thứ hai: "\\', 'safe': 'Tiếng Việt\nOK'}
        result = json.loads(_clean(json.dumps(data, ensure_ascii=False)))
        self.assert_safe_equal(result, {'password': '[REDACTED]', 'safe': data['safe']})

    def test_cli_password_requires_known_command_context(self):
        for command in ('mysql', '/usr/bin/mysql', 'mariadb', 'mysqldump'):
            self.assert_hidden(_clean(command + ' -u demo -p' + FAKE))
        self.assert_policy(lambda: _clean('-p' + FAKE))

    def test_cli_nonpassword_short_p_not_redacted(self):
        for text in ('curl -p https://example.invalid', 'python app.py -profile=dev',
                     'server --port 80', 'grep -pattern sample'):
            self.assert_safe_equal(_clean(text), text)

    def test_malformed_secret_structures_fail_closed(self):
        for text in ('{"db_password":"' + FAKE, 'client_secret="' + FAKE):
            self.assert_policy(lambda: _clean(text))


if __name__ == '__main__':
    unittest.main()
