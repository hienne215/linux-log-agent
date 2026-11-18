"""Fixed service_status worker; never imports synthetic business data."""
import asyncio
import json
from pathlib import Path
import signal
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from src.mcp_server.service_status import collect


async def main():
    raw = sys.stdin.buffer.read(16385)
    if len(raw) > 16384:
        return 2
    try:
        request = json.loads(raw.decode('utf-8'))
        task = asyncio.current_task()
        loop = asyncio.get_running_loop()
        if sys.platform == 'linux':
            loop.add_signal_handler(signal.SIGTERM, task.cancel)
        try:
            response = await collect(request)
        finally:
            if sys.platform == 'linux':
                loop.remove_signal_handler(signal.SIGTERM)
        sys.stdout.buffer.write(json.dumps(response, ensure_ascii=False).encode('utf-8'))
        sys.stdout.buffer.flush()
        return 0
    except (Exception, asyncio.CancelledError):
        return 2


if __name__ == '__main__':
    raise SystemExit(asyncio.run(main()))
