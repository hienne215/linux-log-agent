"""Bounded, non-streaming Ollama adapter. No tool dispatcher or retries."""
import json
from http.client import HTTPException
import math
import re
import socket
import time
from dataclasses import dataclass
from collections.abc import Sequence
from typing import Protocol
from urllib.error import HTTPError, URLError
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener

from jsonschema import Draft202012Validator, FormatChecker
from jsonschema.exceptions import SchemaError, ValidationError

from .interface import AdapterError, Completion, Message, ToolCall, ToolDefinition, Usage
from .redaction import _clean, _is_secret_key


@dataclass(frozen=True)
class OllamaConfig:
    model: str = 'qwen3:4b-instruct-2507-q4_K_M'
    endpoint: str = 'http://127.0.0.1:11434'
    num_ctx: int = 4096
    num_predict: int = 256
    temperature: float = 0
    num_thread: int = 4
    timeout_seconds: float = 45
    max_response_bytes: int = 262144
    max_request_bytes: int = 131072

    def __post_init__(self):
        # Literal loopback only: no DNS, proxies, credentials, query or custom paths.
        if self.endpoint not in ('http://127.0.0.1:11434', 'http://[::1]:11434'):
            raise AdapterError('INVALID_CONFIG')
        if not isinstance(self.model, str) or not self.model or len(self.model) > 256:
            raise AdapterError('INVALID_CONFIG')
        for value, low, high in ((self.num_ctx, 1, 4096), (self.num_predict, 1, 256),
                                 (self.num_thread, 1, 4),
                                 (self.max_response_bytes, 1024, 1048576),
                                 (self.max_request_bytes, 1024, 1048576)):
            if type(value) is not int or not low <= value <= high:
                raise AdapterError('INVALID_CONFIG')
        if (type(self.timeout_seconds) not in (int, float)
                or not math.isfinite(self.timeout_seconds) or not 0 < self.timeout_seconds <= 90
                or type(self.temperature) not in (int, float) or self.temperature != 0):
            raise AdapterError('INVALID_CONFIG')


class Transport(Protocol):
    def post(self, url: str, body: bytes, timeout: float, limit: int) -> bytes: ...


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class HTTPTransport:
    def post(self, url, body, timeout, limit):
        request = Request(url, data=body, headers={'Content-Type': 'application/json'})
        opener = build_opener(ProxyHandler({}), _NoRedirect())
        try:
            with opener.open(request, timeout=timeout) as response:
                raw = response.read(limit + 1)
                if len(raw) > limit:
                    raise AdapterError('OUTPUT_LIMIT')
                return raw
        except HTTPError as error:
            # Do not read or echo arbitrary error bodies, URLs or headers.
            code = 'MODEL_NOT_FOUND' if error.code == 404 else 'HTTP_ERROR'
            try:
                error.close()
            finally:
                raise AdapterError(code) from None
        except (TimeoutError, socket.timeout):
            raise AdapterError('TIMEOUT') from None
        except URLError as error:
            code = 'TIMEOUT' if isinstance(error.reason, TimeoutError) else 'CONNECTION_ERROR'
            raise AdapterError(code) from None
        except HTTPException:
            raise AdapterError('MALFORMED_RESPONSE') from None
        except OSError:
            raise AdapterError('CONNECTION_ERROR') from None


def _safe_json(value):
    """Native JSON only, finite numbers, bounded nesting; prohibit secret arguments."""
    def visit(node, depth=0):
        if depth > 32:
            raise AdapterError('INVALID_INPUT')
        if node is None or type(node) in (bool, int):
            return
        if type(node) is float and math.isfinite(node):
            return
        if isinstance(node, str):
            if _clean(node) != node:
                raise AdapterError('DATA_POLICY')
            return
        if isinstance(node, list):
            for item in node:
                visit(item, depth + 1)
            return
        if isinstance(node, dict) and all(isinstance(k, str) for k in node):
            for key, item in node.items():
                if _is_secret_key(key):
                    raise AdapterError('DATA_POLICY')
                visit(key, depth + 1)
                visit(item, depth + 1)
            return
        raise AdapterError('INVALID_INPUT')
    visit(value)


