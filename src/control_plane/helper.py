"""Single-owner helper candidate. No default bus, entry point or grant route.

The caller supplies an accepted bus factory and kernel-authenticated peer UID.
This module is not reachable from the read-only Agent, MCP or user CLI.
"""
import asyncio
from dataclasses import dataclass
import math

from .approval import ApprovalError
from .ledger import Attempt, HelperLedger, JobResult
from .systemd import SystemdJob


@dataclass(frozen=True)
class HelperOutcome:
    attempt: Attempt
    cleanup_pending: bool


class HelperRunner:
    def __init__(self, ledger, driver_factory):
        if type(ledger) is not HelperLedger or not callable(driver_factory):
            raise ApprovalError('INVALID_POLICY')
        self.ledger = ledger
        self._factory = driver_factory
        self._driver = None
        self._busy = self._failed = False
        self.last_attempt = None

    @property
    def cleanup_pending(self):
        return self._driver is not None

    async def finish_cleanup(self, seconds=1):
        if self._busy:
            raise ApprovalError('BUSY')
        if self._driver is None:
            return True
        self._busy = True
        try:
            if await self._driver.finish_cleanup(seconds):
                self._driver = None
                return True
            return False
        finally:
            self._busy = False

    async def execute(self, grant, *, peer_uid, seconds=30, cleanup_seconds=1):
        if (type(seconds) not in (float, int) or not math.isfinite(seconds) or not 0 < seconds <= 30
                or type(cleanup_seconds) not in (float, int) or not 0 < cleanup_seconds <= 5):
            raise ApprovalError('INVALID_POLICY')
        if self._failed:
            raise ApprovalError('STATE_UNAVAILABLE')
        if self._busy or self.cleanup_pending:
            raise ApprovalError('BUSY')
        self.ledger.codec.verify(grant, peer_uid=peer_uid)
        self._busy = True
        attempt = None
        cancelled = False
        error = None
        deadline = asyncio.get_running_loop().time() + seconds
        try:
            driver = self._factory()  # Must synchronously return the sole owned driver.
            if type(driver) is not SystemdJob:
                raise ApprovalError('INVALID_POLICY')
            self._driver = driver
            await driver.prepare(deadline)
            driver._check(deadline)
            attempt = self.ledger.consume(grant, peer_uid=peer_uid)
            self.last_attempt = attempt
            result = await driver.restart(deadline,
                lambda: self.ledger.codec.verify(grant, peer_uid=peer_uid))
        except asyncio.CancelledError:
            cancelled = True
            result = JobResult(self._driver.job_path if self._driver else None, error_code='CANCELLED')
        except Exception as exc:
            code = exc.code if type(exc) is ApprovalError else 'TRANSPORT_ERROR'
            error = ApprovalError(code if code in {
                'TIMEOUT', 'DISCONNECTED', 'PROTOCOL_ERROR', 'EXPIRED', 'TRANSPORT_ERROR',
                'STATE_UNAVAILABLE', 'REPLAY', 'UNIT_LOCKED', 'INVALID_POLICY'} else 'PROTOCOL_ERROR')
            result = JobResult(self._driver.job_path if self._driver else None,
                               error_code=code if code in {'TIMEOUT', 'DISCONNECTED', 'PROTOCOL_ERROR',
                                                         'EXPIRED', 'TRANSPORT_ERROR'} else 'TRANSPORT_ERROR')
        finally:
            try:
                if attempt is not None:
                    try:
                        self.last_attempt = self.ledger.finish(attempt.action_id, result)
                    except Exception:
                        self._failed = True
                        error = ApprovalError('STATE_UNAVAILABLE')
                if self._driver is not None and await self._driver.finish_cleanup(cleanup_seconds):
                    self._driver = None
            finally:
                self._busy = False
        if cancelled:
            raise asyncio.CancelledError()
        if error is not None and (attempt is None or self._failed):
            raise error from None
        return HelperOutcome(self.last_attempt, self.cleanup_pending)
