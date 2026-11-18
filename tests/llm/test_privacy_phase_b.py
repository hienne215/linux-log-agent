"""Phase B synthetic privacy regressions; failures never print credential values."""
import copy
import json
import unittest
from unittest.mock import Mock

from agent.llm import AdapterError, Message, OllamaAdapter, ToolCall, ToolDefinition
from agent.llm.redaction import _clean, _is_secret_key

VALUE = 'phase-b-' + 'synthetic-value'
USER = 'phase-b-' + 'synthetic-user'
TOOL = ToolDefinition('mock_read', 'Synthetic only', {'type': 'object'})


def response(content='ok', calls=None):
    message = {'role': 'assistant', 'content': content}
    if calls is not None:
        message['tool_calls'] = calls
    return json.dumps({'model': 'qwen3:4b-instruct-2507-q4_K_M',
                       'done': True, 'done_reason': 'stop', 'message': message}).encode()


def samples():
    url = 'postgres://' + USER + ':' + VALUE + '@fixture.invalid/db'
    return [('mysql_assignment', 'MYSQL_PWD=' + VALUE),
            ('url', url), ('json_mysql', json.dumps({'MYSQL_PWD': VALUE, 'state': 'ok'})),
            ('json_url', json.dumps({'connection': url, 'state': 'ok'})),
            ('embedded_json', 'INFO payload=' + json.dumps({'MYSQL_PWD': VALUE, 'url': url})),
            ('nested_json_string', json.dumps(json.dumps({'MYSQL_PWD': VALUE, 'url': url}))),
            ('escaped_json', json.dumps(json.dumps({'MYSQL_PWD': VALUE, 'url': url}))[1:-1])]


