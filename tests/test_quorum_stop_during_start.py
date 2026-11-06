"""Owner stop against every start window: lock table, stop windows and crash windows.

Disposable ledgers and a fake child only; no service, model or real listener. Portable, so the
Windows CI job runs the same table against real `msvcrt` locks.
"""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch
import uuid

SCRIPTS = Path(__file__).resolve().parents[1] / 'skills/quorum/scripts'
sys.path.insert(0, str(SCRIPTS))
SID = '11111111-1111-4111-8111-111111111111'
CONTROL_LOCK = 'listener-control.lock'
REQUIREMENTS = {'protocol_version':2, 'skill_version':'0.3.0'}


class Crash(BaseException):
    """The start process dying at this exact point; nothing after it runs."""


class Api:
    def __init__(self, hook):
        self.hook = hook

    def call(self, operation, payload, request_id=None):
        self.hook('rpc:' + operation)
        return REQUIREMENTS if operation == 'session_requirements' else []


class Child:
    """A spawned listener in one process-free object: holds listener.lock, obeys the stop flag."""
    def __init__(self, root, run_id, *, ready=True):
        from quorum_listener import Control
        from quorum_os import FileLock
        from quorum_release import installed_release
        from quorum_state import LocalLedger
        self.ledger, self.run_id, self.code = LocalLedger(root), run_id, None
        self.control, self.lock = Control(self.ledger), FileLock(root / 'listener.lock').__enter__()
        self.control.update(run_id, 'waiting' if ready else 'starting', 0, timeout=0, release=installed_release())

    def alive(self):
        return self.code is None

    def poll(self):
        if self.code is None and self.control.stopping(self.run_id):
            self.control.update(self.run_id, 'stopped', 0, timeout=0)
            self.close()
        return self.code

    def close(self):
        if self.code is None:
            self.code = 0
            self.lock.__exit__()
            self.ledger.db.close()

    def wait(self, timeout=None):
        self.close()
        return self.code

    terminate = kill = close


