"""Real PTY input guards; no human-identity, backend or execution claim."""
import io
import os
import select
import termios
import threading
import time
import unittest
from unittest.mock import patch

from control_plane.approval import ApprovalError
from control_plane.review import PendingReview


def record(**changes):
    value = dict(action_id='a' * 32, session_id='b' * 32, trace_id='trace-1',
        environment_id='synthetic-lab', unit='nginx.service', evidence_ids=['synthetic-evidence'],
        state='pending', expires_at='2026-01-01T00:01:00.000000Z', remaining_ms=1000,
        execution_enabled=False, executed=None)
    return {**value, **changes}


class ReviewTests(unittest.TestCase):
    def review(self, **changes):
        return PendingReview({**record(), **changes}, request_started=time.monotonic())

    def tty(self, view, answer, *, prequeue=None):
        master, slave = os.openpty()
        original = termios.tcgetattr(slave)
        captured = bytearray()
        errors = []
        def driver():
            try:
                deadline = time.monotonic() + 3
                while time.monotonic() < deadline:
                    if select.select([master], [], [], .02)[0]:
                        captured.extend(os.read(master, 8192))
                        if b'nh\xe1\xba\xadp secrets' in captured:
                            if answer is not None:
                                value = answer(view) if callable(answer) else answer
                                os.write(master, value.encode() if isinstance(value, str) else value)
                            return
                raise AssertionError('PROMPT_NOT_SEEN')
            except Exception as error:
                errors.append(type(error).__name__)
        worker = threading.Thread(target=driver)
        try:
            if prequeue:
                os.write(master, prequeue)
                termios.tcflush(slave, termios.TCOFLUSH)  # discard any prior echo
            with os.fdopen(os.dup(slave), 'r') as stdin, os.fdopen(os.dup(slave), 'w') as stdout:
                worker.start()
                decision = view.confirm(stdin=stdin, stdout=stdout)
                worker.join(4)
                self.assertFalse(worker.is_alive())
                self.assertEqual(errors, [])
                self.assertFalse(view.cleanup_pending)
                self.assertEqual(termios.tcgetattr(slave), original)
                while select.select([master], [], [], .01)[0]:
                    captured.extend(os.read(master, 8192))
            return decision, captured.decode(errors='replace')
        finally:
            if worker.ident is not None:
                worker.join(4)
            os.close(master)
            os.close(slave)

    def test_real_tty_confirmation_bound_to_backend_action_no_grant(self):
        view = self.review()
        decision, output = self.tty(view, lambda v: 'DONG_Y ' + v.action_id + '\n')
        self.assertEqual(decision, 'confirmed')
        self.assertIn('nginx.service', output)
        self.assertIn('approved không có nghĩa executed', output)
        self.assertNotIn('DONG_Y ' + view.action_id + '\r\n', output)
        self.assertFalse(hasattr(view, 'grant'))

    def test_reject_eof_and_wrong_action_fail_closed(self):
        for answer, expected in ((lambda v: 'TU_CHOI ' + v.action_id + '\n', 'rejected'),
                                 (b'\x04', 'eof'), ('DONG_Y ' + 'c' * 32 + '\n', 'invalid_input')):
            with self.subTest(expected=expected):
                decision, _ = self.tty(self.review(), answer)
                self.assertEqual(decision, expected)

    def test_secret_oversize_nonascii_and_automatic_yes_not_echoed(self):
        for answer in (b'password=synthetic-canary\n', b'x' * 200 + b'\n', b'\xff\n', b'yes\n'):
            decision, output = self.tty(self.review(), answer)
            self.assertEqual(decision, 'invalid_input')
            self.assertNotIn('synthetic-canary', output)

    def test_queued_approval_is_discarded_and_timeout_restores_tty(self):
        view = self.review(remaining_ms=100)
        decision, _ = self.tty(view, None, prequeue=('DONG_Y ' + view.action_id + '\n').encode())
        self.assertEqual(decision, 'expired')

    def test_non_tty_does_not_read_or_obey_environment(self):
        view = self.review()
        source = io.StringIO('DONG_Y ' + view.action_id + '\n')
        with patch.object(source, 'read', side_effect=AssertionError('NO_READ')), \
                patch.dict(os.environ, {'AUTO_APPROVE': 'true'}):
            self.assertEqual(view.confirm(stdin=source, stdout=io.StringIO()), 'non_tty')

    def test_expired_or_terminal_state_does_not_prompt(self):
        for changes, expected in (({'remaining_ms': 0}, 'expired'), ({'state': 'approved'}, 'state_final'),
                                  ({'state': 'denied'}, 'state_final'), ({'state': 'expired'}, 'state_final')):
            view = self.review(**changes)
            output = io.StringIO()
            self.assertEqual(view.confirm(stdin=io.StringIO(), stdout=output), expected)
            self.assertEqual(output.getvalue(), '')

    def test_ttl_includes_network_time_and_does_not_follow_wall_clock(self):
        started = time.monotonic() - 2
        view = PendingReview(record(), request_started=started)
        self.assertEqual(view.confirm(stdin=io.StringIO(), stdout=io.StringIO()), 'expired')

    def test_snapshot_readonly_and_input_list_copied(self):
        data = record()
        view = PendingReview(data, request_started=time.monotonic())
        data['evidence_ids'].append('forged')
        self.assertEqual(view.evidence_ids, ('synthetic-evidence',))
        for name, value in (('action_id', 'c' * 32), ('decision', 'confirmed'), ('state', 'approved')):
            with self.assertRaises(AttributeError):
                setattr(view, name, value)

    def test_malformed_or_injected_view_fails_before_display(self):
        for changes in ({'unit': 'ssh.service'}, {'reason': 'password=synthetic-canary'},
                        {'grant': 'forged'}, {'action_id': 'x\x1b[2J'}, {'evidence_ids': ['a', 'a']},
                        {'evidence_ids': ['bad\ninput']}, {'evidence_ids': []}, {'remaining_ms': True},
                        {'remaining_ms': 60001}, {'expires_at': 'not-utc'}, {'executed': True},
                        {'execution_enabled': True}, {'state': 'succeeded'}, {'environment_id': 'lab\nroot'}):
            with self.subTest(changes=changes), self.assertRaises(ApprovalError) as error:
                self.review(**changes)
            self.assertEqual(str(error.exception), 'INVALID_RESPONSE')

    def test_cancel_restores_terminal_and_never_confirms(self):
        master, slave = os.openpty()
        original = termios.tcgetattr(slave)
        try:
            view = self.review()
            with os.fdopen(os.dup(slave), 'r') as stdin, os.fdopen(os.dup(slave), 'w') as stdout, \
                    patch('control_plane.review.selectors.DefaultSelector.select', side_effect=KeyboardInterrupt):
                self.assertEqual(view.confirm(stdin=stdin, stdout=stdout), 'cancelled')
            self.assertEqual(termios.tcgetattr(slave), original)
            self.assertFalse(view.cleanup_pending)
        finally:
            os.close(master)
            os.close(slave)

    def test_restore_failure_retains_owner_and_suppresses_confirmation(self):
        master, slave = os.openpty()
        original = termios.tcgetattr(slave)
        view = self.review()
        real = termios.tcsetattr
        calls = []
        def failing_restore(*args):
            calls.append(1)
            if len(calls) > 1:
                raise OSError('synthetic-canary')
            return real(*args)
        try:
            with os.fdopen(os.dup(slave), 'r') as stdin, os.fdopen(os.dup(slave), 'w') as stdout, \
                    patch('control_plane.review.termios.tcsetattr', side_effect=failing_restore), \
                    patch('control_plane.review.selectors.DefaultSelector.select', return_value=[]):
                self.assertEqual(view.confirm(stdin=stdin, stdout=stdout), 'terminal_error')
            self.assertTrue(view.cleanup_pending)
            owned = view._terminal[0]
            os.fstat(owned)  # caller's stdin/stdout wrappers have already closed
            view.finish_cleanup()
            self.assertFalse(view.cleanup_pending)
            with self.assertRaises(OSError):
                os.fstat(owned)
            self.assertEqual(termios.tcgetattr(slave), original)
            self.assertEqual(view.confirm(), 'terminal_error')
        finally:
            real(slave, termios.TCSANOW, original)
            os.close(master)
            os.close(slave)

    def test_initial_terminal_read_failure_does_not_leak_duplicate(self):
        master, slave = os.openpty()
        actual_dup = os.dup
        owned = []
        def track(fd):
            result = actual_dup(fd)
            owned.append(result)
            return result
        try:
            with os.fdopen(os.dup(slave), 'r') as stdin, os.fdopen(os.dup(slave), 'w') as stdout:
                with patch('control_plane.review.os.dup', side_effect=track), \
                        patch('control_plane.review.termios.tcgetattr', side_effect=OSError('synthetic-canary')):
                    review = self.review()
                    self.assertEqual(review.confirm(stdin=stdin, stdout=stdout), 'terminal_error')
                self.assertFalse(review.cleanup_pending)
                self.assertEqual(len(owned), 1)
                with self.assertRaises(OSError):
                    os.fstat(owned[0])
        finally:
            os.close(master)
            os.close(slave)


if __name__ == '__main__':
    unittest.main()
