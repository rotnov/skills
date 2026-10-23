"""Release ownership regressions use disposable ledgers and real local locks."""
from contextlib import ExitStack
import json
import os
from pathlib import Path
import sys
import tempfile
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import patch
from quorum_test_support import isolated_profiles
from test_quorum_release import AID, Backend, MID, RELEASE, SID

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'skills/quorum/scripts'))


class ReleaseOwnershipTests(unittest.TestCase):
    @isolated_profiles
    def test_losing_native_run_preserves_active_marker_control_and_stop_state(self):
        self.check_losing_run(native=True)

    @isolated_profiles
    def test_losing_legacy_run_preserves_active_marker_control_and_stop_state(self):
        self.check_losing_run(native=False)

    def check_losing_run(self, *, native):
        from quorum_listener import Control, dispatch as listen
        from quorum_os import FileLock
        from quorum_release import ReleaseGuard
        from quorum_session import dispatch as session
        from quorum_state import LocalLedger
        for hold_supervisor in (False, True):
            with self.subTest(hold_supervisor=hold_supervisor), tempfile.TemporaryDirectory() as root:
                auth = LocalLedger(Path(root))
                ledger = LocalLedger(auth.root / 'sessions' / SID) if native else auth
                backend = Backend()
                try:
                    ledger.setting('session-agent', {'engine':'codex', 'agent_id':AID, 'session_ref':SID})
                    Control(ledger).begin('active-run', 0 if native else 60)
                    ledger.setting('listener-release', {**RELEASE, 'run_id':'active-run'})
                    before = ledger.setting('listener')
                    args = SimpleNamespace(action='run', session_id=SID, run_id='duplicate-run',
                                           toolchain=Path(root), timeout=60, approve_subscription=True)
                    with ExitStack() as stack:
                        stack.enter_context(FileLock(ledger.root / 'listener.lock'))
                        if hold_supervisor:
                            stack.enter_context(FileLock(ledger.root / 'supervisor.lock'))
                        stack.enter_context(patch('signal.signal'))
                        stack.enter_context(patch('quorum_live.api_for', return_value=(backend, None, None)))
                        stack.enter_context(patch('quorum_live.prepare_worker', side_effect=AssertionError('worker')))
                        with self.assertRaisesRegex(ValueError, '^worker_already_running$'):
                            session(args, auth, None) if native else listen(args, ledger)
                        self.assertEqual(ledger.setting('listener-release'), {**RELEASE, 'run_id':'active-run'})
                        self.assertEqual(ledger.setting('listener'), before)
                        self.assertIsNone(ledger.setting('listener-stop'))
                        guard = ReleaseGuard(backend, ledger)
                        guard.check()
                        guard.running()
                        self.assertFalse(Control(ledger).stopping('active-run'))
                finally:
                    if native: ledger.db.close()
                    auth.db.close()

    @isolated_profiles
    def test_winning_native_run_publishes_its_marker_before_worker_preparation(self):
        from quorum_listener import Control, locked
        from quorum_session import dispatch
        from quorum_state import LocalLedger
        with tempfile.TemporaryDirectory() as root:
            auth = LocalLedger(Path(root)); ledger = LocalLedger(auth.root / 'sessions' / SID)
            observations = []
            class Idle:
                def reconcile(self): pass
                def run_once(self): Control(ledger).request_stop(); return 'idle'
            def make(*args):
                observations.append((ledger.setting('listener-release'), ledger.setting('listener')['run_id'],
                                     locked(ledger.root / 'listener.lock'), locked(ledger.root / 'supervisor.lock')))
                return Idle()
            waiter = SimpleNamespace(prepare=lambda _:None, pulse=lambda:None, wait=lambda _:None, close=lambda:None)
            try:
                ledger.setting('session-agent', {'engine':'codex', 'agent_id':AID, 'session_ref':SID})
                with patch('quorum_live.api_for', return_value=(Backend(), None, None)), \
                        patch('quorum_session.Relay', side_effect=make), patch('signal.signal'), \
                        patch('quorum_realtime.RealtimeWait', return_value=waiter):
                    result = dispatch(SimpleNamespace(action='run', session_id=SID, run_id='winning-run'), auth, None)
                self.assertEqual(observations, [({**RELEASE, 'run_id':'winning-run'}, 'winning-run', True, True)])
                self.assertEqual((result['listener'], result['state']), ('offline', 'stopped'))
            finally: ledger.db.close(); auth.db.close()

    def test_runtime_publication_never_exposes_a_mixed_marker_control_pair(self):
        from quorum_listener import Control, run_listener
        from quorum_state import LocalLedger
        with tempfile.TemporaryDirectory() as root:
            ledger = LocalLedger(Path(root)); observer = LocalLedger(Path(root)); snapshots = []
            try:
                Control(ledger).begin('prior-run', 0)
                ledger.setting('listener-release', {**RELEASE, 'run_id':'prior-run'})
                def observe(statement):
                    rows = observer.db.execute("select key,data from settings where key in ('listener','listener-release')")
                    snapshot = {key:json.loads(value) for key,value in rows}
                    snapshots.append((snapshot['listener']['run_id'], snapshot['listener-release']['run_id']))
                ledger.db.set_trace_callback(observe)
                stopped = threading.Event(); stopped.set()
                result = run_listener(ledger, lambda:self.fail('stopped run prepared a worker'),
                                      'winning-run', stop_event=stopped, timeout=0)
                ledger.db.set_trace_callback(None)
                self.assertTrue(all(control == marker for control,marker in snapshots), snapshots)
                self.assertEqual(ledger.setting('listener-release'), {**RELEASE, 'run_id':'winning-run'})
                self.assertIn(('prior-run', 'prior-run'), snapshots)
                self.assertIn(('winning-run', 'winning-run'), snapshots)
                self.assertFalse(result['stop_requested'])
            finally: ledger.db.close(); observer.db.close()

    def test_running_guard_reads_one_snapshot_during_atomic_run_replacement(self):
        from quorum_listener import Control
        from quorum_os import FileLock
        from quorum_release import ReleaseGuard, ReleaseMismatch
        from quorum_state import LocalLedger
        with tempfile.TemporaryDirectory() as root:
            ledger = LocalLedger(Path(root))
            ledger.db.execute('pragma journal_mode=WAL')
            writer = LocalLedger(Path(root)); switched = []
            try:
                Control(ledger).begin('old-active', 0)
                ledger.setting('listener-release', {**RELEASE, 'run_id':'old-active'})
                guard = ReleaseGuard(Backend(), ledger); guard.check()
                def replace_after_first_row(cursor, row):
                    if not switched and any(isinstance(value, str) and 'old-active' in value for value in row):
                        switched.append(True)
                        with writer.db:
                            writer.db.executemany('update settings set data=? where key=?', [
                                (json.dumps({'run_id':'new-active', 'state':'waiting'}), 'listener'),
                                (json.dumps({**RELEASE, 'run_id':'new-active'}), 'listener-release')])
                    return row
                ledger.db.row_factory = replace_after_first_row
                with FileLock(ledger.root / 'listener.lock'):
                    try: guard.running()
                    except ReleaseMismatch: self.fail('guard rejected a compatible pair read across run replacement')
                ledger.db.row_factory = None
                self.assertEqual(switched, [True], 'the replacement must occur during the guard read')
                self.assertEqual(ledger.setting('listener-release'), {**RELEASE, 'run_id':'new-active'})
                self.assertIsNone(ledger.setting('listener-stop'))
            finally: ledger.db.close(); writer.db.close()

    @isolated_profiles
    def test_stale_chat_helpers_reject_without_stopping_verified_newer_run(self):
        from quorum_listener import Control
        from quorum_os import FileLock
        from quorum_release import ReleaseMismatch
        from quorum_session import dispatch
        from quorum_state import LocalLedger
        required = {'protocol_version':1, 'skill_version':'0.3.0'}
        clean = {key:value for key,value in os.environ.items() if key not in
                 ('CODEX_THREAD_ID', 'CLAUDE_CODE_SESSION_ID', 'GROK_SESSION_ID',
                  'CURSOR_AGENT', 'CURSOR_CONVERSATION_ID', 'CURSOR_AGENT_SOCKET', 'CURSOR_API_KEY')}
        clean['CODEX_THREAD_ID'] = SID
        for action in ('received', 'reconcile', 'reply'):
            with self.subTest(action=action), tempfile.TemporaryDirectory() as root:
                auth = LocalLedger(Path(root)); ledger = LocalLedger(auth.root / 'sessions' / SID)
                backend = Backend(required)
                try:
                    ledger.setting('session-agent', {'engine':'codex', 'agent_id':AID, 'session_ref':SID})
                    ledger.setting('delivery:' + MID, {'state':'submitted'})
                    Control(ledger).begin('upgraded-run', 0)
                    ledger.setting('listener-release', {**required, 'run_id':'upgraded-run'})
                    with FileLock(ledger.root / 'listener.lock'), patch.dict(os.environ, clean, clear=True), \
                            patch('quorum_live.api_for', return_value=(backend, None, None)):
                        with self.assertRaises(ReleaseMismatch) as raised:
                            dispatch(SimpleNamespace(action=action, session_id=SID, message_id=MID), auth, None)
                        self.assertFalse(Control(ledger).stopping('upgraded-run'))
                    self.assertEqual(str(raised.exception), 'skill_update_required')
                    self.assertEqual(raised.exception.diagnostic,
                                     {'installed':RELEASE, 'required':required, 'update_required':True,
                                      'code':'skill_update_required'})
                    self.assertEqual(ledger.setting('delivery:' + MID), {'state':'submitted'})
                    self.assertEqual([op for op,_ in backend.calls], ['session_requirements'])
                finally: ledger.db.close(); auth.db.close()

    def test_stale_helper_stops_stale_or_unverified_listener_fail_closed(self):
        from quorum_listener import Control
        from quorum_os import FileLock
        from quorum_release import ReleaseGuard, ReleaseMismatch
        from quorum_state import LocalLedger
        required = {'protocol_version':1, 'skill_version':'0.3.0'}
        cases = (
            ('stale', {**RELEASE, 'run_id':'active-run'}, True, required),
            ('missing', None, True, required),
            ('malformed', {'protocol_version':True, 'skill_version':'0.3.0', 'run_id':'active-run'}, True, required),
            ('missing-run', required, True, required),
            ('other-run', {**required, 'run_id':'previous-run'}, True, required),
            ('no-runtime-lock', {**required, 'run_id':'active-run'}, False, required),
            ('unknown-required', {**required, 'run_id':'active-run'}, True, {}),
        )
        for case, marker, hold_lock, server_required in cases:
            with self.subTest(case=case), tempfile.TemporaryDirectory() as root:
                ledger = LocalLedger(Path(root))
                try:
                    Control(ledger).begin('active-run', 0)
                    if marker is not None: ledger.setting('listener-release', marker)
                    with ExitStack() as stack:
                        if hold_lock: stack.enter_context(FileLock(ledger.root / 'listener.lock'))
                        with self.assertRaises(ReleaseMismatch):
                            ReleaseGuard(Backend(server_required), ledger).check()
                        self.assertTrue(Control(ledger).stopping('active-run'))
                finally: ledger.db.close()
