"""A hung or runaway test ends as a failure instead of outliving its run (#176). No network or model."""
import ast
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import textwrap
import threading
import time
import unittest
from unittest.mock import patch
import uuid

import hang_guard
from quorum_test_support import isolated_profiles

TESTS = Path(__file__).resolve().parent
sys.path.insert(0, str(hang_guard.SCRIPTS))
MID = '11111111-1111-4111-8111-111111111111'
SID = '019f0000-0000-7000-8000-000000000000'


def child_source(body, **limits):
    limits = {'TEST_SECONDS':1, 'EXIT_GRACE_SECONDS':30, 'CHECK_SECONDS':.05, **limits}
    settings = '\n'.join(f'hang_guard.{name} = {value!r}' for name, value in limits.items())
    return (f'import os, subprocess, sys, time, unittest\nsys.path.insert(0, {str(TESTS)!r})\nimport hang_guard\n'
            f'{settings}\n\n\n{textwrap.dedent(body)}')


def alive(pid):
    try: os.kill(pid, 0)
    except ProcessLookupError: return False
    except PermissionError: pass
    return True


class Run(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)

    def write(self, body, **limits):
        (self.root / 'test_stuck.py').write_text(child_source(body, **limits))

    def run_child(self):
        return subprocess.run([sys.executable, '-m', 'unittest', 'test_stuck'], cwd=self.root, capture_output=True,
                              text=True, timeout=60)

    def await_file(self, path):
        deadline = time.monotonic() + 30
        while not path.exists():
            if time.monotonic() >= deadline: self.fail('no report from ' + path.name)
            time.sleep(.02)
        return path.read_text()

    def await_exit(self, pid):
        deadline = time.monotonic() + 30
        while alive(pid):
            if time.monotonic() >= deadline: self.fail(f'process {pid} survived')
            time.sleep(.02)

    def kill_after(self, pid):
        def stop():
            try: os.kill(pid, signal.SIGKILL)
            except OSError: pass
        self.addCleanup(stop)


class TimeLimit(Run):
    def test_a_hung_test_ends_the_run_and_names_where_it_hung(self):
        self.write('''
            class Stuck(unittest.TestCase):
                def test_hangs(self): time.sleep(50)
        ''')
        result = self.run_child()
        self.assertEqual(result.returncode, 1)
        self.assertIn('hang_guard: ending the test run: no test boundary', result.stderr)
        self.assertIn('test_hangs', result.stderr)

    def test_a_hung_class_fixture_is_bounded_too(self):
        self.write('''
            class Stuck(unittest.TestCase):
                @classmethod
                def setUpClass(cls): time.sleep(50)
                def test_never_runs(self): pass
        ''')
        result = self.run_child()
        self.assertEqual(result.returncode, 1)
        self.assertIn('no test boundary', result.stderr)
        self.assertIn('setUpClass', result.stderr)

    def test_the_c_timer_ends_a_run_whose_watcher_cannot_run(self):
        self.write('''
            class Stuck(unittest.TestCase):
                def test_hangs(self): time.sleep(50)
        ''', EXIT_GRACE_SECONDS=0, CHECK_SECONDS=50)
        result = self.run_child()
        self.assertEqual(result.returncode, 1)
        self.assertIn('Timeout', result.stderr)
        self.assertNotIn('hang_guard:', result.stderr)