class StopDuringStart(unittest.TestCase):
    def setUp(self):
        from quorum_state import LocalLedger
        temporary = self.enterContext(tempfile.TemporaryDirectory())
        self.state = Path(temporary).resolve()
        self.root = self.state / 'sessions' / SID
        test = self
        class ProbedLedger(LocalLedger):
            def setting(self, key, value=None):
                if value is not None and key in ('session-reception', 'session-agent', 'listener'):
                    test.step('write:' + key)
                return super().setting(key, value)
        self.ledger = ProbedLedger(self.root)
        self.addCleanup(self.ledger.db.close)
        self.record = {'engine':'claude', 'session_ref':SID, 'agent_id':'agent', 'cwd':str(self.state),
                       'messaging_socket':'/synthetic/old'}
        self.other = LocalLedger(self.root)  # A separate process's connection for the racing stop.
        self.addCleanup(self.other.db.close)
        self.other.setting('session-agent', self.record)
        self.children, self.table, self.at, self.action = [], {}, None, None
        self.addCleanup(lambda: [child.close() for child in self.children])
        native = subprocess.Popen
        def spawn(command, **kwargs):
            if '--run-id' not in command: return native(command, **kwargs)
            run_id = command[command.index('--run-id') + 1]
            self.step('spawn')
            child = Child(self.root, run_id, ready=self.child_ready)
            self.children.append(child)
            self.step('spawned')
            return child
        self.child_ready = True
        popen = patch('quorum_listener.subprocess.Popen', side_effect=spawn)
        popen.start()
        self.addCleanup(popen.stop)

    def step(self, name):
        from quorum_listener import START_LOCK, locked
        self.table.setdefault(name, (locked(self.root / START_LOCK), locked(self.root / CONTROL_LOCK)))
        if name == self.at:
            action, self.at = self.action, None
            action()

    def at_step(self, name, action):
        self.at, self.action = name, action

    def owner_stop(self):
        from quorum_listener import stop_listener
        self.result = stop_listener(self.other, revoke_reception=True)

    def crash(self):
        raise Crash()

    def start(self, **options):
        from quorum_session import start
        return start(self.state, self.ledger, SID, api=Api(self.step), **options)

    def reception(self):
        return self.other.setting('session-reception')

    def stopped_run(self):
        return (self.other.setting('listener-stop') or {}).get('run_id')

    def assert_aborted(self, **options):
        with self.assertRaisesRegex(ValueError, '^listener_start_stopped$'):
            self.start(**options)
        self.assertEqual(self.reception(), {'enabled':False})
        self.assertFalse(any(child.alive() for child in self.children), 'no listener outlives the stop')

    def test_lock_table_holds_control_only_around_ledger_mutations(self):
        self.start()
        self.assertEqual(self.table, {  # step: (listener-start.lock, listener-control.lock)
            'rpc:session_requirements': (True, False),
            'write:listener': (True, True),  # control.begin
            'spawn': (True, False),
            'spawned': (True, False),
            'write:session-reception': (True, True)})  # enable

    def test_rebind_wait_runs_outside_the_control_lock_and_its_write_inside(self):
        from quorum_os import FileLock
        old = FileLock(self.root / 'listener.lock').__enter__()
        self.other.setting('listener', {'run_id':str(uuid.uuid4()), 'state':'waiting'})
        waits = []
        real = time.sleep
        def wait(seconds):
            from quorum_listener import locked
            waits.append(locked(self.root / CONTROL_LOCK))
            old.__exit__()
            real(0)
        try:
            from quorum_release import installed_release
            self.other.setting('listener-release', {**installed_release(),
                                                    'run_id':self.other.setting('listener')['run_id']})
            with patch('quorum_session.time.sleep', side_effect=wait):
                result = self.start(endpoint='/synthetic/new')
        finally:
            try: old.__exit__()
            except OSError: pass
        self.assertEqual(waits[0], False)
        self.assertEqual(self.table['write:session-agent'], (True, True))
        self.assertTrue(result['messaging_rebound'])
        self.assertEqual(self.other.setting('session-agent')['messaging_socket'], '/synthetic/new')

    def test_stop_during_the_release_check_prevents_any_child(self):
        self.at_step('rpc:session_requirements', self.owner_stop)
        self.assert_aborted()
        self.assertEqual(self.children, [])
        self.assertIsNone(self.other.setting('listener'), 'no run published')

    def test_stop_during_recovery_inbox_check_revokes_before_any_child(self):
        from quorum_resume import enable
        enable(self.other, self.record)
        self.at_step('rpc:session_inbox', self.owner_stop)
        with self.assertRaisesRegex(ValueError, '^listener_start_stopped$'):
            self.start(recovery=True)
        self.assertEqual(self.children, [])

    def test_stop_while_the_control_section_would_publish_wins_the_compare(self):
        # The last instant before publication: stop lands after every RPC, ahead of the section.
        from quorum_listener import StartGuard
        real = StartGuard.section
        stops = []
        def section(guard):
            if not stops:
                stops.append(True)
                self.owner_stop()
            return real(guard)
        with patch.object(StartGuard, 'section', section):
            self.assert_aborted()
        self.assertIsNone(self.other.setting('listener'))
        self.assertEqual(self.children, [])

    def test_stop_during_spawn_targets_the_published_run(self):
        self.at_step('spawn', self.owner_stop)
        self.assert_aborted()
        self.assertEqual(self.stopped_run(), self.children[0].run_id)

    def test_stop_during_ready_wait_stops_the_starting_child(self):
        self.child_ready = False
        self.at_step('spawned', self.owner_stop)
        self.assert_aborted()
        self.assertEqual(self.result['stop_requested'], True)
        self.assertEqual(self.other.setting('listener')['state'], 'stopped')

    def test_stop_after_ready_observed_cannot_be_overwritten_by_enable(self):
        from quorum_listener import Control
        real = Control.status
        stops = []
        def status(control):
            result = real(control)
            if control.ledger is not self.ledger: return result
            if result.get('state') == 'waiting' and not stops:
                stops.append(True)
                self.owner_stop()
            return result
        with patch.object(Control, 'status', status):
            self.assert_aborted()
        self.assertNotIn('write:session-reception', self.table, 'enable never ran')

    def test_stop_after_a_finished_start_still_wins(self):
        self.start()
        self.assertTrue(self.reception()['enabled'])
        self.owner_stop()
        self.assertEqual(self.reception(), {'enabled':False})
        self.children[0].poll()
        self.assertFalse(self.children[0].alive())

    def test_stop_during_rebind_wait_aborts_without_moving_the_endpoint(self):
        from quorum_os import FileLock
        from quorum_release import installed_release
        run_id = str(uuid.uuid4())
        self.other.setting('listener', {'run_id':run_id, 'state':'waiting'})
        self.other.setting('listener-release', {**installed_release(), 'run_id':run_id})
        old = FileLock(self.root / 'listener.lock').__enter__()
        self.addCleanup(old.__exit__)
        with patch('quorum_session.time.sleep', side_effect=lambda _: self.owner_stop()):
            with self.assertRaisesRegex(ValueError, '^listener_start_stopped$'):
                self.start(endpoint='/synthetic/new')
        self.assertEqual(self.other.setting('session-agent')['messaging_socket'], '/synthetic/old')
        self.assertEqual(self.reception(), {'enabled':False})
        self.assertEqual(self.children, [])

    def test_stop_during_reuse_of_a_running_listener_never_grants_reception(self):
        run_id = str(uuid.uuid4())
        self.child_ready = False
        running = Child(self.root, run_id, ready=False)
        self.children.append(running)
        with patch('quorum_session.time.sleep', side_effect=lambda _: self.owner_stop()):
            with self.assertRaisesRegex(ValueError, '^listener_start_stopped$'):
                self.start()
        self.assertEqual(self.reception(), {'enabled':False})
        self.assertEqual(self.stopped_run(), run_id)
        running.poll()
        self.assertFalse(running.alive())

    def test_retiring_the_binding_aborts_an_in_progress_start(self):
        from quorum_session import retire_binding
        self.at_step('rpc:session_requirements', lambda: retire_binding(self.other, self.record, 'left'))
        self.assert_aborted()
        self.assertEqual(self.children, [])

    def test_stop_does_not_need_the_start_lock(self):
        from quorum_listener import START_LOCK
        from quorum_os import FileLock
        with FileLock(self.root / START_LOCK):
            began = time.monotonic()
            self.owner_stop()
            self.assertLess(time.monotonic() - began, 2)
        self.assertEqual(self.reception(), {'enabled':False})

    def test_stop_returns_promptly_while_start_blocks_in_an_rpc(self):
        # A real second thread with its own connection, blocked as a slow service call would be.
        from quorum_session import start
        from quorum_state import LocalLedger
        entered, release, outcome = threading.Event(), threading.Event(), []
        def blocked(step):
            if step == 'rpc:session_requirements':
                entered.set()
                release.wait(timeout=20)
        def run():
            own = LocalLedger(self.root)
            try: outcome.append(start(self.state, own, SID, api=Api(blocked)))
            except ValueError as error: outcome.append(str(error))
            finally: own.db.close()
        thread = threading.Thread(target=run)
        thread.start()
        try:
            self.assertTrue(entered.wait(5))
            began = time.monotonic()
            self.owner_stop()
            self.assertLess(time.monotonic() - began, 2, 'stop never waits for the start')
        finally:
            release.set()
            thread.join(20)
        self.assertEqual(outcome, ['listener_start_stopped'])
        self.assertEqual(self.children, [])

    def test_second_start_is_refused_while_one_is_in_progress(self):
        from quorum_listener import START_LOCK
        from quorum_os import FileLock
        with FileLock(self.root / START_LOCK):
            with self.assertRaisesRegex(ValueError, '^worker_already_running$'):
                self.start()

    def test_an_explicit_start_after_a_completed_stop_is_not_aborted(self):
        self.owner_stop()
        self.start()
        self.assertTrue(self.reception()['enabled'])
        self.assertTrue(self.children[0].alive())

    # --- Crash windows: the OS releases the start lock with the dead process ---------------------

    def test_crash_before_spawn_leaves_a_diagnosable_run_and_no_child(self):
        from quorum_listener import Control
        self.at_step('spawn', self.crash)
        with self.assertRaises(Crash): self.start()
        self.assertEqual(self.children, [])
        status = Control(self.other).status()
        self.assertEqual((status['state'], status['code']), ('error', 'listener_exited'))

    def test_crash_after_spawn_leaves_a_child_the_owner_stop_can_still_reach(self):
        self.child_ready = False
        self.at_step('spawned', self.crash)
        with self.assertRaises(Crash): self.start()
        child = self.children[0]
        self.assertTrue(child.alive())
        self.owner_stop()
        self.assertEqual(self.stopped_run(), child.run_id)
        child.poll()
        self.assertFalse(child.alive())

    def test_crash_right_after_stop_leaves_the_child_stopping_by_itself(self):
        self.child_ready = False
        def stop_then_crash():
            self.owner_stop()
            raise Crash()
        self.at_step('spawned', stop_then_crash)
        with self.assertRaises(Crash): self.start()
        self.children[0].poll()
        self.assertFalse(self.children[0].alive())

    def test_stop_rows_are_one_transaction(self):
        # Failing the write leaves neither half: no revoked reception without the stop flag.
        from quorum_listener import Control, STOP_INTENT, control_lock
        self.other.setting('listener', {'run_id':str(uuid.uuid4()), 'state':'waiting'})
        self.other.setting('session-reception', {'enabled':True})
        self.other.db.execute('create trigger refuse before update on settings '
                              "when new.key='session-reception' begin select raise(abort,'x'); end")
        self.other.db.commit()
        with control_lock(self.other):
            with self.assertRaises(Exception):
                Control(self.other, holding_control=True).owner_stop(revoke_reception=True)
        self.assertIsNone(self.other.setting(STOP_INTENT))
        self.assertIsNone(self.other.setting('listener-stop'))


