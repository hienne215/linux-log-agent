"""Application-owned backend selection; no URL/model/command from user data."""
from enum import Enum
import sys

from agent.llm.async_adapter import AsyncOllamaAdapter, AsyncLLMError
from agent.llm.ollama import OllamaConfig
from .fixtures import SCENARIOS
from .preflight import available_ram, MIN_RAM, PreflightError


class Backend(str, Enum):
    MOCK = 'mock'
    OLLAMA = 'ollama'


class BackendError(Exception):
    def __init__(self, code):
        self.code = code
        super().__init__(code)


def select_backend(value):
    if type(value) not in (str, Backend):
        raise BackendError('INVALID_BACKEND')
    try:
        return Backend(value)
    except ValueError:
        raise BackendError('INVALID_BACKEND') from None


class _LiveAdapter(AsyncOllamaAdapter):
    """RAM gate before each request; lifecycle/HTTP remain in the existing owner.

    This is not continuous sampling and cannot guarantee absence of transient dips.
    Failure stops this session; never substitute the mock worker or retry inference.
    """
    async def complete(self, messages, tools=(), *, require_tool=False):
        try:
            if available_ram() < MIN_RAM:
                raise PreflightError('RAM_LOW')
        except PreflightError as error:
            self._stopped = True
            self.last_error = error.code
            raise AsyncLLMError(error.code) from None
        return await super().complete(messages, tools, require_tool=require_tool)


def create_adapter(backend=Backend.MOCK, *, scenario='active', seconds=120, _mock_fault='none'):
    """Construction never starts a worker, HTTP, metadata query or inference."""
    mode = select_backend(backend)
    if scenario not in SCENARIOS:
        raise BackendError('INVALID_SCENARIO')
    if mode is Backend.OLLAMA:
        if scenario != 'active' or _mock_fault != 'none':
            raise BackendError('LIVE_SCOPE_DENIED')
        # Fixed original worker/config, 45s/request including startup, <=2 calls.
        return _LiveAdapter(OllamaConfig(), seconds=seconds, max_calls=2)
    return AsyncOllamaAdapter(OllamaConfig(), seconds=seconds, _worker_command=(
        sys.executable, '-B', '-m', 'agent.integration_demo.mock_worker', scenario, _mock_fault))
