"""Explicit metadata-only preflight. No chat/generate/pull, SDK subprocess or inference."""
import json
from pathlib import Path
import sys
from importlib.metadata import version
from urllib.request import Request, build_opener, ProxyHandler

from agent.llm.ollama import OllamaConfig, _NoRedirect
from agent.llm.async_adapter import _decode

MIN_RAM = 1024**3
EXPECTED_VERSION = '0.40.13'
EXPECTED_DIGEST = '0edcdef34593eac1aa2be9c7d06c432dcf81945adca5eca2f27662c18f168ba0'


class PreflightError(Exception):
    def __init__(self, code):
        self.code = code
        super().__init__(code)


def available_ram():
    try:
        for line in Path('/proc/meminfo').read_text().splitlines():
            if line.startswith('MemAvailable:'):
                value = int(line.split()[1]) * 1024
                if value >= 0:
                    return value
    except Exception:
        pass
    raise PreflightError('RAM_UNAVAILABLE')


def check():
    """Only call explicitly before a separately approved live run; no inference."""
    try:
        if sys.version_info[:2] != (3, 12) or version('mcp') != '2.3.0':
            raise PreflightError('RUNTIME_MISMATCH')
        ram = available_ram()
        if ram < MIN_RAM:
            raise PreflightError('RAM_LOW')
        config = OllamaConfig()
        opener = build_opener(ProxyHandler({}), _NoRedirect())
        def read(path, body=None):
            request = Request(config.endpoint + path, data=None if body is None else json.dumps(body).encode(),
                              headers={'Content-Type': 'application/json'})
            with opener.open(request, timeout=5) as response:
                raw = response.read(1024 * 1024 + 1)
                if len(raw) > 1024 * 1024:
                    raise PreflightError('METADATA_LIMIT')
                return _decode(raw)
        current = read('/api/version')['version']
        models = read('/api/tags')['models']
        model = next((m for m in models if m.get('name') == config.model), None)
        if (current != EXPECTED_VERSION or model is None
                or model.get('digest', '').removeprefix('sha256:') != EXPECTED_DIGEST):
            raise PreflightError('MODEL_OR_VERSION_MISMATCH')
        show = read('/api/show', {'model': config.model})
        capabilities = show.get('capabilities')
        if (show.get('details', {}).get('quantization_level') != 'Q4_K_M'
                or type(capabilities) is not list or any(type(c) is not str for c in capabilities)
                or 'tools' not in capabilities):
            raise PreflightError('CAPABILITY_MISMATCH')
        ram = min(ram, available_ram())
        if ram < MIN_RAM:
            raise PreflightError('RAM_LOW')
        return {'preflight': 'PASS', 'inference_attempts': 0, 'backend': 'ollama',
                'data_source': 'SYNTHETIC MCP ONLY', 'model': config.model,
                'ollama_version': current, 'digest': EXPECTED_DIGEST,
                'available_ram_gib': ram / 1024**3,
                'limits': {'llm_requests': 2, 'request_seconds': 45, 'session_seconds': 120},
                'thinking': 'NOT VERIFIED; think omitted', 'backend_idle': 'NOT VERIFIED',
                'live_inference': 'NOT RUN'}
    except PreflightError:
        raise
    except Exception:
        raise PreflightError('METADATA_UNAVAILABLE') from None
