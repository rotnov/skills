"""Synthetic Claude resume boundaries; never run a provider or contact a service."""
import json
import contextlib
import io
import os
from pathlib import Path
import socket
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch
import uuid

from quorum_test_support import isolated_profiles
from test_quorum_session import Generation, without_client_session, own_endpoint

SCRIPTS = Path(__file__).resolve().parents[1] / 'skills/quorum/scripts'
sys.path.insert(0, str(SCRIPTS))
SID = '11111111-1111-4111-8111-111111111111'
MID = '22222222-2222-4222-8222-222222222222'
ATTEMPT = '33333333-3333-4333-8333-333333333333'
LATER = '44444444-4444-4444-8444-444444444444'


class Api:
    def __init__(self):
        self.calls = []
        self.messages = [{'id':MID, 'recipient_id':'native-agent'}]
    def call(self, operation, payload, request_id=None):
        self.calls.append(operation)
        if operation == 'session_requirements': return {'protocol_version':2, 'skill_version':'0.3.0'}
        if operation == 'session_inbox': return self.messages
        if operation == 'session_ack': return {'queued':True}
        raise AssertionError(operation)


class ClaudeResumeTests(unittest.TestCase):
    def setUp(self):
        from quorum_state import LocalLedger
        isolated = patch.dict(os.environ, without_client_session(), clear=True)
        isolated.start()
        self.addCleanup(isolated.stop)
        temporary = tempfile.TemporaryDirectory(prefix='qresume-', dir='/tmp' if os.name == 'posix' else None)
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.ledger = LocalLedger(self.root/'sessions'/SID)
        self.addCleanup(self.ledger.db.close)
        self.api = Api()
        registration = {'engine':'claude', 'session_ref':SID, 'project_ref':'shared', 'cwd':str(self.root)}
        self.record = {**registration, 'registration':registration, 'agent_id':'native-agent',
                       'device_id':'device', 'quorum_id':'quorum-id', 'role':'claude-test', 'quorum':'team',
                       'messaging_socket':str(self.root/'inbox.sock'),
                       'delegation':{'coordinator_id':'coordinator', 'coordinator_role':'coordinator',
                                     'dispatch_mode':'inline'}}
        self.ledger.setting('session-agent', self.record)
        env = patch.dict(os.environ, {'CLAUDE_CODE_SESSION_ID':SID,
                         'CLAUDE_CODE_MESSAGING_SOCKET':self.record['messaging_socket'],
                         'CLAUDE_CODE_MESSAGING_TOKEN':'synthetic-token'})
        env.start()
        self.addCleanup(env.stop)

    def dispatch(self, action='reconcile', **extra):
        from quorum_session import dispatch
        values = dict(session_id=SID, action=action, message_id=MID,
                      confirmed_absent=True, confirmed_queued=False, attempt_id=ATTEMPT)
        args = SimpleNamespace(**{**values, **extra})
        with patch('quorum_live.api_for', return_value=(self.api, None, None)), \
             patch('quorum_session.Path.cwd', return_value=self.root):
            return dispatch(args, SimpleNamespace(root=self.root), None)

    def intent(self, attempt=ATTEMPT):
        value = {'state':'intent', 'attempt_id':attempt}
        self.ledger.setting('delivery:'+MID, value)
        return value

    @isolated_profiles
    def test_definite_unsent_clears_own_intent_without_ack_then_explicit_new_run_reads_same_message(self):
        from quorum_claude_pipe import DeliveryUnsent
        from quorum_session import Relay
        writes = []
        def offline(message):
            writes.append(message['id'])
            raise DeliveryUnsent()
        relay = Relay(self.api, self.ledger, self.record, offline)
        with self.assertRaisesRegex(ValueError, '^session_delivery_unsent$'): relay.run_once()
        self.assertIsNone(self.ledger.setting('delivery:'+MID))
        self.assertNotIn('session_ack', self.api.calls)
        restarted = Relay(self.api, self.ledger, self.record,
                          lambda message:writes.append(message['id']) or 'submitted')
        self.assertEqual(restarted.run_once(), 'idle')
        self.assertEqual(writes, [MID, MID])
        self.assertEqual(self.ledger.setting('delivery:'+MID), {'state':'submitted'})

    @isolated_profiles
    def test_unsent_cleanup_preserves_concurrent_receipt_and_later_attempt(self):
        from quorum_claude_pipe import DeliveryUnsent
        from quorum_session import Relay
        from quorum_state import LocalLedger
        receiver = LocalLedger(self.ledger.root)
        self.addCleanup(receiver.db.close)
        for concurrent in ({'state':'queued'}, {'state':'acked'}, {'state':'intent', 'attempt_id':LATER}):
            with self.subTest(concurrent=concurrent):
                self.ledger.db.execute('delete from settings where key=?', ('delivery:'+MID,))
                self.ledger.db.commit()
                def queue(_):
                    receiver.setting('delivery:'+MID, concurrent)
                    raise DeliveryUnsent()
                with self.assertRaisesRegex(ValueError, '^session_delivery_unsent$'):
                    Relay(self.api, self.ledger, self.record, queue).run_once()
                self.assertEqual(self.ledger.setting('delivery:'+MID), concurrent)
        self.assertNotIn('session_ack', self.api.calls)

    @isolated_profiles
    def test_arbitrary_queue_error_keeps_unique_unknown_attempt_and_never_requeues(self):
        from quorum_session import Relay
        writes = []
        def queue(_):
            writes.append(MID)
            raise ValueError('claude_pipe_unavailable')
        relay = Relay(self.api, self.ledger, self.record, queue)
        with self.assertRaisesRegex(ValueError, '^session_delivery_unknown$'): relay.run_once()
        saved = self.ledger.setting('delivery:'+MID)
        self.assertEqual(saved['state'], 'intent')
        self.assertEqual(str(uuid.UUID(saved['attempt_id'])), saved['attempt_id'])
        for operation in (relay.reconcile, relay.run_once):
            with self.assertRaisesRegex(ValueError, '^session_delivery_unknown$'): operation()
        self.assertEqual(writes, [MID])
        self.assertEqual(self.ledger.setting('delivery:'+MID), saved)

    @isolated_profiles
    def test_submission_cas_never_acks_a_later_unknown_attempt(self):
        from quorum_session import Relay
        later = {'state':'intent', 'attempt_id':LATER}
        def queue(_):
            self.ledger.setting('delivery:'+MID, later)
            return 'submitted'
        with self.assertRaisesRegex(ValueError, '^session_delivery_unknown$'):
            Relay(self.api, self.ledger, self.record, queue).run_once()
        self.assertEqual(self.ledger.setting('delivery:'+MID), later)
        self.assertNotIn('session_ack', self.api.calls)

    @isolated_profiles
    def test_authority_expiry_cleanup_uses_exact_attempt_cas(self):
        from quorum_session import Relay
        later = {'state':'intent', 'attempt_id':LATER}
        def authorize():
            if self.ledger.setting('delivery:'+MID):
                self.ledger.setting('delivery:'+MID, later)
                raise ValueError('session_recovery_denied')
        with self.assertRaisesRegex(ValueError, '^session_recovery_denied$'):
            Relay(self.api, self.ledger, self.record, lambda _:self.fail('no queue'), authorize=authorize).run_once()
        self.assertEqual(self.ledger.setting('delivery:'+MID), later)
        self.assertNotIn('session_ack', self.api.calls)

    @unittest.skipUnless(os.name == 'posix' and hasattr(socket, 'AF_UNIX'), 'Unix socket route')
    @isolated_profiles
    def test_parent_binding_failure_before_helper_is_unsent_without_weakening_guards(self):
        from quorum_claude_pipe import DeliveryUnsent
        from quorum_session import enqueue
        for changes in ({}, {'CLAUDE_CODE_MESSAGING_TOKEN':''},
                        {'CLAUDE_CODE_MESSAGING_SOCKET':'relative.sock'},
                        {'CLAUDE_CODE_SESSION_ID':MID}):
            with self.subTest(changes=changes), patch.dict(os.environ, changes), \
                 patch('quorum_claude_pipe.submit', side_effect=AssertionError('must not invoke helper')):
                with self.assertRaises(DeliveryUnsent):
                    enqueue(self.record, self.api.messages[0], self.root)

    @unittest.skipUnless(os.name == 'posix' and hasattr(socket, 'AF_UNIX'), 'Unix socket route')
    @isolated_profiles
    def test_listener_route_loss_stops_before_inbox_or_intent(self):
        from quorum_listener import run_listener
        from quorum_session import Relay, check_native_route
        endpoint = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.addCleanup(endpoint.close)
        endpoint.bind(self.record['messaging_socket'])
        os.chmod(self.record['messaging_socket'], 0o600)
        relay = Relay(self.api, self.ledger, self.record, lambda _:self.fail('no write'),
                      route_check=lambda:check_native_route(self.record))
        relay.reconcile()
        self.api.calls.clear()
        os.unlink(self.record['messaging_socket'])
        result = run_listener(self.ledger, lambda:relay, SID, sleep=lambda _:None, timeout=0)
        self.assertEqual((result['listener'], result['code']), ('offline', 'claude_pipe_unavailable'))
        self.assertNotIn('session_inbox', self.api.calls)
        self.assertIsNone(self.ledger.setting('delivery:'+MID))

    @isolated_profiles
    def test_connect_preserves_endpoint_until_start_stops_old_generation(self):
        from quorum_session import dispatch
        old = Generation(self.ledger.root)
        self.addCleanup(old.close)
        self.ledger.setting('delivery:'+MID, {'state':'submitted'})
        endpoint = str(self.root/'rotated.sock')
        spawned = []
        children = []
        def spawn(command, ledger):
            spawned.append(old.thread.is_alive())
            child = Generation(ledger.root, command[command.index('--run-id')+1])
            children.append(child)
            self.addCleanup(child.close)
            return child
        args = SimpleNamespace(session_id=SID, action='connect', quorum='team', role='claude-test',
                               project_ref='shared', accept_tasks_from=None, dispatch_mode='inline')
        def register(*_args, **_kwargs):
            return {key:self.record[key] for key in ('agent_id', 'device_id', 'quorum_id')}
        with patch.dict(os.environ, {'CLAUDE_CODE_MESSAGING_SOCKET':endpoint}), \
             patch('quorum_claude_pipe.binding', side_effect=own_endpoint), \
             patch('quorum_live.api_for', return_value=(self.api, None, None)), \
             patch('quorum_session.Path.cwd', return_value=self.root), \
             patch('quorum_session.spawn_background', side_effect=spawn):
            dispatch(args, SimpleNamespace(root=self.root), register)
            self.assertEqual(self.ledger.setting('session-agent')['messaging_socket'], self.record['messaging_socket'])
            self.assertTrue(old.thread.is_alive())
            args.action = 'start'
            result = dispatch(args, SimpleNamespace(root=self.root), register)
        self.assertEqual(spawned, [False])
        self.assertTrue(result['messaging_rebound'])
        self.assertEqual(self.ledger.setting('session-agent'), {**self.record, 'messaging_socket':endpoint})
        self.assertEqual(self.ledger.setting('delivery:'+MID), {'state':'submitted'})

    @isolated_profiles
    def test_connect_cannot_restore_endpoint_rebound_during_registration(self):
        from quorum_session import dispatch
        endpoint = str(self.root/'rotated.sock')
        args = SimpleNamespace(session_id=SID, action='connect', quorum='team', role='claude-test',
                               project_ref='shared', accept_tasks_from=None, dispatch_mode='inline')
        def register(*_args, **_kwargs):
            # Another serialized start completed while registration was in flight.
            self.ledger.setting('session-agent', {**self.record, 'messaging_socket':endpoint})
            return {key:self.record[key] for key in ('agent_id','device_id','quorum_id')}
        with patch.dict(os.environ, {'CLAUDE_CODE_MESSAGING_SOCKET':endpoint}), \
             patch('quorum_claude_pipe.binding', side_effect=own_endpoint), \
             patch('quorum_live.api_for', return_value=(self.api,None,None)), \
             patch('quorum_session.Path.cwd', return_value=self.root):
            dispatch(args, SimpleNamespace(root=self.root), register)
        self.assertEqual(self.ledger.setting('session-agent')['messaging_socket'], endpoint)

    @isolated_profiles
    def test_confirmed_absent_clears_exact_attempt_without_ack_or_start_and_is_aba_safe(self):
        self.intent()
        first = self.dispatch()
        self.assertTrue(first['confirmed_absent'])
        self.assertIsNone(self.ledger.setting('delivery:'+MID))
        marker = self.ledger.setting('delivery-absent:'+MID+':'+ATTEMPT)
        self.assertEqual({key:marker[key] for key in ('state','message_id','attempt_id')},
                         {'state':'confirmed_absent', 'message_id':MID, 'attempt_id':ATTEMPT})
        self.assertEqual(marker['binding']['agent_id'], self.record['agent_id'])
        self.assertNotIn('session_ack', self.api.calls)
        self.assertIsNone(self.ledger.setting('listener'))
        later = self.intent(LATER)
        self.assertTrue(self.dispatch()['confirmed_absent'])
        self.assertEqual(self.ledger.setting('delivery:'+MID), later)

    @isolated_profiles
    def test_absence_allows_one_new_attempt_and_old_command_cannot_clear_it(self):
        from quorum_session import Relay
        self.intent()
        self.dispatch()
        calls = []
        def queue(_):
            calls.append(MID)
            raise ValueError('session_delivery_unknown')
        relay = Relay(self.api, self.ledger, self.record, queue)
        with self.assertRaisesRegex(ValueError, '^session_delivery_unknown$'): relay.run_once()
        new_intent = self.ledger.setting('delivery:'+MID)
        self.assertNotEqual(new_intent['attempt_id'], ATTEMPT)
        self.dispatch()
        with self.assertRaisesRegex(ValueError, '^session_delivery_unknown$'): relay.run_once()
        self.assertEqual(self.ledger.setting('delivery:'+MID), new_intent)
        self.assertEqual(calls, [MID])

    @isolated_profiles
    def test_absence_marker_and_deletion_commit_together_or_roll_back(self):
        self.intent()
        connection = self.ledger.db
        class Interrupted:
            def __enter__(self): return connection.__enter__()
            def __exit__(self, *args): return connection.__exit__(*args)
            def execute(self, sql, parameters=()):
                if sql == 'insert into settings values (?,?)' and parameters[0].startswith('delivery-absent:'):
                    raise RuntimeError('synthetic interruption before commit')
                return connection.execute(sql, parameters)
            def __getattr__(self, name): return getattr(connection, name)
        # dispatch opens a distinct connection, so exercise its actual helper on
        # this wrapped connection to interrupt between the two transaction writes.
        from quorum_session import reconcile_absent
        with patch('quorum_session.Path.cwd', return_value=self.root):
            self.ledger.db = Interrupted()
            try:
                with self.assertRaisesRegex(RuntimeError, 'synthetic interruption'):
                    reconcile_absent(self.api, self.ledger, self.record, MID, ATTEMPT)
            finally: self.ledger.db = connection
        self.assertEqual(self.ledger.setting('delivery:'+MID), {'state':'intent', 'attempt_id':ATTEMPT})
        self.assertIsNone(self.ledger.setting('delivery-absent:'+MID+':'+ATTEMPT))
        self.dispatch()
        # Reopen independently after commit: the audit survives process loss,
        # and a repeated old command remains harmless to the next attempt.
        from quorum_state import LocalLedger
        reopened = LocalLedger(self.ledger.root)
        try:
            self.assertIsNotNone(reopened.setting('delivery-absent:'+MID+':'+ATTEMPT))
            reopened.setting('delivery:'+MID, {'state':'intent', 'attempt_id':LATER})
        finally: reopened.db.close()
        self.dispatch()
        self.assertEqual(self.ledger.setting('delivery:'+MID)['attempt_id'], LATER)

    @isolated_profiles
    def test_absence_refuses_legacy_arbitrary_submitted_and_wrong_attempt(self):
        for previous in (None, {'state':'intent'}, {'state':'submitted'}, {'state':'queued'},
                         {'state':'acked'}, {'state':'intent', 'attempt_id':LATER}):
            with self.subTest(previous=previous):
                self.ledger.db.execute('delete from settings where key=?', ('delivery:'+MID,))
                self.ledger.db.commit()
                if previous is not None: self.ledger.setting('delivery:'+MID, previous)
                with self.assertRaisesRegex(ValueError, '^session_reconcile_denied$'): self.dispatch()
                self.assertEqual(self.ledger.setting('delivery:'+MID), previous)
        self.assertNotIn('session_ack', self.api.calls)

    @isolated_profiles
    def test_absence_requires_attempt_id_exact_cwd_caller_and_release(self):
        from quorum_session import dispatch
        self.intent()
        with self.assertRaisesRegex(ValueError, '^session_reconcile_denied$'): self.dispatch(attempt_id=None)
        with patch('quorum_live.api_for', return_value=(self.api, None, None)):
            with self.assertRaisesRegex(ValueError, '^session_binding_denied$'):
                dispatch(SimpleNamespace(session_id=SID, action='reconcile', message_id=MID,
                         confirmed_absent=True, attempt_id=ATTEMPT), SimpleNamespace(root=self.root), None)
        with patch.dict(os.environ, {'CLAUDE_CODE_SESSION_ID':MID}):
            with self.assertRaisesRegex(ValueError, '^session_binding_denied$'): self.dispatch()
        self.ledger.setting('session-agent', {**self.record, 'engine':'codex'})
        with self.assertRaisesRegex(ValueError, '^session_binding_denied$'): self.dispatch()
        self.ledger.setting('session-agent', self.record)
        original = self.api.call
        self.api.call = lambda op, data, request_id=None: ({'protocol_version':2, 'skill_version':'99.0.0'}
                        if op == 'session_requirements' else original(op, data, request_id))
        with self.assertRaisesRegex(Exception, '^skill_update_required$'): self.dispatch()
        self.assertEqual(self.ledger.setting('delivery:'+MID)['attempt_id'], ATTEMPT)

    @isolated_profiles
    def test_absence_requires_listener_and_supervisor_quiescence(self):
        from quorum_os import FileLock
        self.intent()
        for lock in ('listener-control.lock', 'supervisor.lock', 'listener.lock'):
            with self.subTest(lock=lock), FileLock(self.ledger.root/lock):
                with self.assertRaisesRegex(ValueError, 'worker_already_running|listener_stop_pending'):
                    self.dispatch()
                self.assertEqual(self.ledger.setting('delivery:'+MID)['attempt_id'], ATTEMPT)
        self.assertNotIn('session_ack', self.api.calls)

    @isolated_profiles
    def test_offline_status_exposes_only_unknown_message_and_attempt_ids(self):
        self.intent()
        result = self.dispatch('status')
        self.assertEqual(result['unknown_attempts'], [{'message_id':MID, 'attempt_id':ATTEMPT}])
        self.assertEqual(self.api.calls, [])
        self.assertNotIn('synthetic-token', json.dumps(result))

    @isolated_profiles
    def test_absence_fails_closed_if_original_is_not_current_unreceived_inbox_head(self):
        self.intent()
        for messages in ([], [{'id':LATER, 'recipient_id':'native-agent'}],
                         [{'id':MID, 'recipient_id':'other-agent'}]):
            with self.subTest(messages=messages):
                self.api.messages = messages
                with self.assertRaisesRegex(ValueError, '^session_reconcile_denied$'): self.dispatch()
                self.assertEqual(self.ledger.setting('delivery:'+MID)['attempt_id'], ATTEMPT)

    @isolated_profiles
    def test_absence_cas_preserves_receipt_committed_during_service_check(self):
        from quorum_state import LocalLedger
        self.intent()
        receiver = LocalLedger(self.ledger.root)
        self.addCleanup(receiver.db.close)
        call = self.api.call
        def receipt(operation, payload, request_id=None):
            if operation == 'session_inbox': receiver.setting('delivery:'+MID, {'state':'acked'})
            return call(operation, payload, request_id)
        self.api.call = receipt
        with self.assertRaisesRegex(ValueError, '^session_reconcile_denied$'): self.dispatch()
        self.assertEqual(self.ledger.setting('delivery:'+MID), {'state':'acked'})
        self.assertIsNone(self.ledger.setting('delivery-absent:'+MID+':'+ATTEMPT))
        self.assertNotIn('session_ack', self.api.calls)

    @isolated_profiles
    def test_reconcile_cli_has_mutually_exclusive_evidence_and_requires_absence_attempt(self):
        import quorum
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            quorum.main(['--state', str(self.root), 'session', 'reconcile', MID,
                         '--confirmed-queued', '--confirmed-absent', '--attempt-id', ATTEMPT])
        with patch('quorum_live.api_for', return_value=(self.api, None, None)), \
             patch('quorum_session.Path.cwd', return_value=self.root), \
             contextlib.redirect_stdout(io.StringIO()) as output:
            code = quorum.main(['--state', str(self.root), 'session', 'reconcile', MID, '--confirmed-absent'])
        self.assertNotEqual(code, 0)
        self.assertEqual(json.loads(output.getvalue())['code'], 'session_reconcile_denied')


if __name__ == '__main__': unittest.main()
