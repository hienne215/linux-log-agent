"""Bounded direct-child execution for application-owned collector workers."""
import asyncio
import json
import os
import time
from pathlib import Path


class WorkerFailure(Exception):
    """Only a safe code crosses this boundary; never a child output/exception."""

    def __init__(self, code):
        self.code = code
        super().__init__(code)


class WorkerRunner:
    def __init__(self, timeout=10.0, stdout_limit=65536, stderr_limit=4096):
        if timeout <= 0 or not 0 < stdout_limit <= 65536 or not 0 < stderr_limit <= 4096:
            raise ValueError("Invalid worker resource policy")
        self.timeout = timeout
        self.stdout_limit = stdout_limit
        self.stderr_limit = stderr_limit
        self.lock = asyncio.Lock()
        self.cleanup_lock = asyncio.Lock()
        self.process = None
        self.spawn = None
        self.io_tasks = []
        self.drain_task = None
        self.closing = False

    async def _read(self, stream, limit, keep):
        data = bytearray()
        count = 0
        while chunk := await stream.read(min(4096, limit + 1)):
            count += len(chunk)
            if count > limit:
                raise WorkerFailure("OUTPUT_LIMIT")
            if keep:
                data.extend(chunk)
        return bytes(data)

    async def finish_cleanup(self):
        """Retain ownership and refuse new work until spawn/exit is confirmed."""
        async with self.cleanup_lock:
            return await self._finish_cleanup()

    async def _finish_cleanup(self):
        if self.spawn is not None:
            try:
                process = await asyncio.wait_for(asyncio.shield(self.spawn), 1.0)
            except asyncio.TimeoutError:
                return False
            except Exception:
                self.spawn = None
            else:
                self.process = process
                self.spawn = None
        process = self.process
        if process is None:
            return True
        for task in self.io_tasks:
            task.cancel()
        if self.io_tasks:
            await asyncio.gather(*self.io_tasks, return_exceptions=True)
        self.io_tasks = []

        async def discard(stream):
            if stream is not None:
                while await stream.read(4096):
                    pass

        async def drain_pipes():
            await asyncio.gather(discard(getattr(process, "stdout", None)),
                                 discard(getattr(process, "stderr", None)))
            stdin = getattr(process, "stdin", None)
            if stdin is not None:
                stdin.close()
                try:
                    await stdin.wait_closed()
                except (BrokenPipeError, ConnectionResetError):
                    pass

        if self.drain_task is None:
            # Keep reading/discarding after killing a flooder, without buffering secrets.
            # wait() alone does not prove pipe transports have reached EOF.
            self.drain_task = asyncio.create_task(drain_pipes())
        if process.returncode is None:
            try:
                process.terminate()
            except ProcessLookupError:
                pass
            try:
                await asyncio.wait_for(process.wait(), 0.25)
            except asyncio.TimeoutError:
                try:
                    process.kill()
                except ProcessLookupError:
                    pass
                try:
                    await asyncio.wait_for(process.wait(), 1.0)
                except asyncio.TimeoutError:
                    return False
        try:
            await asyncio.wait_for(asyncio.shield(self.drain_task), 1.0)
        except (asyncio.TimeoutError, OSError, RuntimeError):
            return False
        self.drain_task = None
        self.process = None
        return True

    async def close(self):
        self.closing = True
        try:
            return await self.finish_cleanup()
        except OSError:
            return False

    async def run(self, argv, arguments):
        # This is not an MCP shell tool. argv comes only from trusted server code.
        if (not argv or not os.path.isabs(argv[0])
                or not all(isinstance(arg, str) and "\x00" not in arg for arg in argv)):
            raise WorkerFailure("POLICY_DENIED")
        if self.closing or self.lock.locked() or self.process is not None or self.spawn is not None:
            raise WorkerFailure("POLICY_DENIED")
        payload = json.dumps(arguments, ensure_ascii=False).encode("utf-8")
        if len(payload) > 16384:
            raise WorkerFailure("OUTPUT_LIMIT")
        async with self.lock:
            deadline = time.monotonic() + self.timeout
            tasks = []
            # No inherited API keys/PYTHONPATH; executable and cwd are code-owned.
            env = {key: os.environ[key] for key in ("SystemRoot", "WINDIR") if key in os.environ}
            env.update(PYTHONIOENCODING="utf-8", PYTHONUTF8="1")
            self.spawn = asyncio.create_task(asyncio.create_subprocess_exec(
                *argv, stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE, env=env,
                cwd=str(Path(__file__).resolve().parents[2])))
            try:
                self.process = await asyncio.wait_for(asyncio.shield(self.spawn), self.timeout)
                self.spawn = None
                process = self.process

                async def send():
                    process.stdin.write(payload)
                    await process.stdin.drain()
                    process.stdin.close()

                tasks = [asyncio.create_task(send()),
                         asyncio.create_task(self._read(process.stdout, self.stdout_limit, True)),
                         asyncio.create_task(self._read(process.stderr, self.stderr_limit, False)),
                         asyncio.create_task(process.wait())]
                self.io_tasks = tasks
                remaining = max(0.0, deadline - time.monotonic())
                _, stdout, _, returncode = await asyncio.wait_for(asyncio.gather(*tasks), remaining)
                if returncode != 0:
                    raise WorkerFailure("EXECUTION_UNKNOWN")
                return stdout
            except asyncio.TimeoutError:
                raise WorkerFailure("TIMEOUT") from None
            except PermissionError:
                raise WorkerFailure("PERMISSION_DENIED") from None
            except (OSError, ValueError):
                raise WorkerFailure("EXECUTION_UNKNOWN") from None
            finally:
                try:
                    cleaned = await asyncio.shield(self.finish_cleanup())
                except OSError:
                    cleaned = False
                if not cleaned:
                    self.closing = True
