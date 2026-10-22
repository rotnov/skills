"""Continuous listener checks use synthetic peers and disposable state only."""
import json
import os
from contextlib import contextmanager
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
import uuid
from unittest.mock import patch
from quorum_test_support import isolated_profiles

SCRIPTS = Path(__file__).resolve().parents[1] / 'skills/quorum/scripts'
sys.path.insert(0, str(SCRIPTS))


@contextmanager
def temporary_ledger():
    from quorum_state import LocalLedger
    with tempfile.TemporaryDirectory() as temporary:
        ledger = LocalLedger(Path(temporary))
        try:
            yield temporary, ledger
        finally:
            ledger.db.close()


class RequirementsApi:
    def call(self, operation, payload):
        assert operation == 'session_requirements'
        return {'protocol_version':2, 'skill_version':'0.3.0'}


class ListenerTests(unittest.TestCase):
    def test_stop_waits_for_an_in_progress_start_to_publish_its_run(self):
        from types import SimpleNamespace
        from quorum_listener import Control, dispatch
        from quorum_os import FileLock
        with temporary_ledger() as (_, ledger):
            control, run_id = Control(ledger), str(uuid.uuid4())
            guard = FileLock(ledger.root / 'listener-control.lock')
            guard.__enter__()
            released = False
            def finish_start(_):
                nonlocal released
                control.begin(run_id, 60)
                guard.__exit__()
                released = True
            try:
                with patch('quorum_listener.time.sleep', side_effect=finish_start):
                    result = dispatch(SimpleNamespace(action='stop'), ledger)
                self.assertTrue(released, 'stop must serialize with the unfinished start')
                self.assertEqual(result['run_id'], run_id)
                self.assertTrue(result['stop_requested'])
            finally:
                if not released: guard.__exit__()

    def test_start_allows_a_slow_successful_full_preflight(self):
        from quorum_listener import Control, start_listener
        with temporary_ledger() as (temporary, ledger):
            clock = [0]
            native_popen = subprocess.Popen
            class Starting:
                def poll(self): return None
            def spawn(command, **kwargs):
                if '--run-id' not in command: return native_popen(command, **kwargs)
                return Starting()
            def status(_):
                record = ledger.setting('listener')
                return {**record, 'listener': 'running', 'stop_requested': False,
                        'state': 'waiting' if clock[0] >= 60 else 'starting'}
            def sleep(_): clock[0] += 10
            with patch('quorum_live.api_for', return_value=(RequirementsApi(), None, None)), \
                 patch('quorum_listener.subprocess.Popen', side_effect=spawn), \
                 patch('quorum_listener.time.monotonic', side_effect=lambda: clock[0]), \
                 patch('quorum_listener.time.sleep', side_effect=sleep), \
                 patch.object(Control, 'status', status):
                result = start_listener(ledger, temporary, 60, True)
            self.assertEqual(result['state'], 'waiting')
            self.assertGreaterEqual(clock[0], 60)

    def test_stop_during_spawn_cancels_before_worker_preparation(self):
        from quorum_listener import Control, run_listener, start_listener
        from quorum_state import LocalLedger
        from quorum_api import QuorumError
        with temporary_ledger() as (temporary, ledger):
            control, calls, acknowledgements = Control(ledger), [], []
            class Worker:
                def reconcile(self): return []
                def run_once(self):
                    calls.append('processed_after_stop')
                    control.request_stop()
                    return 'answered'
            def prepare():
                calls.append('prepared_after_stop')
                return Worker()
            class Exited:
                def poll(self): return 0
            native_popen = subprocess.Popen
            def spawn(command, **kwargs):
                if '--run-id' not in command: return native_popen(command, **kwargs)
                acknowledgements.append(control.request_stop())
                run_id = command[command.index('--run-id') + 1]
                run_listener(ledger, prepare, run_id)
                return Exited()
            with patch('quorum_live.api_for', return_value=(RequirementsApi(), None, None)), \
                    patch('quorum_listener.subprocess.Popen', side_effect=spawn):
                with self.assertRaisesRegex(QuorumError, 'listener_start_failed'):
                    start_listener(ledger, temporary, 60, True)
            self.assertTrue(acknowledgements[0]['stop_requested'])
            self.assertEqual(calls, [])
            self.assertEqual(control.status()['state'], 'stopped')

    def test_spawn_failure_records_a_safe_offline_error(self):
        from quorum_listener import Control, start_listener
        from quorum_state import LocalLedger
        from quorum_api import QuorumError
        with temporary_ledger() as (temporary, ledger):
            native_popen = subprocess.Popen
            def spawn(command, **kwargs):
                if '--run-id' in command: raise OSError('synthetic-private-token')
                return native_popen(command, **kwargs)
            with patch('quorum_live.api_for', return_value=(RequirementsApi(), None, None)), \
                    patch('quorum_listener.subprocess.Popen', side_effect=spawn):
                with self.assertRaisesRegex(QuorumError, 'listener_start_failed'):
                    start_listener(ledger, temporary, 60, True)
            status = Control(ledger).status()
            self.assertEqual((status['listener'], status['state']), ('offline', 'error'))
            self.assertNotIn('synthetic-private-token', json.dumps(status))

    def test_start_requires_explicit_ongoing_subscription_approval(self):
        with tempfile.TemporaryDirectory() as temporary:
            state = Path(temporary) / 'state'
            result = subprocess.run([sys.executable, str(SCRIPTS / 'quorum.py'), '--state', str(state),
                                     'listen', 'start', '--toolchain', temporary],
                                    capture_output=True, text=True, timeout=5)
            self.assertEqual(result.returncode, 2)
            self.assertFalse(state.exists())

    @isolated_profiles
    def test_unconfigured_background_start_fails_without_leaving_a_listener(self):
        with tempfile.TemporaryDirectory() as temporary:
            command = [sys.executable, str(SCRIPTS / 'quorum.py'), '--state', temporary, 'listen']
            result = subprocess.run(command + ['start', '--toolchain', temporary, '--approve-subscription'],
                                    capture_output=True, text=True, timeout=50)
            self.assertEqual(result.returncode, 4, result.stdout + result.stderr)
            self.assertEqual(json.loads(result.stdout)['code'], 'service_not_configured')
            self.assertFalse((Path(temporary)/'listener.log').exists())
            status = subprocess.run(command + ['status'], capture_output=True, text=True, check=True, timeout=15)
            data = json.loads(status.stdout)['data']
            self.assertEqual((data['listener'], data['state']), ('offline', 'stopped'))

    def test_unconfigured_background_start_cannot_discover_the_default_owner_profile(self):
        # Exercise the actual test/subprocess chain with a synthetic OS home.
        # Canonical temp paths ensure a macOS /var guard cannot hide leakage.
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            home, temp = root / 'synthetic-home', root / 'canonical-temp'
            home.mkdir(mode=0o700)
            temp.mkdir(mode=0o700)
            env = dict(os.environ, HOME=str(home), USERPROFILE=str(home),
                       TMPDIR=str(temp), TEMP=str(temp), TMP=str(temp))
            env.pop('QUORUM_PROFILE_ROOT', None)
            result = subprocess.run([sys.executable, '-m', 'unittest',
                                     'test_quorum_listener.ListenerTests.'
                                     'test_unconfigured_background_start_fails_without_leaving_a_listener'],
                                    cwd=Path(__file__).parent, env=env,
                                    capture_output=True, text=True, timeout=60)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertFalse((home / '.local/share/quorum/machine-profiles').exists(),
                             'the listener fixture discovered the synthetic owner profile')

    def test_two_messages_are_handled_serially_without_reply_loops(self):
        from quorum_listener import Control, run_listener
        from quorum_state import LocalLedger, SessionBinding
        from quorum_worker import Worker
        from quorum_acp import ExecutionPolicy
        with temporary_ledger() as (temporary, ledger):
            control, run_id = Control(ledger), str(uuid.uuid4())
            ledger.bind_session(SessionBinding('q', 'p', 'd', 'a', 'c', 'claude', temporary, 'main'))

            class Api:
                def __init__(self): self.claims, self.replies = 0, []
                def call(self, operation, data, request_id=None):
                    if operation == 'session_requirements': return {'protocol_version':2, 'skill_version':'0.3.0'}
                    if operation == 'inbox_claim':
                        self.claims += 1
                        return {'attempt_id': str(self.claims), 'generation': 1,
                                'delivery': {'conversation_id': 'c', 'body': 'synthetic ping'}}
                    if operation == 'context_read': return []
                    if operation == 'delivery_complete':
                        self.replies.append(data['reply_body'])
                        if len(self.replies) == 2: control.request_stop()
                    return {'state': 'running'}

            class Runner:
                def __init__(self): self.starts = 0
                def start(self, *args):
                    self.starts += 1
                    return object()
                def events(self, handle):
                    yield {'type': 'started'}
                    yield {'type': 'completed', 'success': True, 'text': 'pong'}

            api, runner = Api(), Runner()
            result = run_listener(ledger, lambda: Worker(api, ledger, runner, 'd',
                                  ExecutionPolicy(1, 60, 'subscription', True, True)), run_id)
            self.assertEqual((runner.starts, api.claims, api.replies), (2, 2, ['pong', 'pong']))
            self.assertEqual((result['handled'], result['state'], result['listener']), (2, 'stopped', 'offline'))

    def test_empty_queue_waits_ten_seconds_without_a_model(self):
        from quorum_listener import Control, run_listener
        from quorum_state import LocalLedger
        with temporary_ledger() as (temporary, ledger):
            control, sleeps = Control(ledger), []

            class Idle:
                calls = 0
                def reconcile(self): return []
                def run_once(self):
                    self.calls += 1
                    if self.calls == 2: control.request_stop()
                    return 'idle'

            worker = Idle()
            result = run_listener(ledger, lambda: worker, str(uuid.uuid4()), sleep=sleeps.append)
            self.assertEqual(sum(sleeps), 10)
            self.assertEqual(result['handled'], 0)
            self.assertEqual(worker.calls, 2)

    def test_uncertain_attempt_stops_and_cannot_be_replayed_by_restart(self):
        from quorum_listener import run_listener
        from quorum_state import LocalLedger
        with temporary_ledger() as (temporary, ledger):

            class Uncertain:
                calls = 0
                def reconcile(self): return []
                def run_once(self):
                    self.calls += 1
                    ledger.set_attempt('a', 1, 'unknown')
                    return 'unknown'

            worker = Uncertain()
            first = run_listener(ledger, lambda: worker, str(uuid.uuid4()))
            second = run_listener(ledger, lambda: self.fail('uncertainty must be checked before preflight'), str(uuid.uuid4()))
            self.assertEqual((first['state'], second['state'], worker.calls), ('unknown', 'unknown', 1))

    def test_errors_are_redacted_and_release_the_listener_lock(self):
        from quorum_listener import Control, run_listener
        from quorum_state import LocalLedger
        with temporary_ledger() as (temporary, ledger):
            def failed(): raise ValueError('synthetic-private-token')
            result = run_listener(ledger, failed, str(uuid.uuid4()))
            self.assertEqual(result['state'], 'error')
            self.assertEqual(Control(ledger).status()['listener'], 'offline')
            self.assertNotIn('synthetic-private-token', json.dumps(result))

    def test_existing_pilot_worker_excludes_a_listener(self):
        from quorum_listener import run_listener
        from quorum_live import LocalSupervisorLock
        from quorum_state import LocalLedger
        with temporary_ledger() as (temporary, ledger):
            with LocalSupervisorLock(ledger.root):
                with self.assertRaisesRegex(ValueError, 'worker_already_running'):
                    run_listener(ledger, lambda: self.fail('second worker started'), str(uuid.uuid4()))

    def test_copy_installed_background_start_status_and_stop(self):
        with tempfile.TemporaryDirectory(prefix='quorum listener ') as temporary:
            root = Path(temporary)
            installed = root / 'installed skill'
            shutil.copytree(SCRIPTS.parent, installed, ignore=shutil.ignore_patterns('.venv','__pycache__'))
            # Replace only the copied CLI entrypoint with a provider-free host fixture.
            (installed / 'scripts/quorum.py').write_text('''import argparse, json, sys
from quorum_listener import add_commands, dispatch, run_listener
from quorum_state import LocalLedger
p=argparse.ArgumentParser()
p.add_argument('--state', type=__import__('pathlib').Path, required=True)
add_commands(p.add_subparsers(dest='command', required=True))
a=p.parse_args()
l=LocalLedger(a.state)
class Api:
    def call(self, operation, payload):
        assert operation=='session_requirements'
        return {'protocol_version':2,'skill_version':'0.3.0'}
import quorum_live
quorum_live.api_for=lambda ledger:(Api(),None,None)
class Idle:
    def reconcile(self): return []
    def run_once(self): return 'idle'
try:
    if a.action=='run': l.setting('listener-release',{'protocol_version':2,'skill_version':'0.3.0','run_id':a.run_id})
    result = run_listener(l, lambda: Idle(), a.run_id) if a.action=='run' else dispatch(a,l)
    print(json.dumps(result))
except Exception:
    print(json.dumps({'error':'fixture_failed'}))
    sys.exit(3)
''', encoding='utf-8')
            command = [sys.executable, str(installed / 'scripts/quorum.py'), '--state', str(root / 'state'), 'listen']
            def invoke(*args):
                result = subprocess.run(command + list(args), capture_output=True, text=True, timeout=40)
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                return json.loads(result.stdout)
            started = None
            try:
                started = invoke('start', '--toolchain', str(root), '--approve-subscription')
                status = invoke('status')
                again = invoke('start', '--toolchain', str(root), '--approve-subscription')
                self.assertEqual(started['listener'], 'running')
                self.assertEqual((status['run_id'], again['run_id']), (started['run_id'], started['run_id']))
                self.assertEqual(status['handled'], 0)
                invoke('stop')
                deadline = time.monotonic() + 15
                while time.monotonic() < deadline:
                    stopped = invoke('status')
                    if stopped['listener'] == 'offline': break
                    time.sleep(.1)
                self.assertEqual((stopped['listener'], stopped['state']), ('offline', 'stopped'))
            finally:
                if started:
                    invoke('stop')


if __name__ == '__main__': unittest.main()
