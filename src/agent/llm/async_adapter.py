"""Owned subprocess boundary around the existing sync Adapter; no threads/retry."""
import asyncio
from dataclasses import asdict, fields
import json
import math
import os
from pathlib import Path
import sys
import time

from .interface import AdapterError, Completion, Message, ToolCall, ToolDefinition, Usage
from .ollama import OllamaConfig, _validators, _call, _pairs
from .redaction import _clean

MAX_IPC = 1024 * 1024
SAFE_CODES = frozenset({'INVALID_CONFIG', 'INVALID_INPUT', 'INVALID_SCHEMA', 'INVALID_ARGUMENTS',
                       'DATA_POLICY', 'INPUT_LIMIT', 'OUTPUT_LIMIT', 'OUTPUT_POLICY',
                       'MALFORMED_RESPONSE', 'MODEL_NOT_FOUND', 'HTTP_ERROR', 'API_ERROR',
                       'TIMEOUT', 'CONNECTION_ERROR', 'WORKER_FAILURE'})


class AsyncLLMError(Exception):
    def __init__(self, code):
        self.code = code
        self.layer = ('transport' if code in {'CONNECTION_ERROR', 'HTTP_ERROR', 'WORKER_FAILURE'}
                      else 'model' if code in {'MODEL_NOT_FOUND', 'API_ERROR', 'OUTPUT_POLICY'}
                      else 'timeout' if code in {'TIMEOUT', 'DEADLINE_EXCEEDED'}
                      else 'lifecycle' if code in {'CLEANUP_PENDING', 'BUSY', 'STOPPED'}
                      else 'protocol')
        super().__init__(code)


class LLMTimeout(AsyncLLMError, TimeoutError):
    pass


def _decode(raw):
    try:
        return json.loads(raw, object_pairs_hook=_pairs,
                          parse_constant=lambda _: (_ for _ in ()).throw(ValueError()))
    except Exception:
        raise AsyncLLMError('MALFORMED_RESPONSE') from None


def _completion(raw, tools, config):
    """Revalidate the bounded, normalized child message before any dispatch."""
    try:
        packet = _decode(raw)
        if type(packet) is not dict or type(packet.get('ok')) is not bool:
            raise ValueError()
        if packet['ok'] is False:
            code = packet.get('code')
            if code not in SAFE_CODES:
                raise ValueError()
            raise LLMTimeout(code) if code == 'TIMEOUT' else AsyncLLMError(code)
        if set(packet) != {'ok', 'completion'}:
            raise ValueError()
        item = packet['completion']
        if (type(item) is not dict or set(item) != {f.name for f in fields(Completion)} or item['kind'] not in ('text', 'tool_calls')
                or type(item['text']) is not str or type(item['tool_calls']) is not list
                or item['model'] != config.model or item['done_reason'] != 'stop'
                or type(item['redacted']) is not bool):
            raise ValueError()
        validators = _validators(tools)
        calls = tuple(ToolCall(**call) for call in item['tool_calls'])
        if len(calls) > 1 or (bool(calls) != (item['kind'] == 'tool_calls')):
            raise ValueError()
        for call in calls:
            _call(call, validators)
        usage = Usage(**item['usage'])
        for value in (usage.prompt_tokens, usage.output_tokens):
            if value is not None and (type(value) is not int or not 0 <= value <= 2**63-1):
                raise ValueError()
        for value in (usage.generation_seconds, usage.provider_total_seconds, usage.tokens_per_second):
            if value is not None and (type(value) not in (int, float) or not math.isfinite(value) or value < 0):
                raise ValueError()
        latency = item['latency_seconds']
        if (type(latency) not in (int, float) or not math.isfinite(latency) or latency < 0
                or (usage.output_tokens is not None and usage.output_tokens > config.num_predict)):
            raise ValueError()
        text = _clean(item['text'])
        if not calls and not text.strip():
            raise ValueError()
        return Completion(item['kind'], text, calls, usage, latency, item['model'], 'stop', item['redacted'])
    except AsyncLLMError:
        raise
    except Exception:
        raise AsyncLLMError('MALFORMED_RESPONSE') from None