class PrivacyPhaseBTests(unittest.TestCase):
    def hidden(self, text, *, username=False):
        self.assertTrue(VALUE not in text, 'synthetic credential survived; value omitted')
        if username:
            self.assertTrue(USER not in text, 'synthetic userinfo survived; value omitted')

    def policy(self, call):
        with self.assertRaises(AdapterError) as caught:
            call()
        self.assertTrue(str(caught.exception) == 'DATA_POLICY', 'safe policy code required')
        self.hidden(str(caught.exception), username=True)

    def test_mysql_key_detection_and_assignments(self):
        for key in ('MYSQL_PWD', 'mysql_pwd', 'mysqlPwd', 'APP_MYSQL_PWD', 'ＭＹＳＱＬ＿ＰＷＤ'):
            with self.subTest(kind='key_variant'):
                self.assertTrue(_is_secret_key(key), 'credential key must be classified')
                for sep in ('=', ': ', ' is '):
                    self.hidden(_clean(key + sep + VALUE))

    def test_mysql_quoted_and_multiline_values(self):
        for quote in ('"', "'"):
            self.hidden(_clean('MYSQL_PWD=' + quote + VALUE + '\nsecond-line' + quote))
        self.assertTrue('second-line' not in _clean('MYSQL_PWD="' + VALUE + '\nsecond-line"'))

    def test_url_userinfo_username_and_password_removed(self):
        for scheme in ('postgres', 'mysql', 'https', 'ssh'):
            self.hidden(_clean(scheme + '://' + USER + ':' + VALUE + '@fixture.invalid:5432/db'), username=True)

    def test_url_username_only_and_empty_password(self):
        for suffix in ('', ':'):
            self.hidden(_clean('ssh://' + USER + suffix + '@fixture.invalid'), username=True)

    def test_percent_encoded_userinfo_and_case(self):
        for userinfo in (USER + ':' + VALUE + '%40x', USER + '%3A' + VALUE, ':' + VALUE):
            self.hidden(_clean('HTTPS://' + userinfo + '@fixture.invalid/'), username=True)

    def test_scheme_relative_credentials(self):
        self.hidden(_clean('//' + USER + ':' + VALUE + '@fixture.invalid/db'), username=True)

    def test_url_in_json_and_log_shapes(self):
        for label, text in samples():
            with self.subTest(shape=label):
                self.hidden(_clean(text))
        result = json.loads(_clean(json.dumps({'MYSQL_PWD': VALUE, 'state': 'ok',
            'nested': [{'url': 'postgres://' + USER + ':' + VALUE + '@fixture.invalid/db'}]})))
        self.assertTrue(result['state'] == 'ok' and isinstance(result['nested'], list), 'JSON structure preserved')
        self.hidden(json.dumps(result), username=True)

    def test_unicode_escaped_json_fields(self):
        text = '{"MYSQL_\\u0050WD":' + json.dumps(VALUE) + '}'
        self.hidden(_clean(text))
        url = 'postgres://' + USER + ':' + VALUE + '@fixture.invalid/db'
        self.hidden(_clean(json.dumps({'url': url}).replace('@', '\\u0040')), username=True)

    def test_input_messages_are_sanitized_before_real_adapter_http(self):
        for label, text in samples():
            for role in ('system', 'user', 'assistant', 'tool'):
                with self.subTest(shape=label, role=role):
                    transport = Mock(post=Mock(return_value=response()))
                    message = Message(role, text, tool_name='mock_read' if role == 'tool' else None)
                    OllamaAdapter(transport=transport).complete((message,), (TOOL,))
                    transport.post.assert_called_once()
                    self.hidden(transport.post.call_args.args[1].decode())

    def test_output_text_and_structured_data_are_sanitized_by_real_adapter(self):
        for label, text in samples():
            with self.subTest(shape=label):
                transport = Mock(post=Mock(return_value=response(text)))
                result = OllamaAdapter(transport=transport).complete((Message('user', 'Synthetic only'),))
                self.hidden(result.text)
                self.assertTrue(result.redacted, 'Adapter must record local redaction')

    def test_tool_arguments_are_rejected_without_mutation_or_http(self):
        for args in ({'MYSQL_PWD': VALUE}, {'nested': {'MYSQL_PWD': ''}},
                     {'detail': 'MYSQL_PWD=' + VALUE},
                     {'connection': 'postgres://' + USER + ':' + VALUE + '@fixture.invalid/db'}):
            original = copy.deepcopy(args); transport = Mock()
            call = ToolCall('mock_read', args)
            self.policy(lambda: OllamaAdapter(transport=transport).complete(
                (Message('assistant', tool_calls=(call,)),), (TOOL,)))
            self.assertTrue(args == original, 'arguments must not be rewritten')
            transport.post.assert_not_called()

    def test_native_response_arguments_are_rejected_not_repaired(self):
        for args in ({'MYSQL_PWD': VALUE}, {'url': 'https://' + USER + ':' + VALUE + '@fixture.invalid'}):
            transport = Mock(post=Mock(return_value=response(calls=[{'function': {'name': 'mock_read', 'arguments': args}}])))
            self.policy(lambda: OllamaAdapter(transport=transport).complete((Message('user', 'Synthetic only'),), (TOOL,)))
            transport.post.assert_called_once()

    def test_malformed_credentials_fail_closed_before_http(self):
        urls = ['postgres:' + '\\\\' + USER + ':' + VALUE + '@fixture.invalid/db',
                'postgres:' + '/\\' + USER + ':' + VALUE + '@fixture.invalid/db',
                'postgres://' + USER + ':' + VALUE + '@',
                'postgres://' + USER + ':' + VALUE + '@fixture.invalid:invalid',
                'postgres://' + USER + ':' + VALUE + '@one@two',
                'postgres://' + USER + ':"' + VALUE + '@fixture.invalid',
                'postgres://' + USER + ':123"' + VALUE + '@fixture.invalid',
                'postgres://' + USER + ':123\'' + VALUE + '@fixture.invalid',
                'postgres://' + USER + ':' + VALUE + '#fragment@fixture.invalid',
                'postgres://' + USER + ' space:' + VALUE + '@fixture.invalid',
                'postgres://' + USER + ':' + VALUE + '@[broken',
                'postgres%3A%2F%2F' + USER + '%3A' + VALUE + '%40fixture.invalid',
                'INFO \\"MYSQL_PWD\\":\\"' + VALUE]
        for index, text in enumerate(urls):
            with self.subTest(format=index):
                transport = Mock()
                self.policy(lambda: OllamaAdapter(transport=transport).complete((Message('user', text),)))
                transport.post.assert_not_called()

    def test_empty_mysql_values_and_unrelated_keys_preserved(self):
        for text in ('MYSQL_PWD=\nstate=ok', 'MYSQL_PWD=""', '{"MYSQL_PWD":"","state":"ok"}',
                     '{"MYSQL_PWD":null,"PWD":"/workspace","mysql_port":3306}',
                     'The MYSQL_PWD variable is documented; no value is present.'):
            self.assertTrue(_clean(text) == text, 'benign/empty data should remain unchanged')
        self.assertFalse(_is_secret_key('PWD'), 'working directory is not mysql credential')

    def test_benign_urls_and_email_preserved(self):
        for text in ('https://fixture.invalid/path?x=1', 'https://fixture.invalid/path@segment',
                     'reader@example.invalid', 'See https://fixture.invalid/ and reader@example.invalid',
                     'http://fixture.invalid:8080/path', 'file:///workspace/example.txt'):
            self.assertTrue(_clean(text) == text, 'noncredential URL/text must be preserved')

    def test_url_redaction_is_idempotent_and_bounds_hold(self):
        cleaned = _clean('postgres://' + USER + ':' + VALUE + '@fixture.invalid/db')
        self.hidden(cleaned, username=True)
        self.assertTrue(_clean(cleaned) == cleaned, 'redaction must be idempotent')
        self.policy(lambda: _clean('x' * (1024 * 1024 + 1)))


