"""Shared collector subprocess ownership; no real logs or service commands."""
import asyncio
import os
import sys
import unittest
from unittest.mock import patch

from agent.diagnostics import journal_reader as reader
from src.mcp_server import service_status, list_failed_units, listening_ports


class FakePipe:
    async def read(self,size):return b''


class FakeProcess:
    def __init__(self):
        self.stdout,self.stderr=FakePipe(),FakePipe()
        self.returncode=None
        self.killed=False
        self.waited=False
        self.release=asyncio.Event()
        self.cleanup_wait=asyncio.Event()

    def kill(self):self.killed=True

    async def wait(self):
        if self.killed:self.cleanup_wait.set()
        await self.release.wait()
        self.returncode=-9
        self.waited=True
        return self.returncode


class OwnerTests(unittest.IsolatedAsyncioTestCase):
    execute=staticmethod(reader.execute)

    async def test_pipe_error_during_cleanup_cannot_abandon_process_wait(self):
        class BrokenPipe:
            async def read(self,size):raise OSError('synthetic-pipe-error')
        process=FakeProcess();process.stdout=BrokenPipe()
        waiters=[];original_wait=process.wait
        async def wait():
            waiters.append(asyncio.current_task())
            return await original_wait()
        process.wait=wait
        async def spawn(*args,**kwargs):return process
        with patch.object(reader.asyncio,'create_subprocess_exec',spawn):
            owner=asyncio.create_task(self.execute(('synthetic-command',)))
            try:
                await process.cleanup_wait.wait()
                for _ in range(20):await asyncio.sleep(0)
                self.assertFalse(owner.done(),'pipe failure released owner before process wait completed')
                process.release.set()
                with self.assertRaises(OSError):await owner
                self.assertTrue(process.killed and process.waited)
                self.assertTrue(all(task.done() for task in waiters))
            finally:
                process.release.set();await asyncio.gather(owner,*waiters,return_exceptions=True)

    async def test_repeated_cancel_during_spawn_keeps_owner_until_process_is_reaped(self):
        process=FakeProcess();entered=asyncio.Event();release_spawn=asyncio.Event();spawn_owner=None
        async def spawn(*args,**kwargs):
            nonlocal spawn_owner
            spawn_owner=asyncio.current_task();entered.set()
            await release_spawn.wait()
            return process
        with patch.object(reader.asyncio,'create_subprocess_exec',spawn):
            task=asyncio.create_task(self.execute(('synthetic-command',)))
            try:
                await entered.wait();task.cancel()
                for _ in range(3):await asyncio.sleep(0)
                task.cancel()
                for _ in range(3):await asyncio.sleep(0)
                self.assertFalse(task.done(),'caller released before its pending spawn was joined')
                release_spawn.set();await process.cleanup_wait.wait();process.release.set()
                with self.assertRaises(asyncio.CancelledError):await task
                self.assertTrue(process.killed and process.waited)
                self.assertTrue(spawn_owner.done())
            finally:
                release_spawn.set();process.release.set()
                await asyncio.gather(task,return_exceptions=True)
                if spawn_owner is not None:await spawn_owner
                if not process.waited:process.kill();await process.wait()

    async def test_repeated_cancel_during_wait_does_not_abandon_cleanup(self):
        process=FakeProcess()
        async def spawn(*args,**kwargs):return process
        with patch.object(reader.asyncio,'create_subprocess_exec',spawn):
            task=asyncio.create_task(self.execute(('synthetic-command',)))
            try:
                for _ in range(8):await asyncio.sleep(0)
                task.cancel();await process.cleanup_wait.wait();task.cancel()
                for _ in range(3):await asyncio.sleep(0)
                self.assertFalse(task.done(),'cleanup wait was abandoned by repeated cancellation')
                process.release.set()
                with self.assertRaises(asyncio.CancelledError):await task
                self.assertTrue(process.killed and process.waited)
            finally:
                process.release.set();await asyncio.gather(task,return_exceptions=True)
                if not process.waited:process.kill();await process.wait()

    async def test_real_synthetic_child_reaped_after_cancel_during_delayed_handoff(self):
        original=asyncio.create_subprocess_exec
        entered=asyncio.Event();release=asyncio.Event();process=None;spawn_owner=None
        async def delayed(*args,**kwargs):
            nonlocal process,spawn_owner
            spawn_owner=asyncio.current_task()
            process=await original(*args,**kwargs)
            entered.set();await release.wait()
            return process
        with patch.object(reader.asyncio,'create_subprocess_exec',delayed):
            task=asyncio.create_task(self.execute((sys.executable,'-I','-c','import time; time.sleep(60)')))
            try:
                await asyncio.wait_for(entered.wait(),5)
                task.cancel()
                for _ in range(3):await asyncio.sleep(0)
                task.cancel();release.set()
                with self.assertRaises(asyncio.CancelledError):await asyncio.wait_for(task,5)
                self.assertIsNotNone(process.returncode)
                self.assertTrue(spawn_owner.done())
                with self.assertRaises(ProcessLookupError):os.kill(process.pid,0)
            finally:
                release.set();await asyncio.gather(task,return_exceptions=True)
                if spawn_owner is not None:await spawn_owner
                if process is not None and process.returncode is None:process.kill();await process.communicate()


class ServiceOwnerTests(OwnerTests):
    execute=staticmethod(service_status.run)

    async def test_request_timeout_includes_pending_spawn_but_retains_cleanup_owner(self):
        process=FakeProcess();entered=asyncio.Event();release=asyncio.Event();closing=asyncio.Event();spawns=[]
        original_close=service_status.finish_process
        async def spawn(*args,**kwargs):
            spawns.append(asyncio.current_task());entered.set();await release.wait();return process
        async def close(*args):
            closing.set();return await original_close(*args)
        with patch.object(service_status.asyncio,'create_subprocess_exec',spawn),patch.object(
                service_status,'TIMEOUT',.02),patch.object(service_status,'finish_process',close):
            owner=asyncio.create_task(self.execute(('synthetic-command',)))
            try:
                await entered.wait();await asyncio.wait_for(closing.wait(),1)
                self.assertFalse(owner.done());self.assertEqual(len(spawns),1)
                self.assertFalse(spawns[0].done());self.assertEqual(spawns[0].cancelling(),0)
                release.set();await process.cleanup_wait.wait();process.release.set()
                with self.assertRaises(service_status.CollectionFailure) as error:await owner
                self.assertEqual(error.exception.code,'TIMEOUT');self.assertTrue(process.killed and process.waited)
                self.assertTrue(all(t.done() for t in spawns))
            finally:
                release.set();process.release.set();await asyncio.gather(owner,*spawns,return_exceptions=True)


class FailedUnitsOwnerTests(OwnerTests):
    @staticmethod
    async def execute(command):
        # Only test-owned Python/fake argv are used, never the real systemctl.
        with patch.object(list_failed_units,'ARGV',command):
            return await list_failed_units._run()


class ListeningPortsOwnerTests(OwnerTests):
    @staticmethod
    async def execute(command):
        # Only test-owned Python/fake argv are used, never real socket diagnostics.
        with patch.object(listening_ports,'ARGV',command):
            return await listening_ports._run()