@unittest.skipIf(os.name == 'nt', 'os.kill terminates a Windows process, and the sweep is POSIX')
class Survivors(Run):
    def test_a_process_started_by_a_hung_test_ends_with_the_run(self):
        self.write('''
            class Stuck(unittest.TestCase):
                def test_hangs(self):
                    child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(50)'],
                                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                    with open('pid', 'w') as report: report.write(str(child.pid))
                    time.sleep(50)
        ''')
        self.assertEqual(self.run_child().returncode, 1)
        pid = int(self.await_file(self.root / 'pid'))
        self.kill_after(pid)
        self.await_exit(pid)

    def test_a_run_ends_with_its_caller_past_a_launcher_that_stays_alive(self):
        self.write('''
            class Stuck(unittest.TestCase):
                def test_hangs(self):
                    with open('pid', 'w') as report: report.write(str(os.getpid()))
                    time.sleep(50)
        ''', TEST_SECONDS=50)
        launcher = ("import subprocess, sys; "
                    "subprocess.run([sys.executable, '-m', 'unittest', 'test_stuck'])")
        caller_source = ("import subprocess, sys, time; "
                         f"subprocess.Popen([sys.executable, '-c', {launcher!r}]); time.sleep(50)")
        caller = subprocess.Popen([sys.executable, '-c', caller_source], cwd=self.root,
                                  stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        self.addCleanup(caller.wait)
        self.addCleanup(caller.kill)
        pid = int(self.await_file(self.root / 'pid'))
        self.kill_after(pid)
        self.assertTrue(alive(pid))
        caller.kill()
        caller.wait()  # An unreaped caller still answers a signal probe as alive.
        self.await_exit(pid)


class MemoryLimit(Run):
    @unittest.skipIf(hang_guard.resource is None, 'POSIX peak memory')
    def test_a_run_past_the_memory_limit_ends(self):
        self.write('''
            class Greedy(unittest.TestCase):
                def test_grows(self):
                    kept = b'x' * (512 * 1024 ** 2)
                    time.sleep(50)
        ''', TEST_SECONDS=50, MEMORY_BYTES=256 * 1024 ** 2)
        result = self.run_child()
        self.assertEqual(result.returncode, 1)
        self.assertIn('peak memory', result.stderr)


    def test_a_test_mocking_the_platform_does_not_change_the_reading(self):
        """The full suite was once ended by a 25 MiB process read as 25 GiB during such a mock."""
        real = hang_guard.peak_bytes()
        for platform in ('linux', 'darwin', 'win32'):
            with self.subTest(platform), patch('sys.platform', platform):
                self.assertLess(hang_guard.peak_bytes(), real + 2 ** 30)


class CallerCheck(unittest.TestCase):
    def verdicts(self, *readings):
        left = iter(readings)
        def verdict():
            value = next(left)
            return value() if callable(value) else value
        with patch.object(hang_guard, 'die', side_effect=SystemExit) as die, \
             patch.object(hang_guard, 'CHECK_SECONDS', 0):
            with self.assertRaises(SystemExit): hang_guard.watch([verdict])
        return die, list(left)

    def test_one_reading_is_a_mock_and_only_two_in_a_row_end_the_run(self):
        die, left = self.verdicts(False, True, False, True, True, False)
        die.assert_called_once_with('the caller of this run has ended')
        self.assertEqual(left, [False])

    def test_a_probe_that_raises_is_not_a_dead_caller(self):
        def broken(): raise OSError('mocked')
        die, _ = self.verdicts(broken, True, broken, True, True)
        die.assert_called_once()
        self.assertIs(hang_guard.caller_gone([broken]), False)


class ListenerBudget(unittest.TestCase):
    @isolated_profiles
    def test_a_relay_whose_queue_never_gets_a_receipt_stops_and_keeps_its_fake_small(self):
        """The likely shape of #176: a real loop writing SQLite while a fake records every call."""
        from quorum_listener import run_listener
        from quorum_session import Relay
        from quorum_state import LocalLedger
        message = {'id':MID, 'recipient_id':'a'}
        class Api:
            calls = []
            def call(self, operation, payload, request_id=None):
                self.calls.append(operation)
                if operation == 'session_requirements': return {'protocol_version':2, 'skill_version':'0.3.0'}
                return [message] if operation == 'session_inbox' else {'queued':True}
        with tempfile.TemporaryDirectory() as root, patch.object(hang_guard, 'LISTENER_PASSES', 50):
            ledger = LocalLedger(Path(root))
            try:
                with self.assertRaisesRegex(hang_guard.RunawayLoop, 'no stop condition'):
                    run_listener(ledger, lambda: Relay(Api(), ledger, {'agent_id':'a', 'session_ref':SID},
                                                       lambda _: 'submitted'),
                                 str(uuid.uuid4()), sleep=lambda _: None, timeout=0)
            finally: ledger.db.close()
        self.assertLessEqual(len(Api.calls), 200)

    def test_each_outcome_that_skips_the_sleep_is_stopped(self):
        from quorum_listener import run_listener
        from quorum_state import LocalLedger
        for outcome in ('notice', 'delivered', 'answered'):
            with self.subTest(outcome=outcome), tempfile.TemporaryDirectory() as root, \
                 patch.object(hang_guard, 'LISTENER_PASSES', 50):
                hang_guard.passes = 0
                ledger = LocalLedger(Path(root))
                class Busy:
                    def reconcile(self): pass
                    def run_once(self): return outcome
                try:
                    with self.assertRaises(hang_guard.RunawayLoop):
                        run_listener(ledger, Busy, str(uuid.uuid4()), sleep=lambda _: None, timeout=0)
                finally: ledger.db.close()

    def test_a_test_boundary_restarts_both_limits(self):
        hang_guard.passes, hang_guard.ticked_at = hang_guard.LISTENER_PASSES, 0.0
        hang_guard.tick()
        self.assertEqual(hang_guard.passes, 0)
        self.assertGreater(hang_guard.ticked_at, 0.0)


class Coverage(unittest.TestCase):
    def test_every_test_module_imports_the_guard_at_the_top_level(self):
        for path in sorted(TESTS.glob('test_*.py')):
            names = set()
            for node in ast.parse(path.read_text(encoding='utf-8')).body:
                if isinstance(node, ast.Import): names.update(alias.name for alias in node.names)
                if isinstance(node, ast.ImportFrom): names.add(node.module)
            with self.subTest(path.name):
                self.assertTrue(names & {'hang_guard', 'quorum_test_support'})

    def test_the_support_module_carries_the_guard(self):
        self.assertIn('import hang_guard', (TESTS / 'quorum_test_support.py').read_text(encoding='utf-8'))

    def test_importing_a_test_module_as_a_probe_script_starts_nothing(self):
        script = (f'import sys, threading; sys.path.insert(0, {str(TESTS)!r}); import hang_guard; '
                  'assert not hang_guard.started and threading.active_count() == 1')
        subprocess.run([sys.executable, '-c', script], check=True, timeout=30)

    def test_the_guard_is_running_in_this_process(self):
        self.assertTrue(hang_guard.started)
        self.assertIn('hang-guard', [thread.name for thread in threading.enumerate()])


if __name__ == '__main__': unittest.main()