def _no_external_refs(node):
    if isinstance(node, dict):
        for key, value in node.items():
            if key in ('$ref', '$dynamicRef') and (not isinstance(value, str) or not value.startswith('#')):
                raise AdapterError('INVALID_SCHEMA')
            _no_external_refs(value)
    elif isinstance(node, list):
        for value in node:
            _no_external_refs(value)


def _validators(tools):
    validators = {}
    for tool in tools:
        if (not isinstance(tool, ToolDefinition) or not isinstance(tool.name, str)
                or not re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]{0,63}', tool.name)
                or tool.name in validators or not isinstance(tool.description, str)
                or not isinstance(tool.parameters, dict) or tool.parameters.get('type') != 'object'):
            raise AdapterError('INVALID_SCHEMA')
        _safe_json(tool.parameters)
        _no_external_refs(tool.parameters)
        if tool.parameters.get('$schema', 'https://json-schema.org/draft/2020-12/schema') != 'https://json-schema.org/draft/2020-12/schema':
            raise AdapterError('INVALID_SCHEMA')
        try:
            Draft202012Validator.check_schema(tool.parameters)
            validators[tool.name] = Draft202012Validator(tool.parameters, format_checker=FormatChecker())
        except SchemaError:
            raise AdapterError('INVALID_SCHEMA') from None
    return validators


def _call(call, validators):
    if (not isinstance(call, ToolCall) or not isinstance(call.name, str) or call.name not in validators
            or not isinstance(call.arguments, dict)
            or (call.provider_id is not None and (not isinstance(call.provider_id, str)
                                                 or not call.provider_id or len(call.provider_id) > 256
                                                 or _clean(call.provider_id) != call.provider_id))):
        raise AdapterError('INVALID_ARGUMENTS')
    _safe_json(call.arguments)
    try:
        validators[call.name].validate(call.arguments)
    except ValidationError:
        raise AdapterError('INVALID_ARGUMENTS') from None
    except Exception:
        # Includes unresolved local refs; never fetch external schemas.
        raise AdapterError('INVALID_SCHEMA') from None


def _pairs(pairs):
    obj = {}
    for key, value in pairs:
        if key in obj:
            raise ValueError('duplicate key')
        obj[key] = value
    return obj