class PrivacyAsyncBoundaryTests(unittest.IsolatedAsyncioTestCase):
    async def test_actual_async_adapter_sanitizes_before_worker_ipc(self):
        from dataclasses import asdict
        from agent.llm.async_adapter import AsyncOllamaAdapter
        for label, text in samples():
            with self.subTest(shape=label):
                adapter = AsyncOllamaAdapter(seconds=5, max_calls=1)
                packets = []
                safe = OllamaAdapter(transport=Mock(post=Mock(return_value=response()))).complete((Message('user', 'Synthetic only'),))
                async def perform(payload, abort):
                    packets.append(payload)
                    return json.dumps({'ok': True, 'completion': asdict(safe)}).encode()
                adapter._perform = perform  # observe existing production pre-IPC validation, no child/network
                await adapter.complete((Message('user', text),), (TOOL,))
                await adapter.finish_cleanup()
                self.assertTrue(len(packets) == 1 and VALUE.encode() not in packets[0] and USER.encode() not in packets[0], 'IPC must be sanitized')
                self.assertFalse(adapter.cleanup_pending)

    async def test_actual_async_adapter_rejects_secret_arguments_before_ipc(self):
        from unittest.mock import AsyncMock
        from agent.llm.async_adapter import AsyncOllamaAdapter, AsyncLLMError
        adapter = AsyncOllamaAdapter(seconds=5, max_calls=1)
        adapter._perform = AsyncMock()
        with self.assertRaises(AsyncLLMError) as caught:
            await adapter.complete((Message('assistant', tool_calls=(ToolCall('mock_read', {'MYSQL_PWD': VALUE}),)),), (TOOL,))
        self.assertTrue(str(caught.exception) == 'DATA_POLICY', 'safe policy code required')
        adapter._perform.assert_not_called()
        self.assertTrue(adapter.calls_used == 0 and not adapter.cleanup_pending)

    async def test_decision_source_redacts_question_and_evidence_messages(self):
        from dataclasses import asdict
        from unittest.mock import AsyncMock
        from agent.llm.decision_source import AsyncDecisionSource
        from agent.coordinator import Evidence, Observation
        completion = OllamaAdapter(transport=Mock(post=Mock(return_value=response(
            json.dumps({'conclusion': 'Synthetic only.', 'citations': ['ev-1']}))))).complete((Message('user', 'Synthetic only'),))
        fake = Mock(complete=AsyncMock(return_value=completion))
        source = AsyncDecisionSource(fake, (TOOL,), read_only=frozenset({'mock_read'}))
        data = 'MYSQL_PWD=' + VALUE + '\npostgres://' + USER + ':' + VALUE + '@fixture.invalid/db'
        await source.choose(data, (Observation((Evidence('ev-1', 'synthetic.fixture', data),)),))
        messages = fake.complete.call_args.args[0]
        payload = json.dumps([asdict(m) for m in messages])
        self.assertTrue(VALUE not in payload and USER not in payload, 'DecisionSource messages must be sanitized')
        self.assertTrue('UNTRUSTED OBSERVATIONS' in messages[-1].content)


