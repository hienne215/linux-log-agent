"""Coordinator unit seams only: no MCP Client, Bridge, SDK, model or Linux."""
import asyncio
import unittest
from agent.coordinator import Coordinator, Report


class ExecutorDouble:
    async def finish_cleanup(self):
        raise ValueError('synthetic-secret')


class ReviewUnitTests(unittest.IsolatedAsyncioTestCase):
    def make(self, **kwargs):
        return Coordinator(None, ExecutorDouble(), read_only=frozenset({'fixture_read'}),
                           scope_allows=lambda *_:True, interpret=lambda _:None,
                           redact=lambda text:text, **kwargs)

    async def test_cleanup_error_does_not_escape_as_secret(self):
        c=self.make();c.report=Report('cleanup_pending',cleanup_pending=True)
        with self.assertRaisesRegex(RuntimeError,'^CLEANUP_UNCONFIRMED$') as caught:
            await c.finish_cleanup()
        self.assertNotIn('synthetic-secret',str(caught.exception))
        self.assertTrue(c.report.cleanup_pending);self.assertFalse(c._joining)

    async def test_cleanup_budget_must_be_finite_and_bounded(self):
        for value in (True,0,-1,float('nan'),float('inf'),61):
            with self.subTest(value=value):
                with self.assertRaisesRegex(ValueError,'INVALID_CONFIG'):
                    self.make(cleanup_seconds=value)

    async def test_concurrent_cleanup_join_rejected(self):
        c=self.make();started=asyncio.Event();release=asyncio.Event()
        async def cleanup():
            started.set();await release.wait()
        c.executor.finish_cleanup=cleanup
        task=asyncio.create_task(c.finish_cleanup())
        await started.wait()
        try:
            with self.assertRaisesRegex(ValueError,'BUSY'):await c.finish_cleanup()
        finally:release.set();await task
        self.assertFalse(c._joining)

    async def test_response_wins_deadline_race_is_sanitized_and_retained(self):
        from agent.coordinator import Decision, Evidence, Observation
        class Result:
            budget_exhausted=False
        result=Result()
        class Executor:
            calls=0
            async def execute(self,*_):
                self.calls+=1
                try:await asyncio.Event().wait()
                except asyncio.CancelledError:return result
            async def finish_cleanup(self):pass
        class Model:
            calls=0
            async def choose(self,*_):
                self.calls+=1
                return Decision(tool='fixture_read',arguments={})
        executor,model=Executor(),Model()
        c=Coordinator(model,executor,read_only=frozenset({'fixture_read'}),
                      scope_allows=lambda *_:True,seconds=.01,
                      interpret=lambda _:Observation((Evidence('safe-1','fixture','synthetic-secret'),)),
                      redact=lambda text:text.replace('synthetic-secret','[REDACTED]'))
        r=await c.run('test')
        self.assertEqual(r.status,'budget_exhausted')
        self.assertEqual(r.execution_state,'response_received')
        self.assertEqual(r.evidence[0].text,'[REDACTED]')
        self.assertEqual((executor.calls,model.calls),(1,1))
        self.assertIsNone(c._operation)

    async def test_safe_result_arriving_after_cleanup_timeout_is_retained(self):
        from agent.coordinator import Decision, Evidence, Observation
        release=asyncio.Event()
        class Executor:
            async def execute(self,*_):
                try:await asyncio.Event().wait()
                except asyncio.CancelledError:await release.wait()
                return object()
            async def finish_cleanup(self):pass
        class Model:
            async def choose(self,*_):return Decision(tool='fixture_read',arguments={})
        c=Coordinator(Model(),Executor(),read_only=frozenset({'fixture_read'}),seconds=.01,cleanup_seconds=.01,
                      scope_allows=lambda *_:True,
                      interpret=lambda _:Observation((Evidence('late-1','fixture','synthetic-secret'),)),
                      redact=lambda text:text.replace('synthetic-secret','[REDACTED]'))
        report=await c.run('test')
        self.assertTrue(report.cleanup_pending)
        try:
            release.set();await c.finish_cleanup()
            self.assertEqual(c.report.evidence[0].id,'late-1')
            self.assertEqual(c.report.evidence[0].text,'[REDACTED]')
            self.assertEqual(c.report.execution_state,'response_received')
            self.assertIsNone(c._operation)
        finally:
            release.set();await c.finish_cleanup()

    async def test_late_redaction_failure_keeps_cleanup_confirmed_and_fails_closed(self):
        from agent.coordinator import Decision, Evidence, Observation
        release=asyncio.Event()
        class Executor:
            async def execute(self,*_):
                try:await asyncio.Event().wait()
                except asyncio.CancelledError:await release.wait()
                return object()
            async def finish_cleanup(self):pass
        class Model:
            async def choose(self,*_):return Decision(tool='fixture_read',arguments={})
        def redact_late(text):
            if text=='synthetic-secret':raise ValueError('synthetic-secret')
            return text
        c=Coordinator(Model(),Executor(),read_only=frozenset({'fixture_read'}),seconds=.01,cleanup_seconds=.01,
                      scope_allows=lambda *_:True,
                      interpret=lambda _:Observation((Evidence('late-1','fixture','synthetic-secret'),)),redact=redact_late)
        await c.run('test')
        release.set();await c.finish_cleanup()
        self.assertEqual(c.report.status,'data_policy')
        self.assertEqual(c.report.evidence,())
        self.assertFalse(c.report.cleanup_pending);self.assertIsNone(c._operation)
        self.assertNotIn('synthetic-secret',repr(c.report))

    async def test_late_evidence_survives_cleanup_confirmation_failure(self):
        from agent.coordinator import Decision, Evidence, Observation
        release = asyncio.Event()
        class Executor:
            confirmations = 0
            async def execute(self, *_):
                try:
                    await asyncio.Event().wait()
                except asyncio.CancelledError:
                    await release.wait()
                return object()
            async def finish_cleanup(self):
                self.confirmations += 1
                if self.confirmations == 1:
                    raise ValueError('synthetic-secret')
        class Model:
            async def choose(self, *_):
                return Decision(tool='fixture_read', arguments={})
        c = Coordinator(Model(), Executor(), read_only=frozenset({'fixture_read'}),
                        seconds=.01, cleanup_seconds=.01, scope_allows=lambda *_: True,
                        interpret=lambda _: Observation((Evidence('late-1', 'fixture', 'synthetic-secret'),)),
                        redact=lambda text: text.replace('synthetic-secret', '[REDACTED]'))
        await c.run('test')
        release.set()
        with self.assertRaisesRegex(RuntimeError, '^CLEANUP_UNCONFIRMED$'):
            await c.finish_cleanup()
        self.assertTrue(c.report.cleanup_pending)
        self.assertEqual(c.report.execution_state, 'response_received')
        self.assertEqual(c.report.evidence, (Evidence('late-1', 'fixture', '[REDACTED]'),))
        self.assertIsNone(c._operation)
        await c.finish_cleanup()
        self.assertFalse(c.report.cleanup_pending)
        self.assertEqual(c.report.evidence[0].text, '[REDACTED]')
        self.assertFalse(c._joining)
