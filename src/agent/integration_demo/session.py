"""Application wiring for explicit synthetic fixtures, not a production binder."""
import asyncio
from dataclasses import replace
import json
from pathlib import Path
import sys
import time

from agent.coordinator import Coordinator, Evidence, Observation, Report
from agent.integration_boundary import ToolBridge, TrustedTool, BridgeBudget
from agent.llm.async_adapter import AsyncOllamaAdapter
from agent.llm.decision_source import AsyncDecisionSource, run_coordinator
from agent.llm.interface import ToolDefinition
from agent.llm.redaction import _clean
from agent.mcp_client import StdioMCPClient, StdioConfig
from .fixtures import SCENARIOS, TOOLS, PARAMETERS, steps, observation


class DemoError(Exception):
    def __init__(self, code):
        self.code = code
        super().__init__(code)


def interpret(message, scenario):
    """Require this fixture's complete provenance; never manufacture missing IDs."""
    data = json.loads(message.content)['structuredContent']
    if (message.is_error or type(data) is not dict
            or data.get('tool') != message.tool_name or data.get('tool') not in steps(scenario)
            or data != observation(scenario, data['tool'])):
        raise DemoError('INVALID_SYNTHETIC_EVIDENCE')
    return Observation(tuple(Evidence(e['id'], e['source'], e['text']) for e in data['evidence']),
                       data['limited'], 'SYNTHETIC DEMO — no Linux observation')


class DemoSession:
    """Retain every owner through pending cleanup. No inference/network backend choice.

    Underscore fault controls are trusted test seams, never CLI/model arguments.
    """
    def __init__(self, scenario, *, seconds=120, _model_fault='none', _server_fault='none',
                 _request_seconds=10, _max_turns=6):
        if scenario not in SCENARIOS:
            raise DemoError('INVALID_SCENARIO')
        self.deadline = time.monotonic() + seconds
        self.scenario = scenario
        names = frozenset(steps(scenario))
        definitions = tuple(ToolDefinition(n, TOOLS[n], PARAMETERS) for n in steps(scenario))
        self.adapter = AsyncOllamaAdapter(seconds=seconds, _worker_command=(
            sys.executable, '-B', '-m', 'agent.integration_demo.mock_worker', scenario, _model_fault))
        self.model = AsyncDecisionSource(self.adapter, definitions, read_only=names)
        self.client = StdioMCPClient(StdioConfig(command=sys.executable,
            args=('-B', str(Path(__file__).with_name('server.py').resolve()), '--scenario', scenario,
                  '--fault', _server_fault), allowed_tools=names, request_timeout=_request_seconds))
        self.bridge = ToolBridge(self.client, tuple(TrustedTool(t.name, t.description, t.parameters, True)
                                                   for t in definitions), redact=_clean,
                                 budget=BridgeBudget(max_calls=8, total_seconds=seconds,
                                                     request_seconds=_request_seconds))
        self.coordinator = Coordinator(self.model, self.bridge, read_only=names,
            scope_allows=lambda name,args: name in names and args == {'unit': 'nginx.service'},
            interpret=lambda message: interpret(message, scenario), redact=_clean,
            seconds=seconds, max_turns=_max_turns)
        self._cleanup_owner = None
        self._cleanup_ok = False
        self.cleanup_seconds = 8
        self.discovered = ()
        self.started = False

    @property
    def cleanup_pending(self):
        return (self._cleanup_owner is not None or not self._cleanup_ok
                or self.adapter.cleanup_pending or self.client._owner is not None
                or self.coordinator._operation is not None or self.bridge.has_pending_task)

    async def _cleanup_round(self):
        failures = False
        # Always attempt all joins, even when one resource needs another round.
        for close in (self.model.finish_cleanup, self.coordinator.finish_cleanup, self.client.disconnect):
            try:
                await close()
            except Exception:
                failures = True
            finally:
                # Coordinator's executor-only join may clear its own flag while
                # the model/client is still pending. Session owns the full flag.
                if self.coordinator.report is not None:
                    self.coordinator.report = replace(self.coordinator.report, cleanup_pending=True)
        return not failures

    async def finish_cleanup(self):
        if self.coordinator.report is not None:
            self.coordinator.report = replace(self.coordinator.report, cleanup_pending=True)
        if self._cleanup_owner is None:
            self._cleanup_owner = asyncio.create_task(self._cleanup_round(), name='demo-cleanup-owner')
        cancelled = False
        end = time.monotonic() + self.cleanup_seconds
        while not self._cleanup_owner.done():
            remaining = end - time.monotonic()
            if remaining <= 0:
                if self.coordinator.report is not None:
                    self.coordinator.report = replace(self.coordinator.report, cleanup_pending=True)
                raise DemoError('CLEANUP_PENDING')
            try:
                await asyncio.wait({self._cleanup_owner}, timeout=remaining)
            except asyncio.CancelledError:
                cancelled = True  # Keep the cleanup owner alive, never cancel it.
        self._cleanup_ok = self._cleanup_owner.result()
        self._cleanup_owner = None
        report = self.coordinator.report
        if report is not None:
            self.coordinator.report = replace(report, cleanup_pending=not self._cleanup_ok)
        if not self._cleanup_ok:
            raise DemoError('CLEANUP_PENDING')
        if cancelled:
            raise asyncio.CancelledError

    async def run(self, question):
        if self.started:
            raise DemoError('SINGLE_USE')
        self.started = True
        try:
            question = _clean(question)  # Before worker IPC; no question in server arguments.
            remaining = self.deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError
            async def initialize():
                await self.client.connect()
                return await self.client.list_tools()
            page = await asyncio.wait_for(initialize(), remaining)
            self.discovered = tuple(t['name'] for t in page.tools)
            if page.next_cursor is not None or not set(steps(self.scenario)) <= set(self.discovered):
                raise DemoError('INVALID_REGISTRY')
            remaining = self.deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError
            self.coordinator.seconds = remaining
            await run_coordinator(self.coordinator, question)
        except TimeoutError:
            self.coordinator.report = self.coordinator.report or Report('budget_exhausted')
        except asyncio.CancelledError:
            if self.coordinator.report is None:
                self.coordinator.report = Report('cancelled')
            raise
        except Exception:
            if self.coordinator.report is None:
                self.coordinator.report = Report('disconnected')
            raise DemoError('DEMO_FAILED') from None
        finally:
            await self.finish_cleanup()
        return self.coordinator.report