class PrivacyReviewerRegressions(unittest.TestCase):
    def test_nfkc_key_aliases_before_http(self):
        for separator in ('－', '︳', '︴', '﹍', '﹎', '﹏', '﹣', '．', '․', '﹒', '＿'):
            key = 'ＭＹＳＱＬ' + separator + 'ＰＷＤ'
            for text in (key + '=' + VALUE,
                         json.dumps({'payload': key + '=' + VALUE}),
                         json.dumps({key: VALUE})):
                for role in ('user', 'tool'):
                    with self.subTest(separator=ord(separator), role=role):
                        transport = Mock(post=Mock(return_value=response()))
                        message = Message(role, text, tool_name='mock_read' if role == 'tool' else None)
                        OllamaAdapter(transport=transport).complete((message,), (TOOL,))
                        self.assertTrue(VALUE not in transport.post.call_args.args[1].decode(),
                                        'credential reached mock HTTP; value omitted')

    def test_multiline_url_authority_rejected_before_http(self):
        for newline in ('\n', '\r', '\r\n'):
            for authority in (newline + USER + ':' + VALUE,
                              USER + ':' + newline + VALUE):
                url = 'postgres://' + authority + '@fixture.invalid/db'
                for text in (url, json.dumps({'nested': [{'url': url}]}), json.dumps(json.dumps({'url': url}))):
                    with self.subTest(newline=repr(newline)):
                        transport = Mock()
                        with self.assertRaises(AdapterError) as caught:
                            OllamaAdapter(transport=transport).complete((Message('tool', text, tool_name='mock_read'),), (TOOL,))
                        self.assertTrue(str(caught.exception) == 'DATA_POLICY')
                        transport.post.assert_not_called()

    def test_benign_unicode_message_not_normalized(self):
        text = 'The ＭＹＳＱＬ－ＰＷＤ variable; 状態：正常。'
        self.assertTrue(_clean(text) == text)


class PrivacyReviewerIPCRegressions(unittest.IsolatedAsyncioTestCase):
    async def test_nfkc_alias_sanitized_before_ipc(self):
        from dataclasses import asdict
        from agent.llm.async_adapter import AsyncOllamaAdapter
        safe = OllamaAdapter(transport=Mock(post=Mock(return_value=response()))).complete((Message('user', 'Synthetic only'),))
        for separator in ('－', '︳'):
            adapter = AsyncOllamaAdapter(seconds=5, max_calls=1)
            packets = []
            async def perform(payload, abort):
                packets.append(payload)
                return json.dumps({'ok': True, 'completion': asdict(safe)}).encode()
            adapter._perform = perform
            await adapter.complete((Message('tool', 'ＭＹＳＱＬ' + separator + 'ＰＷＤ=' + VALUE, tool_name='mock_read'),), (TOOL,))
            await adapter.finish_cleanup()
            self.assertTrue(len(packets) == 1 and VALUE.encode() not in packets[0], 'credential reached IPC; value omitted')
            self.assertFalse(adapter.cleanup_pending)

    async def test_multiline_authority_rejected_before_ipc(self):
        from unittest.mock import AsyncMock
        from agent.llm.async_adapter import AsyncOllamaAdapter, AsyncLLMError
        for newline in ('\n', '\r', '\r\n'):
            adapter = AsyncOllamaAdapter(seconds=5, max_calls=1)
            adapter._perform = AsyncMock()
            url = 'postgres://' + newline + USER + ':' + VALUE + '@fixture.invalid/db'
            with self.assertRaises(AsyncLLMError) as caught:
                await adapter.complete((Message('user', json.dumps({'nested': {'url': url}})),), (TOOL,))
            self.assertTrue(str(caught.exception) == 'DATA_POLICY')
            adapter._perform.assert_not_called()
            await adapter.finish_cleanup()
            self.assertTrue(adapter.calls_used == 0 and not adapter.cleanup_pending)


if __name__ == '__main__':
    unittest.main()
