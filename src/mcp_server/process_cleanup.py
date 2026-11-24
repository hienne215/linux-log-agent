"""Join only subprocess resources already owned by a collector.

No command selection, process discovery, shell, retry or privilege changes.
Cleanup is separate from acquisition deadlines and has no hard wall guarantee.
"""
import asyncio


async def _discard(stream):
    while await stream.read(4096):
        pass


async def _close(spawn, process, readers):
    if process is None:
        try:
            process = await spawn
        except Exception:
            return  # Failed spawn allocated no process to reap.
    try:
        if process.returncode is None:
            try:
                process.kill()
            except ProcessLookupError:
                pass
    finally:
        for task in readers:
            task.cancel()
        await asyncio.gather(*readers, return_exceptions=True)
        # A pipe failure must not release the separate wait/reap owner early.
        closed = await asyncio.gather(_discard(process.stdout), _discard(process.stderr),
                                      process.wait(), return_exceptions=True)
        if isinstance(closed[-1], BaseException):
            raise closed[-1]


async def finish_process(spawn, process, readers):
    """Retain and join cleanup, including an in-flight spawn, on repeated cancel."""
    cleanup = asyncio.create_task(_close(spawn, process, tuple(readers)),
                                  name='collector-process-cleanup')
    cancelled = False
    while True:
        try:
            await asyncio.shield(cleanup)
            break
        except asyncio.CancelledError:
            if cleanup.cancelled():
                raise
            cancelled = True
    if cancelled:
        raise asyncio.CancelledError
