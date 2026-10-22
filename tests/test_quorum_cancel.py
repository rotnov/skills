"""Deterministic owned-child cancellation boundaries; never signal processes."""
import errno
import io
import os
from pathlib import Path
import signal
import sys
import threading
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'skills/quorum/scripts'))
from quorum_acp import ClaudePilotRunner


class OwnedChild:
    """Only the Popen state transitions needed for a controlled reap race."""
    pid = 424242
    quorum_timeout = -1

    def __init__(self, reap_after=2):
        self.stdout = io.BytesIO()
        self.returncode = None
        self.polls = 0
        self.reap_after = reap_after
        self.waited = False

    def poll(self):
        self.polls += 1
        if self.polls >= self.reap_after:
            self.returncode = -signal.SIGTERM
        return self.returncode

    def wait(self, *, timeout):
        if self.returncode is None:
            raise AssertionError('owned child was not reaped')
        self.waited = True
        return self.returncode


class CancellationTests(unittest.TestCase):
    def setUp(self):
        self.runner = ClaudePilotRunner.__new__(ClaudePilotRunner)
        self.child = OwnedChild()
        self.addCleanup(self.child.stdout.close)

    def signals(self, boundary):
        # Run the POSIX branch even on Windows; all group signals are intercepted.
        self.enterContext(patch('os.name', 'posix'))
        return self.enterContext(patch('os.killpg', side_effect=boundary, create=True))

    def test_probe_permission_race_preserves_timeout_and_closes_output(self):
        probes = []
        def boundary(pid, sig):
            self.assertEqual(pid, self.child.pid)
            if sig == signal.SIGTERM:
                return
            self.assertEqual(sig, 0)
            probes.append(self.child.returncode)
            if len(probes) == 1:
                raise PermissionError(errno.EPERM, 'unreaped group')
            self.assertIsNotNone(self.child.returncode, 'must reap before retry')
            raise ProcessLookupError(errno.ESRCH, 'group gone')
        self.signals(boundary)
        with self.assertRaisesRegex(ValueError, '^adapter_timeout$'):
            list(self.runner.events(self.child))
        self.assertEqual(len(probes), 2)
        self.assertTrue(self.child.waited)
        self.assertTrue(self.child.stdout.closed)

    def test_persistent_probe_permission_propagates_and_closes_output(self):
        def boundary(pid, sig):
            if sig == signal.SIGTERM:
                return
            raise PermissionError(errno.EPERM, 'group inaccessible')
        self.signals(boundary)
        with self.assertRaisesRegex(PermissionError, 'group inaccessible'):
            list(self.runner.events(self.child))
        self.assertTrue(self.child.stdout.closed)
        self.assertFalse(self.child.waited)

    def test_permission_failure_returns_while_pipe_reader_is_blocked(self):
        entered, closed = threading.Event(), threading.Event()
        class ObservedPipe(io.BufferedReader):
            def read1(self, size):
                entered.set()
                return super().read1(size)

            def close(self):
                super().close()
                closed.set()

        read_fd, write_fd = os.pipe()
        stream = ObservedPipe(io.FileIO(read_fd, 'rb', closefd=True))
        self.child.stdout.close()
        self.child.stdout = stream
        self.child.quorum_timeout = .1
        def boundary(pid, sig):
            self.assertTrue(entered.wait(timeout=1), 'reader did not enter read1')
            raise PermissionError(errno.EPERM, 'live writer inaccessible')
        self.signals(boundary)
        errors, finished = [], threading.Event()
        def consume():
            try:
                list(self.runner.events(self.child))
            except BaseException as error:
                errors.append(error)
            finally:
                finished.set()
        consumer = threading.Thread(target=consume, daemon=True)
        consumer.start()
        try:
            self.assertTrue(entered.wait(timeout=1))
            self.assertTrue(finished.wait(timeout=3), 'buffered close blocked cancellation failure')
            self.assertEqual(len(errors), 1)
            self.assertIsInstance(errors[0], PermissionError)
            self.assertIn('live writer inaccessible', str(errors[0]))
            self.assertFalse(stream.closed, 'blocked reader must own eventual close')
        finally:
            os.close(write_fd)  # Unblock the owned reader even on regression failure.
            consumer.join(timeout=3)
            reader_closed = closed.wait(timeout=3)
            if not reader_closed: stream.close()
        self.assertFalse(consumer.is_alive())
        self.assertTrue(reader_closed, 'reader must close its stream after EOF')
        self.assertTrue(stream.closed)

    def test_reaped_leader_does_not_hide_surviving_group(self):
        probes = 0
        killed = False
        def boundary(pid, sig):
            nonlocal probes, killed
            self.assertEqual(pid, self.child.pid)
            if sig == signal.SIGTERM:
                return
            if sig == signal.SIGKILL:
                killed = True
                return
            self.assertEqual(sig, 0)
            probes += 1
            if probes == 1:
                raise PermissionError(errno.EPERM, 'reap race')
            self.assertIsNotNone(self.child.returncode)
        self.signals(boundary)
        self.runner.cancel(self.child)
        self.assertTrue(killed, 'live group must receive escalation after leader exit')
        self.assertTrue(self.child.waited)

    def test_initial_term_permission_recovers_only_after_reap_and_esrch(self):
        self.child.reap_after = 1
        def boundary(pid, sig):
            self.assertEqual(pid, self.child.pid)
            if sig == signal.SIGTERM:
                raise PermissionError(errno.EPERM, 'already zombie')
            self.assertEqual(sig, 0)
            self.assertIsNotNone(self.child.returncode)
            raise ProcessLookupError(errno.ESRCH, 'group gone')
        self.signals(boundary)
        self.runner.cancel(self.child)
        self.assertTrue(self.child.waited)

    def test_initial_term_permission_with_live_group_propagates(self):
        self.child.reap_after = 1
        def boundary(pid, sig):
            if sig == signal.SIGTERM:
                raise PermissionError(errno.EPERM, 'term denied')
            self.assertEqual(sig, 0)
        self.signals(boundary)
        with self.assertRaisesRegex(PermissionError, 'term denied'):
            self.runner.cancel(self.child)
        self.assertFalse(self.child.waited)

    def test_initial_term_and_probe_permission_remain_an_error(self):
        def boundary(*_):
            raise PermissionError(errno.EPERM, 'persistent denial')
        self.signals(boundary)
        with self.assertRaisesRegex(PermissionError, 'persistent denial'):
            self.runner.cancel(self.child)
        self.assertFalse(self.child.waited)

    def test_escalation_permission_requires_independent_group_disappearance(self):
        for state in ('gone', 'live', 'inaccessible'):
            with self.subTest(state=state):
                self.child = OwnedChild(reap_after=1)
                self.addCleanup(self.child.stdout.close)
                escalated = False
                def boundary(pid, sig):
                    nonlocal escalated
                    if sig == signal.SIGKILL:
                        escalated = True
                        raise PermissionError(errno.EPERM, 'kill denied')
                    if sig == 0 and escalated:
                        if state == 'gone':
                            raise ProcessLookupError(errno.ESRCH, 'group gone')
                        if state == 'inaccessible':
                            raise PermissionError(errno.EPERM, 'probe denied')
                with patch('os.name', 'posix'), patch('os.killpg', side_effect=boundary, create=True):
                    if state == 'gone':
                        self.runner.cancel(self.child)
                        self.assertTrue(self.child.waited)
                    else:
                        with self.assertRaises(PermissionError):
                            self.runner.cancel(self.child)
                        self.assertFalse(self.child.waited)
                self.assertTrue(escalated)


if __name__ == '__main__':
    unittest.main()