class AsyncOllamaAdapter:
    """Single-flight, <=6 requests/120s; one worker, bounded IPC and cleanup join.

    _worker_command is a trusted test seam, never model/user input. A caller must
    retain this object and rejoin finish_cleanup if cleanup_pending is true.
    """
    def __init__(self, config=None, *, seconds=120, max_calls=6, cleanup_seconds=2,
                 terminate_seconds=.25, _worker_command=None):
        self.config = config or OllamaConfig()
        if not isinstance(self.config, OllamaConfig):
            raise AsyncLLMError('INVALID_CONFIG')
        for value, cap in ((seconds, 120), (cleanup_seconds, 30), (terminate_seconds, 5)):
            if type(value) not in (int, float) or not math.isfinite(value) or not 0 < value <= cap:
                raise AsyncLLMError('INVALID_CONFIG')
        if type(max_calls) is not int or not 1 <= max_calls <= 6:
            raise AsyncLLMError('INVALID_CONFIG')
        self.deadline = time.monotonic() + seconds
        self.max_calls, self.calls_used = max_calls, 0
        self.cleanup_seconds, self.terminate_seconds = cleanup_seconds, terminate_seconds
        self._command = tuple(_worker_command or (sys.executable, '-B', '-m', 'agent.llm._async_worker'))
        self._owner = self._process = self._abort = None
        self._busy = self._joining = self._stopped = False
        self.last_error = None
        self.last_returncode = None
        self.last_cleanup_error = None
        self._cleanup_retry = None

    @property
    def cleanup_pending(self):
        return self._owner is not None

    @property
    def worker_pid(self):
        return self._process.pid if self._process else None

    async def _exchange(self, process, payload):
        process.stdin.write(payload)
        await process.stdin.drain()
        process.stdin.close()
        raw = bytearray()
        while True:
            chunk = await process.stdout.read(min(65536, MAX_IPC + 1 - len(raw)))
            if not chunk:
                break
            raw.extend(chunk)
            if len(raw) > MAX_IPC:
                raise AsyncLLMError('OUTPUT_LIMIT')
        await process.wait()
        if process.returncode != 0:
            raise AsyncLLMError('WORKER_FAILURE')
        return bytes(raw)

    async def _stop(self, process):
        if process.returncode is not None:
            return
        waiter = asyncio.create_task(process.wait())
        try:
            try:
                process.terminate()
            except ProcessLookupError:
                pass
            done, _ = await asyncio.wait({waiter}, timeout=self.terminate_seconds)
            if not done:
                try:
                    process.kill()
                except ProcessLookupError:
                    pass
            # Owner retains this join even when caller's cleanup budget expires.
            await waiter
        finally:
            if not waiter.done():
                waiter.cancel()
                await asyncio.gather(waiter, return_exceptions=True)

    async def _perform(self, payload, abort):
        process = exchange = stopper = None
        try:
            if abort.is_set():
                raise AsyncLLMError('STOPPED')
            env = {'PATH': os.defpath, 'PYTHONDONTWRITEBYTECODE': '1',
                   'PYTHONPATH': str(Path(__file__).resolve().parents[2])}
            process = await asyncio.create_subprocess_exec(*self._command, stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL, env=env, limit=65536)
            self._process = process
            if abort.is_set():
                raise AsyncLLMError('STOPPED')
            exchange = asyncio.create_task(self._exchange(process, payload))
            stopper = asyncio.create_task(abort.wait())
            await asyncio.wait({exchange, stopper}, return_when=asyncio.FIRST_COMPLETED)
            if abort.is_set():
                raise AsyncLLMError('STOPPED')
            return await exchange
        finally:
            if process is not None:
                # A teardown exception must not end the owner while its process
                # or pipe tasks remain live. Explicit finish_cleanup retries it.
                while True:
                    self._cleanup_retry.clear()
                    try:
                        await self._stop(process)
                        if process.returncode is None:
                            raise OSError('UNCONFIRMED')
                        self.last_cleanup_error = None
                        break
                    except Exception:
                        self.last_cleanup_error = 'CLEANUP_FAILED'
                        await self._cleanup_retry.wait()
                self.last_returncode = process.returncode
            for task in (exchange, stopper):
                if task is not None and not task.done():
                    task.cancel()
            await asyncio.gather(*(t for t in (exchange, stopper) if t is not None), return_exceptions=True)

    def _release(self):
        task = self._owner
        if task is not None and task.done():
            # Retrieve exceptions; never log payloads/exception text.
            try:
                task.result()
            except BaseException:
                pass
            self._owner = self._process = self._abort = self._cleanup_retry = None

    async def _join(self):
        end = time.monotonic() + self.cleanup_seconds
        cancelled = False
        while self._owner is not None and not self._owner.done():
            remaining = end - time.monotonic()
            if remaining <= 0:
                raise AsyncLLMError('CLEANUP_PENDING') from None
            try:
                await asyncio.wait({self._owner}, timeout=remaining)
            except asyncio.CancelledError:
                cancelled = True  # Never cancel the owner a second time.
        self._release()
        return cancelled

    async def finish_cleanup(self):
        if self._busy or self._joining:
            raise AsyncLLMError('BUSY')
        self._joining = True
        try:
            if self._owner is not None:
                self._stopped = True
                self._abort.set()
                self._cleanup_retry.set()
            cancelled = await self._join()
        finally:
            self._joining = False
        if cancelled:
            raise asyncio.CancelledError from None

    async def complete(self, messages, tools=(), *, require_tool=False):
        if self._busy or self._owner is not None:
            raise AsyncLLMError('BUSY')
        if self._stopped:
            raise AsyncLLMError('STOPPED')
        remaining = self.deadline - time.monotonic()
        if remaining <= 0 or self.calls_used >= self.max_calls:
            raise LLMTimeout('DEADLINE_EXCEEDED')
        try:
            if (type(require_tool) is not bool or not isinstance(messages, (tuple, list))
                    or not messages or any(type(m) is not Message for m in messages)
                    or not isinstance(tools, (tuple, list))):
                raise AsyncLLMError('INVALID_INPUT')
            tools = tuple(ToolDefinition(t.name, _clean(t.description),
                          json.loads(json.dumps(t.parameters, allow_nan=False))) for t in tools)
            validators = _validators(tools)
            # Redact text before IPC. Child reuses original Adapter validation.
            clean = []
            for message in messages:
                for call in message.tool_calls:
                    _call(call, validators)  # reject secret arguments, never rewrite them
                item = asdict(message)
                item['content'] = _clean(message.content)
                clean.append(item)
            config = asdict(self.config)
            config['timeout_seconds'] = min(self.config.timeout_seconds, remaining)
            payload = json.dumps({'config': config, 'messages': clean,
                                  'tools': [asdict(t) for t in tools], 'require_tool': require_tool},
                                 ensure_ascii=False, allow_nan=False).encode()
            if len(payload) > MAX_IPC:
                raise AsyncLLMError('INPUT_LIMIT')
        except AsyncLLMError:
            raise
        except AdapterError as error:
            raise AsyncLLMError(error.code if error.code in SAFE_CODES else 'INVALID_INPUT') from None
        except Exception:
            raise AsyncLLMError('INVALID_INPUT') from None
        if time.monotonic() >= self.deadline:
            raise LLMTimeout('DEADLINE_EXCEEDED')
        self._busy = True
        self.calls_used += 1
        self._abort = asyncio.Event()
        self._cleanup_retry = asyncio.Event()
        self.last_error = self.last_returncode = self.last_cleanup_error = None
        self._owner = asyncio.create_task(self._perform(payload, self._abort), name='llm-worker-owner')
        cancelled = False
        try:
            end = min(self.deadline, time.monotonic() + self.config.timeout_seconds)
            done, _ = await asyncio.wait({self._owner}, timeout=max(0, end-time.monotonic()))
            if not done or time.monotonic() >= end:
                self._stopped = True
                self._abort.set()
                self.last_error = 'DEADLINE_EXCEEDED' if time.monotonic() >= self.deadline else 'TIMEOUT'
                cancelled = await self._join()
                if cancelled:
                    raise asyncio.CancelledError
                raise LLMTimeout(self.last_error)
            raw = self._owner.result()
            result = _completion(raw, tools, self.config)
            if time.monotonic() >= min(end, self.deadline):
                self._stopped = True
                raise LLMTimeout('DEADLINE_EXCEEDED' if time.monotonic() >= self.deadline else 'TIMEOUT')
            if require_tool and result.kind != 'tool_calls':
                raise AsyncLLMError('OUTPUT_POLICY')
            return result
        except asyncio.CancelledError:
            self.last_error, self._stopped = 'CANCELLED', True
            if self._abort is not None:
                self._abort.set()
            try:
                await self._join()
            except AsyncLLMError:
                pass  # Explicit pending owner remains reachable; caller must rejoin.
            raise asyncio.CancelledError from None
        except AsyncLLMError as error:
            if error.code != 'CLEANUP_PENDING':
                self.last_error = error.code
            raise
        except Exception:
            self.last_error = 'WORKER_FAILURE'
            raise AsyncLLMError('WORKER_FAILURE') from None
        finally:
            self._release()
            self._busy = False
