"""UI-only regression. Pseudo terminals test input guards, not human identity."""
from contextlib import redirect_stdout, redirect_stderr
import io
import os
from pathlib import Path
import re
import select
import subprocess
import sys
import termios
import threading
import time
import unittest
from unittest.mock import patch

from agent.cli import actions as ui

ROOT = Path(__file__).resolve().parents[2]


class ActionTests(unittest.TestCase):
    def tty_flow(self, answer=None, *, seconds=2):
        """Real PTY/selector; driver sends only after the visible prompt."""
        proposal=ui.Proposal(seconds=seconds)
        master,slave=os.openpty();original=termios.tcgetattr(slave)
        captured=bytearray();errors=[]
        def driver():
            try:
                end=time.monotonic()+3
                while time.monotonic()<end:
                    if select.select([master],[],[],.05)[0]:
                        captured.extend(os.read(master,8192))
                        if b'nh\xe1\xba\xadp secrets' in captured:
                            if answer is not None:
                                text=answer(proposal) if callable(answer) else answer
                                os.write(master,text if isinstance(text,bytes) else text.encode())
                            return
                raise AssertionError('NO_PROMPT')
            except Exception as error:errors.append(type(error).__name__)
        thread=threading.Thread(target=driver)
        try:
            with os.fdopen(os.dup(slave),'r') as stdin, os.fdopen(os.dup(slave),'w') as stdout:
                thread.start()
                decision=ui.confirm(proposal,stdin=stdin,stdout=stdout)
                thread.join(4)
                self.assertFalse(thread.is_alive());self.assertEqual(errors,[])
                self.assertFalse(proposal.cleanup_pending)
                self.assertEqual(termios.tcgetattr(slave),original)
                while select.select([master],[],[],.01)[0]:captured.extend(os.read(master,8192))
            return proposal,decision,captured.decode(errors='replace')
        finally:
            thread.join(4) if thread.ident is not None else None
            os.close(master);os.close(slave)

    def test_real_tty_approve_is_local_and_never_executed(self):
        p,d,text=self.tty_flow(lambda p:'DONG_Y '+p.identifier+'\n')
        self.assertEqual(d.status,ui.Status.APPROVED);self.assertFalse(d.executed)
        self.assertIn('approved KHÔNG phải executed',text)
        self.assertIn('nginx.service',text);self.assertIn('Rủi ro:',text)
        self.assertIn('failed-02 — synthetic:query_journal',text)
        self.assertNotIn('DONG_Y '+p.identifier+'\r\n',text) # input not echoed

    def test_real_tty_reject_and_eof(self):
        for answer,reason in ((lambda p:'TU_CHOI '+p.identifier+'\n',ui.Reason.REJECTED),(b'\x04',ui.Reason.EOF)):
            p,d,_=self.tty_flow(answer)
            self.assertEqual(d.status,ui.Status.REJECTED);self.assertEqual(d.reason,reason)
            self.assertFalse(d.executed)

    def test_real_tty_timeout_restores_terminal(self):
        _,d,_=self.tty_flow(seconds=.1)
        self.assertEqual(d.status,ui.Status.EXPIRED)

    def test_invalid_input_and_secrets_are_not_echoed(self):
        for raw in (b'yes\n',b'DONG_Y forged\n',b'password=fake-secret\n',b'x'*200+b'\n',b'\xff\n'):
            _,d,text=self.tty_flow(raw)
            self.assertEqual(d.status,ui.Status.REJECTED);self.assertEqual(d.reason,ui.Reason.INVALID_INPUT)
            self.assertNotIn('fake-secret',text)

    def test_non_tty_no_read_no_autoapprove(self):
        p=ui.Proposal()
        with patch.object(ui,'_read_choice') as reader, patch.dict(os.environ,{'AUTO_APPROVE':'true'}):
            d=ui.confirm(p,stdin=io.StringIO('DONG_Y '+p.identifier+'\n'),stdout=io.StringIO())
        reader.assert_not_called();self.assertEqual(d.reason,ui.Reason.NON_TTY)
        self.assertEqual(d.status,ui.Status.REJECTED)

    def test_expired_before_input_no_read(self):
        p=ui.Proposal()
        with patch.object(ui.time,'monotonic',return_value=p._deadline),patch.object(ui,'_read_choice') as reader:
            d=ui.confirm(p,stdin=io.StringIO(),stdout=io.StringIO())
        reader.assert_not_called();self.assertEqual(d.status,ui.Status.EXPIRED)

    def test_no_evidence_display_and_fixed_scope(self):
        text=ui.Proposal(ui.Scenario.NO_EVIDENCE).display()
        self.assertIn('Nguồn evidence: không có',text)
        self.assertIn('không có căn cứ',text)
        for value in ('ssh.service','sudo restart','approved=true','password=fake-secret'):
            with self.assertRaises(ValueError):ui.Proposal(value)
        with self.assertRaises(TypeError):ui.Proposal(unit='ssh.service')
        with self.assertRaises(TypeError):ui.Proposal(approval_token='forged')
        with self.assertRaises(TypeError):ui.Decision(ui.Status.APPROVED,ui.Reason.CONFIRMED,executed=True)

    def test_terminal_states_do_not_prompt_again(self):
        p,d,_=self.tty_flow(lambda p:'DONG_Y '+p.identifier+'\n')
        with patch.object(ui,'_read_choice') as reader:
            self.assertIs(ui.confirm(p,stdin=io.StringIO(),stdout=io.StringIO()),d)
        reader.assert_not_called()
        self.assertEqual(p.status,ui.Status.APPROVED)

    def test_cancel_io_failure_and_late_confirmation(self):
        class TTY(io.StringIO):
            def isatty(self):return True
            def fileno(self):return 5
        for error,reason in ((KeyboardInterrupt(),ui.Reason.CANCELLED),(OSError('password=fake-secret'),ui.Reason.TERMINAL_ERROR)):
            p=ui.Proposal()
            with patch.object(ui.os,'isatty',return_value=True),patch.object(ui.os,'ttyname',return_value='same'),patch.object(ui,'_read_choice',side_effect=error):
                d=ui.confirm(p,stdin=TTY(),stdout=TTY())
            self.assertEqual(d.reason,reason);self.assertEqual(d.status,ui.Status.REJECTED)
            self.assertNotIn('fake-secret',str(d))
        p=ui.Proposal()
        def late(*args):
            p._deadline=time.monotonic()-1
            return 'DONG_Y '+p.identifier+'\n'
        with patch.object(ui.os,'isatty',return_value=True),patch.object(ui.os,'ttyname',return_value='same'),patch.object(ui,'_read_choice',side_effect=late):
            self.assertEqual(ui.confirm(p,stdin=TTY(),stdout=TTY()).status,ui.Status.EXPIRED)

    def test_read_cancellation_restores_terminal_and_no_task_owner(self):
        master,slave=os.openpty();original=termios.tcgetattr(slave)
        try:
            with os.fdopen(os.dup(slave),'r') as stdin,os.fdopen(os.dup(slave),'w') as stdout:
                p=ui.Proposal()
                with patch.object(ui.selectors,'DefaultSelector') as selector,patch.object(ui.os,'read',side_effect=KeyboardInterrupt):
                    selector.return_value.__enter__.return_value.select.return_value=[object()]
                    d=ui.confirm(p,stdin=stdin,stdout=stdout)
                self.assertEqual(d.reason,ui.Reason.CANCELLED)
                self.assertFalse(p.cleanup_pending)
                self.assertEqual(termios.tcgetattr(slave),original)
        finally:os.close(master);os.close(slave)

    def test_failed_restoration_retains_owner_and_cannot_approve(self):
        master,slave=os.openpty();original=termios.tcgetattr(slave)
        actual=termios.tcsetattr;count=0
        def fail_restore(*args):
            nonlocal count
            count+=1
            if count>1:raise OSError('password=fake-secret')
            return actual(*args)
        try:
            with os.fdopen(os.dup(slave),'r') as stdin,os.fdopen(os.dup(slave),'w') as stdout:
                p=ui.Proposal()
                with patch.object(ui.selectors,'DefaultSelector') as selector,patch.object(ui.os,'read',return_value=('DONG_Y '+p.identifier+'\n').encode()),patch.object(ui.termios,'tcsetattr',side_effect=fail_restore):
                    selector.return_value.__enter__.return_value.select.return_value=[object()]
                    d=ui.confirm(p,stdin=stdin,stdout=stdout)
                self.assertEqual(d.status,ui.Status.REJECTED);self.assertTrue(p.cleanup_pending)
                p.finish_cleanup();self.assertFalse(p.cleanup_pending)
                self.assertEqual(termios.tcgetattr(slave),original)
        finally:termios.tcsetattr(slave,termios.TCSANOW,original);os.close(master);os.close(slave)

    def test_cleanup_owner_survives_stdin_wrapper_closing(self):
        master,slave=os.openpty();original=termios.tcgetattr(slave)
        actual=termios.tcsetattr;count=0;p=ui.Proposal()
        def fail_restore(*args):
            nonlocal count
            count+=1
            if count>1:raise OSError('synthetic restoration failure')
            return actual(*args)
        try:
            with os.fdopen(os.dup(slave),'r') as stdin,os.fdopen(os.dup(slave),'w') as stdout:
                with patch.object(ui.selectors,'DefaultSelector') as selector, \
                        patch.object(ui.os,'read',return_value=('DONG_Y '+p.identifier+'\n').encode()), \
                        patch.object(ui.termios,'tcsetattr',side_effect=fail_restore):
                    selector.return_value.__enter__.return_value.select.return_value=[object()]
                    decision=ui.confirm(p,stdin=stdin,stdout=stdout)
                self.assertEqual(decision.status,ui.Status.REJECTED)
                self.assertTrue(p.cleanup_pending)
                owned=p._terminal[0]
            # Retry occurs after the borrowed stdin/stdout wrappers were closed.
            os.fstat(owned)
            p.finish_cleanup()
            self.assertFalse(p.cleanup_pending)
            self.assertEqual(termios.tcgetattr(slave),original)
            with self.assertRaises(OSError):os.fstat(owned)
        finally:
            actual(slave,termios.TCSANOW,original)
            os.close(master);os.close(slave)

    def test_different_terminal_is_denied(self):
        class TTY(io.StringIO):
            def isatty(self):return True
            def fileno(self):return 1
        with patch.object(ui.os,'isatty',return_value=True),patch.object(ui.os,'ttyname',side_effect=['a','b']),patch.object(ui,'_read_choice') as reader:
            d=ui.confirm(ui.Proposal(),stdin=TTY(),stdout=TTY())
        self.assertEqual(d.reason,ui.Reason.NON_TTY);reader.assert_not_called()

    def test_bad_cli_args_do_not_echo_secrets(self):
        for args in (['--yes'],['--approval-token','fake-secret'],['--unit','ssh.service'],
                     ['--command','sudo restart'],['--scenario','password=fake-secret'],
                     ['--timeout','nan'],['--timeout','0'],['--timeout','61']):
            out=io.StringIO();err=io.StringIO()
            with redirect_stdout(out),redirect_stderr(err):
                with self.assertRaises(SystemExit) as caught:ui.main(args)
            self.assertEqual(caught.exception.code,2);self.assertNotIn('fake-secret',out.getvalue()+err.getvalue())

    def test_cli_pipe_is_denied_without_network_or_executor(self):
        result=subprocess.run([sys.executable,'-B','-m','agent.cli.actions'],input='yes\n',
            text=True,capture_output=True,cwd=ROOT,env={**os.environ,'PYTHONPATH':str(ROOT/'src'),
            'AUTO_APPROVE':'true','PYTHONDONTWRITEBYTECODE':'1'},timeout=5)
        self.assertEqual(result.returncode,1)
        self.assertIn('NON_TTY',result.stdout);self.assertIn('executed=false',result.stdout)
        self.assertEqual(result.stderr,'')

    def test_actual_cli_pty_approve(self):
        master,slave=os.openpty();original=termios.tcgetattr(slave)
        process=None
        try:
            process=subprocess.Popen([sys.executable,'-B','-m','agent.cli.actions','--timeout','3'],
                stdin=slave,stdout=slave,stderr=slave,cwd=ROOT,
                env={**os.environ,'PYTHONPATH':str(ROOT/'src'),'PYTHONDONTWRITEBYTECODE':'1'})
            output=bytearray();end=time.monotonic()+5;sent=False
            while time.monotonic()<end:
                if select.select([master],[],[],.1)[0]:output.extend(os.read(master,8192))
                if not sent and b'nh\xe1\xba\xadp secrets' in output:
                    identifier=re.search(rb'ID \xc4\x91\xe1\xbb\x81 xu\xe1\xba\xa5t: ([a-f0-9]{32})',output).group(1)
                    os.write(master,b'DONG_Y '+identifier+b'\n');sent=True
                if process.poll() is not None:
                    while select.select([master],[],[],.01)[0]:output.extend(os.read(master,8192))
                    break
            self.assertEqual(process.wait(timeout=1),0)
            self.assertIn(b'CONFIRMED_LOCAL_DEMO_ONLY',output)
            self.assertIn(b'cleanup_pending=false',output)
            self.assertEqual(termios.tcgetattr(slave),original)
        finally:
            if process is not None and process.poll() is None:process.kill();process.wait(timeout=2)
            os.close(master);os.close(slave)

    def test_invalid_configuration_is_rejected(self):
        for seconds in (0,-1,61,float('nan'),True):
            with self.assertRaises(ValueError):ui.Proposal(seconds=seconds)