class OtherStartersYieldToStop(unittest.TestCase):
    def setUp(self):
        from quorum_state import LocalLedger
        temporary = self.enterContext(tempfile.TemporaryDirectory())
        self.root = Path(temporary).resolve()
        self.ledger = LocalLedger(self.root)
        self.addCleanup(self.ledger.db.close)
        self.other = LocalLedger(self.root)
        self.addCleanup(self.other.db.close)
        self.children = []
        self.addCleanup(lambda: [child.close() for child in self.children])

    def stop_during_spawn(self):
        from quorum_listener import stop_listener
        native = subprocess.Popen
        def spawn(command, **kwargs):
            if '--run-id' not in command: return native(command, **kwargs)
            stop_listener(self.other)
            child = Child(self.root, command[command.index('--run-id') + 1], ready=False)
            self.children.append(child)
            return child
        return patch('quorum_listener.subprocess.Popen', side_effect=spawn)

    def assert_stopped(self):
        self.assertEqual(len(self.children), 1)
        self.assertFalse(self.children[0].alive())

    def test_pilot_listener_start(self):
        from quorum_listener import start_listener
        with patch('quorum_live.api_for', return_value=(Api(lambda _: None), None, None)), self.stop_during_spawn():
            with self.assertRaisesRegex(ValueError, '^listener_start_stopped$'):
                start_listener(self.ledger, str(self.root), 60, True)
        self.assert_stopped()

    def test_grok_cloud_relay_start(self):
        import quorum_grok_cloud
        with patch.object(quorum_grok_cloud, 'command', side_effect=lambda _, *arguments: ['quorum', *arguments]), \
             self.stop_during_spawn():
            with self.assertRaisesRegex(ValueError, '^listener_start_stopped$'):
                quorum_grok_cloud.start(SimpleNamespace(root=self.root), self.ledger, Api(lambda _: None))
        self.assert_stopped()

    def test_status_keeps_a_published_run_while_a_start_is_in_progress(self):
        from quorum_listener import Control, START_LOCK
        from quorum_os import FileLock
        control = Control(self.ledger)
        with FileLock(self.root / START_LOCK):
            control.begin(str(uuid.uuid4()), 0)
            self.assertEqual(control.status()['state'], 'starting')
            self.assertEqual(Control(self.other, holding_control=True).status()['state'], 'starting')
        self.assertEqual(control.status()['code'], 'listener_exited')

    def test_new_failure_code_crosses_the_cli_boundary(self):
        import contextlib
        import io
        import quorum
        output = io.StringIO()
        with patch('quorum.LocalLedger', side_effect=ValueError('listener_start_stopped')), \
             contextlib.redirect_stdout(output):
            quorum.main(['--state', str(self.root), 'status'])
        self.assertEqual(json.loads(output.getvalue())['code'], 'listener_start_stopped')


if __name__ == '__main__':
    unittest.main()