class OllamaAdapter:
    def __init__(self, config=None, *, transport=None):
        self.config = config or OllamaConfig()
        self.transport = transport or HTTPTransport()

    def complete(self, messages, tools=(), *, require_tool=False):
        if type(require_tool) is not bool:
            raise AdapterError('INVALID_INPUT')
        if (not isinstance(messages, Sequence) or isinstance(messages, (str, bytes))
                or not isinstance(tools, Sequence) or isinstance(tools, (str, bytes))):
            raise AdapterError('INVALID_INPUT')
        tools = tuple(tools)
        validators = _validators(tools)
        if not messages or (require_tool and not tools):
            raise AdapterError('INVALID_INPUT')
        history, redacted = [], False
        for message in messages:
            if (not isinstance(message, Message) or message.role not in ('system', 'user', 'assistant', 'tool')
                    or (message.tool_calls and message.role != 'assistant')
                    or (message.tool_name is not None and message.role != 'tool')):
                raise AdapterError('INVALID_INPUT')
            if not isinstance(message.tool_calls, (tuple, list)):
                raise AdapterError('INVALID_INPUT')
            clean = _clean(message.content)
            redacted |= clean != message.content
            item = {'role': message.role, 'content': clean}
            if message.role == 'tool':
                if not isinstance(message.tool_name, str) or message.tool_name not in validators:
                    raise AdapterError('INVALID_INPUT')
                item['tool_name'] = message.tool_name
            if message.tool_calls:
                calls = []
                for call in message.tool_calls:
                    _call(call, validators)
                    calls.append({'function': {'name': call.name, 'arguments': call.arguments}})
                item['tool_calls'] = calls
            history.append(item)
        config = self.config
        payload = {'model': config.model, 'messages': history, 'stream': False,
                   'options': {'num_ctx': config.num_ctx, 'num_predict': config.num_predict,
                               'temperature': config.temperature, 'num_thread': config.num_thread}}
        if tools:
            payload['tools'] = [{'type': 'function', 'function': {'name': t.name,
                                'description': _clean(t.description), 'parameters': t.parameters}} for t in tools]
        # Omit think entirely for the approved non-thinking Instruct model.
        try:
            body = json.dumps(payload, ensure_ascii=False, allow_nan=False).encode('utf-8')
        except (ValueError, TypeError, UnicodeError):
            raise AdapterError('INVALID_INPUT') from None
        if len(body) > config.max_request_bytes:
            raise AdapterError('INPUT_LIMIT')
        started = time.monotonic()
        raw = self.transport.post(config.endpoint + '/api/chat', body,
                                  config.timeout_seconds, config.max_response_bytes)
        latency = time.monotonic() - started
        if not isinstance(raw, bytes):
            raise AdapterError('MALFORMED_RESPONSE')
        if len(raw) > config.max_response_bytes:
            raise AdapterError('OUTPUT_LIMIT')
        try:
            response = json.loads(raw.decode('utf-8'), object_pairs_hook=_pairs,
                                  parse_constant=lambda _: (_ for _ in ()).throw(ValueError()))
        except (ValueError, UnicodeError, RecursionError):
            raise AdapterError('MALFORMED_RESPONSE') from None
        return self._normalize(response, validators, require_tool, latency, redacted)

    def _normalize(self, response, validators, require_tool, latency, redacted):
        if not isinstance(response, dict):
            raise AdapterError('MALFORMED_RESPONSE')
        if 'error' in response:
            raise AdapterError('API_ERROR')
        if response.get('model') != self.config.model:
            raise AdapterError('MALFORMED_RESPONSE')
        if response.get('done') is not True or response.get('done_reason') != 'stop':
            raise AdapterError('OUTPUT_POLICY')
        message = response.get('message')
        if not isinstance(message, dict) or message.get('role') != 'assistant':
            raise AdapterError('MALFORMED_RESPONSE')
        text = message.get('content', '')
        thinking = message.get('thinking', '')
        if not isinstance(text, str) or not isinstance(thinking, str):
            raise AdapterError('MALFORMED_RESPONSE')
        if thinking.strip() or '<think>' in text.lower() or '</think>' in text.lower():
            raise AdapterError('OUTPUT_POLICY')
        clean = _clean(text)
        redacted |= clean != text
        native = message.get('tool_calls', [])
        if not isinstance(native, list):
            raise AdapterError('MALFORMED_RESPONSE')
        if len(native) > 1:
            raise AdapterError('OUTPUT_POLICY')
        calls = []
        for item in native:
            if not isinstance(item, dict) or not isinstance(item.get('function'), dict):
                raise AdapterError('MALFORMED_RESPONSE')
            function = item['function']
            call = ToolCall(function.get('name'), function.get('arguments'), item.get('id'))
            _call(call, validators)
            calls.append(call)
        if not calls and (require_tool or not clean.strip()):
            raise AdapterError('OUTPUT_POLICY')
        metrics = {}
        for key in ('prompt_eval_count', 'eval_count', 'eval_duration', 'total_duration'):
            value = response.get(key)
            if value is not None and (type(value) is not int or not 0 <= value <= 2**63 - 1):
                raise AdapterError('MALFORMED_RESPONSE')
            metrics[key] = value
        count, duration = metrics['eval_count'], metrics['eval_duration']
        if count is not None and count > self.config.num_predict:
            raise AdapterError('OUTPUT_POLICY')
        seconds = duration / 1e9 if duration is not None else None
        total = metrics['total_duration']
        usage = Usage(metrics['prompt_eval_count'], count, seconds,
                      total / 1e9 if total is not None else None,
                      count / seconds if count is not None and seconds else None)
        return Completion('tool_calls' if calls else 'text', clean, tuple(calls), usage,
                          latency, self.config.model, 'stop', redacted)
