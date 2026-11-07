"""Failure point x client -> final delivery state (#142); never run a provider or contact a service.

`unsent` removes the exact intent and allows one later attempt, `unknown` keeps it for the owner,
and `delivered` is a submitted or acknowledged row. None of the failing paths may acknowledge.
"""
import contextlib
import ctypes.wintypes
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import patch
from urllib.error import HTTPError, URLError
import uuid

from quorum_test_support import isolated_profiles, queue_accepted
from test_quorum_session import without_client_session

SCRIPTS = Path(__file__).resolve().parents[1] / 'skills/quorum/scripts'
sys.path.insert(0, str(SCRIPTS))
SID = '11111111-1111-4111-8111-111111111111'
MID = '22222222-2222-4222-8222-222222222222'
ATTEMPT = '33333333-3333-4333-8333-333333333333'
URGENT = '44444444-4444-4444-8444-444444444444'
OTHER = '55555555-5555-4555-8555-555555555555'
CONVERSATION = '66666666-6666-4666-8666-666666666666'
ENV = {'codex':'CODEX_THREAD_ID', 'claude':'CLAUDE_CODE_SESSION_ID', 'grok':'GROK_SESSION_ID'}
NO_DATA, BROKEN_PIPE = 232, 109


class Crash(BaseException):
    """A process death: nothing after this point runs, so no handler may classify it."""


class Child:
    """A finished child. A clean exit prints the recorded `codex queue` line, which the I/O helpers ignore."""
    def __init__(self, code=0, stdout=None, stderr=b'', failure=None):
        if stdout is None: stdout = queue_accepted(SID) if code == 0 else b''
        self.returncode, self.stdout, self.stderr, self.failure = code, stdout, stderr, failure
    def __enter__(self): return self
    def __exit__(self, *_): return False
    def kill(self): pass
    def communicate(self, data=None, timeout=None):
        if self.failure: raise self.failure
        return self.stdout, self.stderr


class Api:
    def __init__(self, *messages):
        self.calls, self.messages, self.unreceipted = [], list(messages), set()
    def call(self, operation, payload, request_id=None):
        self.calls.append(operation)
        if operation == 'session_requirements': return {'protocol_version':2, 'skill_version':'0.3.0'}
        if operation == 'session_inbox': return self.messages[:1]
        if operation == 'session_ack':
            self.messages = [item for item in self.messages if item['id'] != payload['message_id']]
            return {'queued':True}
        if operation == 'session_reply': return {'reply_message_id':OTHER}
        if operation == 'session_message_unreceipted':
            return {'message_id':payload['message_id'], 'unreceipted':payload['message_id'] in self.unreceipted}
        raise AssertionError(operation)
    def acks(self): return self.calls.count('session_ack')


def message(identity=MID, *, body='peer text', priority='normal'):
    return {'id':identity, 'recipient_id':'agent', 'body':body, 'sender_role':'peer', 'reply_to':None,
            'priority':priority, 'conversation_id':CONVERSATION, 'sequence':3}


class Harness(unittest.TestCase):
    def setUp(self):
        from quorum_state import LocalLedger
        isolated = patch.dict(os.environ, without_client_session(), clear=True)
        isolated.start()
        self.addCleanup(isolated.stop)
        # These rows cover the delivery ledger; test_quorum_client_contracts covers the contract reads.
        for name in ('check_queue', 'check_claude_session'):
            contract = patch('quorum_client_contracts.' + name)
            contract.start()
            self.addCleanup(contract.stop)
        temporary = tempfile.TemporaryDirectory(prefix='qintent-', dir='/tmp' if os.name == 'posix' else None)
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.ledger = LocalLedger(self.root/'sessions'/SID)
        self.addCleanup(self.ledger.db.close)
        self.client = None
        self.addCleanup(self.release_client)

    def bind(self, engine):
        registration = {'engine':engine, 'session_ref':SID, 'project_ref':'shared', 'cwd':str(self.root)}
        record = {**registration, 'registration':registration, 'agent_id':'agent', 'device_id':'device',
                  'quorum_id':'quorum-id', 'role':'test', 'quorum':'team'}
        if engine == 'grok': record['grok_transport'] = {'leader_socket':'/fixture/leader.sock'}
        if engine == 'claude': record['messaging_socket'] = '/fixture/inbox.sock'
        self.ledger.setting('session-agent', record)
        self.release_client()
        self.client = patch.dict(os.environ, {ENV[engine]:SID})
        self.client.start()
        return record

    def release_client(self):
        if self.client: self.client.stop()
        self.client = None

    def relay(self, api, record, queue=None):
        from quorum_session import Relay, enqueue
        return Relay(api, self.ledger, record, queue or (lambda item:enqueue(record, item, self.root)))

    def dispatch(self, api, action, **extra):
        from quorum_session import dispatch
        args = SimpleNamespace(session_id=SID, action=action, message_id=MID, **extra)
        with patch('quorum_live.api_for', return_value=(api, None, None)), \
             patch('quorum_session.Path.cwd', return_value=self.root), \
             patch('quorum_session.sys.stdin', SimpleNamespace(buffer=io.BytesIO(b'answer'))):
            return dispatch(args, SimpleNamespace(root=self.root), None)

    def intent(self, identity=MID):
        value = {'state':'intent', 'attempt_id':ATTEMPT}
        self.ledger.setting('delivery:'+identity, value)
        return value

    def row(self, identity=MID): return self.ledger.setting('delivery:'+identity)


def child(*args, **kwargs):
    return {'return_value':Child(*args, **kwargs)}


SPAWN_FAILED = {'side_effect':OSError(7, 'argument list too long')}
TIMED_OUT = child(failure=subprocess.TimeoutExpired('child', 1))
BROKEN = child(failure=OSError(5, 'io error after spawn'))
HELPER_ROWS = (('helper_proved_no_write', child(2), 'unsent'),
               ('noisy_exit_2_is_not_proof', child(2, stderr=b'noise'), 'unknown'),
               ('helper_may_have_written', child(1), 'unknown'))
SHARED_ROWS = (('spawn_oserror', SPAWN_FAILED, 'unsent'),
               ('child_timed_out', TIMED_OUT, 'unknown'),
               ('oserror_after_spawn', BROKEN, 'unknown'),
               ('clean_exit', child(0), 'delivered'))
CODEX_ROWS = (('queue_child_failed', child(1), 'unknown'),)
TABLE = tuple((engine, *row) for engine in ('grok', 'claude') for row in SHARED_ROWS + HELPER_ROWS) \
    + tuple(('codex', *row) for row in SHARED_ROWS + CODEX_ROWS)


class FailureTableTests(Harness):
    def run_row(self, engine, spawn, final, *, body='peer text', priority='normal', patches=()):
        record = self.bind(engine)
        api = Api(message(body=body, priority=priority))
        relay = self.relay(api, record)
        stack = contextlib.ExitStack()
        self.addCleanup(stack.close)
        popen = stack.enter_context(patch('quorum_claude_pipe.subprocess.Popen', **spawn))
        stack.enter_context(patch('quorum_session.check_native_route'))
        stack.enter_context(patch('quorum_session.shutil.which', return_value='/synthetic/codex'))
        for item in patches: stack.enter_context(item)
        code = None
        try: relay.run_once()
        except ValueError as error: code = str(error)
        spawned = popen.call_count
        if final == 'unsent':
            self.assertEqual(code, 'session_delivery_unsent')
            self.assertIsNone(self.row())
        elif final == 'unknown':
            self.assertEqual(code, 'session_delivery_unknown')
            intent = self.row()
            self.assertEqual(intent['state'], 'intent')
            with self.assertRaisesRegex(ValueError, '^session_delivery_unknown$'): relay.run_once()
            self.assertEqual(self.row(), intent)
            self.assertEqual(popen.call_count, spawned)
        else:
            self.assertIsNone(code)
            self.assertIn(self.row()['state'], ('submitted', 'acked'))
        if final != 'delivered': self.assertEqual(api.acks(), 0)
        return popen

    def test_every_child_outcome_on_every_client(self):
        for engine, point, spawn, final in TABLE:
            with self.subTest(engine=engine, point=point):
                self.run_row(engine, spawn, final)
                with self.ledger.db: self.ledger.db.execute("delete from settings where key like 'delivery:%'")

    def test_guards_before_any_child_are_unsent(self):
        from quorum_session import check_native_route  # noqa: F401 - the patched name exists.
        rows = [('grok', 'caller_changed', [patch.dict(os.environ, {'GROK_SESSION_ID':OTHER})]),
                ('claude', 'route_lost', [patch('quorum_session.check_native_route',
                                                side_effect=ValueError('claude_pipe_unavailable'))]),
                ('codex', 'cli_missing', [patch('quorum_session.shutil.which', return_value=None)])]
        rows += [(engine, 'prompt_too_large', [patch('quorum_session.opening', return_value='x'*48001)])
                 for engine in ENV]
        for engine, point, patches in rows:
            with self.subTest(engine=engine, point=point):
                popen = self.run_row(engine, {'side_effect':AssertionError('must not spawn')}, 'unsent',
                                     patches=patches)
                self.assertEqual(popen.call_count, 0)

    def test_the_windows_limit_stays_a_guard_before_spawning(self):
        host = SimpleNamespace(name='nt')
        popen = self.run_row('codex', {'side_effect':AssertionError('must not spawn')}, 'unsent',
                             patches=[patch('quorum_session.os', host),
                                      patch('quorum_session.fits_windows_command', return_value=False)])
        self.assertEqual(popen.call_count, 0)

    def test_urgent_codex_routes(self):
        from quorum_claude_pipe import DeliveryUnsent
        from quorum_codex_steer import SteerUnavailable
        unavailable = SteerUnavailable('no_active_turn')
        for point, effect, final, spawns in (
                ('steered', {'return_value':'submitted'}, 'delivered', 0),
                ('documented_refusal_queues_once', {'return_value':'rejected'}, 'delivered', 1),
                ('nothing_written_queues_once', {'side_effect':unavailable}, 'delivered', 1),
                ('prompt_unbuildable', {'side_effect':DeliveryUnsent()}, 'unsent', 0),
                ('frame_lost_after_write', {'side_effect':ValueError('session_delivery_unknown')}, 'unknown', 0)):
            with self.subTest(point=point):
                popen = self.run_row('codex', child(0), final, priority='urgent',
                                     patches=[patch('quorum_codex_steer.submit', **effect)])
                self.assertEqual(popen.call_count, spawns)
                with self.ledger.db: self.ledger.db.execute("delete from settings where key like 'delivery:%'")

    def test_urgent_caller_mismatch_is_unsent_and_never_steers(self):
        self.run_row('codex', {'side_effect':AssertionError('must not spawn')}, 'unsent', priority='urgent',
                     patches=[patch.dict(os.environ, {'CODEX_THREAD_ID':OTHER}),
                              patch('quorum_codex_steer.submit', side_effect=AssertionError('steered'))])

    def test_a_crash_around_the_write_leaves_an_attempt_nothing_replays(self):
        for engine in ENV:
            with self.subTest(engine=engine):
                record, api = self.bind(engine), Api(message())
                def die(_): raise Crash()
                with self.assertRaises(Crash): self.relay(api, record, die).run_once()
                restarted = self.relay(api, record, lambda _:self.fail('replayed an uncertain write'))
                for step in (restarted.reconcile, restarted.run_once):
                    with self.assertRaisesRegex(ValueError, '^session_delivery_unknown$'): step()
                self.assertEqual(self.row()['state'], 'intent')
                self.assertEqual(api.acks(), 0)
                with self.ledger.db: self.ledger.db.execute("delete from settings where key like 'delivery:%'")


class HeadOnlyBlockingTests(Harness):
    def test_stuck_attempt_lets_a_newer_urgent_through_and_blocks_again_as_head(self):
        for engine in ENV:
            with self.subTest(engine=engine):
                record, api = self.bind(engine), Api(message(URGENT, priority='urgent'), message())
                self.intent()
                delivered = []
                relay = self.relay(api, record, lambda item:delivered.append(item['id']))
                relay.reconcile()
                self.assertEqual(relay.run_once(), 'idle')
                self.assertEqual(delivered, [URGENT])
                api.call('session_ack', {'message_id':URGENT})  # The chat confirmed it.
                for step in (relay.reconcile, relay.run_once):
                    with self.assertRaisesRegex(ValueError, '^session_delivery_unknown$'): step()
                self.assertEqual(delivered, [URGENT])
                self.assertEqual(self.row()['attempt_id'], ATTEMPT)
                with self.ledger.db: self.ledger.db.execute("delete from settings where key like 'delivery:%'")

    def test_a_stuck_attempt_behind_an_urgent_head_does_not_block_rebinding(self):
        from quorum_listener import Control, StartGuard
        from quorum_session import rebind
        self.bind('grok')
        self.intent()
        api = Api(message(URGENT, priority='urgent'), message())
        inbox = lambda: api.call('session_inbox', {})
        with StartGuard(self.ledger) as guard:
            rebind(self.ledger, Control(self.ledger), SID, {'leader_socket':'/new'}, guard, inbox=inbox, engine='grok')
            self.assertEqual(self.ledger.setting('session-agent')['grok_transport'], {'leader_socket':'/new'})
            api.messages = [message()]
            with self.assertRaisesRegex(ValueError, '^session_delivery_unknown$'):
                rebind(self.ledger, Control(self.ledger), SID, {'leader_socket':'/next'}, guard, inbox=inbox,
                       engine='grok')

    def test_a_stop_during_the_head_read_wins_before_the_listener_is_disturbed(self):
        from quorum_listener import Control, STOP_INTENT, StartGuard
        from quorum_session import rebind
        record = self.bind('grok')
        control = Control(self.ledger)
        api = Api()
        def stopped_meanwhile():
            self.ledger.setting(STOP_INTENT, {'generation':1})
            return api.call('session_inbox', {})
        with StartGuard(self.ledger) as guard, patch.object(control, 'request_stop') as request_stop:
            with self.assertRaisesRegex(ValueError, '^listener_start_stopped$'):
                rebind(self.ledger, control, SID, {'leader_socket':'/new'}, guard, inbox=stopped_meanwhile,
                       engine='grok', authorize=guard.check)
        request_stop.assert_not_called()
        self.assertEqual(self.ledger.setting('session-agent'), record)


class ConfirmedAbsentTests(Harness):
    def attest(self, api):
        return self.dispatch(api, 'reconcile', confirmed_absent=True, confirmed_queued=False, attempt_id=ATTEMPT)

    @isolated_profiles
    def test_grok_and_codex_clear_the_exact_attempt_without_an_ack_behind_an_urgent_head(self):
        for engine in ('grok', 'codex'):
            with self.subTest(engine=engine):
                self.bind(engine)
                self.intent()
                api = Api(message(URGENT, priority='urgent'), message())
                api.unreceipted = {MID, URGENT}
                self.assertTrue(self.attest(api)['confirmed_absent'])
                self.assertIsNone(self.row())
                self.assertEqual(self.ledger.setting('delivery-absent:'+MID+':'+ATTEMPT)['binding']['engine'], engine)
                self.assertEqual(api.acks(), 0)
                self.assertTrue(self.attest(api)['confirmed_absent'])  # A repeat touches nothing.
                with self.ledger.db: self.ledger.db.execute("delete from settings where key like 'delivery-absent:%'")

    @isolated_profiles
    def test_a_receipted_or_foreign_message_is_refused(self):
        self.bind('grok')
        intent = self.intent()
        with self.assertRaisesRegex(ValueError, '^session_reconcile_denied$'): self.attest(Api(message()))
        self.assertEqual(self.row(), intent)

    @isolated_profiles
    def test_an_older_server_falls_back_to_the_head_and_fails_closed(self):
        from quorum_api import QuorumError
        class OldServer(Api):
            def call(self, operation, payload, request_id=None):
                if operation == 'session_message_unreceipted':
                    raise QuorumError('service_error', missing=True)
                return super().call(operation, payload, request_id)
        self.bind('codex')
        intent = self.intent()
        with self.assertRaisesRegex(ValueError, '^session_reconcile_denied$'):
            self.attest(OldServer(message(URGENT, priority='urgent'), message()))
        self.assertEqual(self.row(), intent)
        self.assertTrue(self.attest(OldServer(message()))['confirmed_absent'])

    @isolated_profiles
    def test_a_service_failure_is_not_absence(self):
        from quorum_api import QuorumError
        class Down(Api):
            def call(self, operation, payload, request_id=None):
                if operation == 'session_message_unreceipted': raise QuorumError('service_error')
                return super().call(operation, payload, request_id)
        self.bind('grok')
        intent = self.intent()
        with self.assertRaisesRegex(ValueError, '^service_error$'): self.attest(Down(message()))
        self.assertEqual(self.row(), intent)

    @isolated_profiles
    def test_a_tool_inbox_binding_cannot_attest(self):
        from quorum_session import reconcile_absent
        record = {**self.bind('codex'), 'transport':'tool-inbox'}
        with self.assertRaisesRegex(ValueError, '^session_reconcile_denied$'):
            reconcile_absent(Api(), self.ledger, record, MID, ATTEMPT)


class BareReconcileTests(Harness):
    @isolated_profiles
    def test_a_call_without_exactly_one_attestation_changes_nothing(self):
        from quorum_session import ReconcileEvidenceRequired
        self.bind('codex')
        for row in ({'state':'submitted'}, {'state':'intent', 'attempt_id':ATTEMPT}):
            self.ledger.setting('delivery:'+MID, row)
            for flags in ({}, {'confirmed_queued':False, 'confirmed_absent':False},
                          {'confirmed_queued':True, 'confirmed_absent':True, 'attempt_id':ATTEMPT}):
                with self.subTest(row=row, flags=flags):
                    api = Api(message())
                    with self.assertRaises(ReconcileEvidenceRequired) as caught:
                        self.dispatch(api, 'reconcile', **flags)
                    self.assertEqual(str(caught.exception), 'session_reconcile_denied')
                    self.assertEqual(self.row(), row)
                    self.assertEqual(api.acks(), 0)

    @isolated_profiles
    def test_the_cli_returns_the_code_and_a_next_step(self):
        import quorum
        self.bind('codex')
        self.ledger.setting('delivery:'+MID, {'state':'submitted'})
        api = Api(message())
        with patch('quorum_live.api_for', return_value=(api, None, None)), \
             contextlib.redirect_stdout(io.StringIO()) as output:
            code = quorum.main(['--state', str(self.root), 'session', 'reconcile', MID])
        result = json.loads(output.getvalue())
        self.assertEqual((code, result['code']), (3, 'session_reconcile_denied'))
        self.assertIn('--confirmed-queued', result['data']['next_step'])
        self.assertEqual((api.acks(), self.row()), (0, {'state':'submitted'}))

    @isolated_profiles
    def test_confirmed_queued_stays_the_owner_path_for_an_unresolved_attempt(self):
        self.bind('grok')
        self.intent()
        api = Api(message())
        self.assertEqual(self.dispatch(api, 'reconcile', confirmed_queued=True, confirmed_absent=False),
                         {'confirmed_queued':True})
        self.assertEqual((self.row(), api.acks()), ({'state':'acked', 'attempt_id':ATTEMPT}, 1))


class ReceiptTests(Harness):
    def settle_later(self, state):
        from quorum_state import LocalLedger
        def settle():
            other = LocalLedger(self.ledger.root)
            try:
                if state is None:
                    with other.db: other.db.execute('delete from settings where key=?', ('delivery:'+MID,))
                else: other.setting('delivery:'+MID, state)
            finally: other.db.close()
        timer = threading.Timer(.2, settle)
        timer.start()
        self.addCleanup(timer.join)

    @isolated_profiles
    def test_received_acknowledges_only_a_submitted_or_queued_row(self):
        self.bind('grok')
        for row, allowed in (({'state':'submitted'}, True), ({'state':'queued'}, True), ({'state':'acked'}, True),
                             ({'state':'intent', 'attempt_id':ATTEMPT}, False), (None, False)):
            with self.subTest(row=row):
                with self.ledger.db: self.ledger.db.execute('delete from settings where key=?', ('delivery:'+MID,))
                if row: self.ledger.setting('delivery:'+MID, row)
                api = Api(message())
                if allowed:
                    self.assertEqual(self.dispatch(api, 'received'), {'confirmed_queued':True})
                    self.assertEqual(self.row(), {'state':'acked'})
                else:
                    with self.assertRaisesRegex(ValueError, '^receipt_not_submitted$'):
                        self.dispatch(api, 'received')
                    self.assertEqual((self.row(), api.acks()), (row, 0))

    @isolated_profiles
    def test_reply_keeps_its_answer_but_never_receipts_an_unresolved_attempt(self):
        self.bind('codex')
        intent = self.intent()
        api = Api(message())
        self.dispatch(api, 'reply', priority='normal')
        self.assertIn('session_reply', api.calls)
        self.assertEqual((self.row(), api.acks()), (intent, 0))

    @isolated_profiles
    def test_reply_receipts_a_submitted_row(self):
        self.bind('claude')
        self.ledger.setting('delivery:'+MID, {'state':'submitted'})
        api = Api(message())
        self.dispatch(api, 'reply', priority='normal')
        self.assertEqual((self.row(), api.acks()), ({'state':'acked'}, 1))

    @isolated_profiles
    def test_a_fast_chat_waits_for_its_in_flight_enqueue_to_settle(self):
        from quorum_os import FileLock
        self.bind('grok')
        for action, extra in (('received', {}), ('reply', {'priority':'normal'})):
            with self.subTest(action=action):
                self.intent()
                api = Api(message())
                self.settle_later({'state':'submitted'})
                with FileLock(self.ledger.root/'listener.lock'): self.dispatch(api, action, **extra)
                self.assertEqual((self.row(), api.acks()), ({'state':'acked'}, 1))

    @isolated_profiles
    def test_an_enqueue_that_proves_unsent_leaves_nothing_to_receipt(self):
        from quorum_os import FileLock
        self.bind('grok')
        self.intent()
        api = Api(message())
        self.settle_later(None)
        with FileLock(self.ledger.root/'listener.lock'):
            with self.assertRaisesRegex(ValueError, '^receipt_not_submitted$'): self.dispatch(api, 'received')
        self.assertEqual(api.acks(), 0)

    @isolated_profiles
    def test_a_stuck_attempt_with_no_listener_refuses_at_once_and_a_live_one_is_bounded(self):
        from quorum_os import FileLock
        from quorum_session import settled_delivery
        self.bind('grok')
        intent = self.intent()
        with patch('quorum_session.time.sleep', side_effect=AssertionError('waited for a dead listener')):
            self.assertEqual(settled_delivery(self.ledger, MID), intent)
        with FileLock(self.ledger.root/'listener.lock'):
            self.assertEqual(settled_delivery(self.ledger, MID, wait=.2), intent)

    @isolated_profiles
    def test_receipt_cannot_overwrite_a_row_a_new_attempt_replaced(self):
        from quorum_session import acknowledge
        self.bind('codex')
        intent = self.intent()
        api = Api(message())
        original = api.call
        def observed(operation, payload, request_id=None):
            if operation == 'session_ack': self.assertEqual(self.row(), intent)
            return original(operation, payload, request_id)
        api.call = observed
        acknowledge(api, self.ledger, {'agent_id':'agent', 'session_ref':SID}, MID)


class GrokRouteTests(Harness):
    TRANSPORT = {'leader_socket':'/fixture/leader.sock'}

    def watched(self, reads, api):
        from quorum_grok_leader import TransportWatch
        from quorum_session import Relay, listener_route
        record = self.bind('grok')
        self.now, answers = [0], iter(reads)
        watch = TransportWatch(record['grok_transport'], alive=lambda _:next(answers), clock=lambda:self.now[0])
        self.delivered = []
        return watch, Relay(api, self.ledger, record, lambda item:self.delivered.append(item['id']) or 'submitted',
                            route_check=listener_route(record, watch.check))

    def test_a_replaced_tui_ends_the_run_before_inbox_or_intent_once_the_rule_is_met(self):
        api = Api()
        _, relay = self.watched([False, False, False], api)
        for _ in range(2):
            self.assertEqual(relay.run_once(), 'idle')
            self.now[0] += 15
        api.messages, api.calls = [message()], []
        with self.assertRaisesRegex(ValueError, '^grok_tui_gone$'): relay.run_once()
        self.assertNotIn('session_inbox', api.calls)
        self.assertIsNone(self.row())

    def test_one_failed_read_keeps_delivering_like_a_heartbeat(self):
        _, relay = self.watched([False, True], Api(message()))
        self.assertEqual(relay.run_once(), 'idle')
        self.assertEqual(self.delivered, [MID])

    def test_a_burst_of_failed_reads_is_not_a_restart_window(self):
        _, relay = self.watched([False] * 5, Api())
        for _ in range(5): self.assertEqual(relay.run_once(), 'idle')

    def test_heartbeats_and_inbox_reads_count_toward_one_rule(self):
        watch, relay = self.watched([False, False, False], Api())
        watch.check()
        self.now[0] += 15
        relay.reconcile()
        self.now[0] += 15
        with self.assertRaisesRegex(ValueError, '^grok_tui_gone$'): relay.run_once()

    def test_a_delivery_inside_the_window_is_unsent_by_the_helper(self):
        from quorum_grok_leader import TransportWatch
        from quorum_session import Relay, enqueue, listener_route
        record, api = self.bind('grok'), Api(message())
        watch = TransportWatch(record['grok_transport'], alive=lambda _:False, clock=lambda:0)
        relay = Relay(api, self.ledger, record, lambda item:enqueue(record, item, self.root),
                      route_check=listener_route(record, watch.check))
        with patch('quorum_claude_pipe.subprocess.Popen', return_value=Child(2)):
            with self.assertRaisesRegex(ValueError, '^session_delivery_unsent$'): relay.run_once()
        self.assertIsNone(self.row())
        self.assertEqual(api.acks(), 0)

    def test_the_run_shares_one_watch_between_heartbeats_and_inbox_reads(self):
        record = {**self.bind('grok'), 'agent_id':OTHER}  # Realtime topics need a real agent UUID.
        self.ledger.setting('session-agent', record)
        captured = {}
        def listener(_, make, run_id, **options):
            captured.update(make=make, waiter=options['waiter'])
        args = dict(run_id=SID, recovering=False)
        with patch('quorum_grok_leader.binding', return_value=record['grok_transport']), \
             patch('quorum_grok_leader.TransportWatch') as watch, \
             patch('quorum_resume.startup_authority', return_value=lambda:None), \
             patch('quorum_session.ReleaseGuard'), \
             patch('quorum_session.run_listener', side_effect=listener):
            self.dispatch(Api(), 'run', **args)
            captured['make']().route_check()
            captured['make']().route_check()
        watch.assert_called_once_with(record['grok_transport'])
        self.assertIs(captured['waiter'].keepalive.liveness, watch.return_value.check)
        self.assertEqual(watch.return_value.check.call_count, 2)

    def test_other_clients_have_no_transport_read(self):
        from quorum_session import listener_route
        with patch('quorum_grok_leader.transport_alive', side_effect=AssertionError('probed')):
            listener_route(self.bind('codex'))()

    @isolated_profiles
    def test_an_attempt_left_behind_by_a_resume_has_a_recovery_path(self):
        record, api = self.bind('grok'), Api(message())
        self.intent()
        api.unreceipted = {MID}
        blocked = self.relay(api, record, lambda _:self.fail('replayed'))
        with self.assertRaisesRegex(ValueError, '^session_delivery_unknown$'): blocked.reconcile()
        self.dispatch(api, 'reconcile', confirmed_absent=True, confirmed_queued=False, attempt_id=ATTEMPT)
        retried = self.relay(api, record, lambda _:'submitted')
        retried.reconcile()
        self.assertEqual(retried.run_once(), 'idle')
        self.assertEqual((self.row(), api.acks()), ({'state':'submitted'}, 0))


class Socket:
    """The Leader's socket calls; a failed sendall models a frame that never fully left."""
    def __init__(self, failure=None): self.failure, self.sent, self.closed = failure, [], False
    def settimeout(self, _): pass
    def sendall(self, data):
        if self.failure and b'session/prompt' in data: raise self.failure
        self.sent.append(data)
    def recv(self, _): return b''  # The leader closed before confirming.
    def close(self): self.closed = True


class GrokHelperTests(unittest.TestCase):
    TRANSPORT = {'leader_socket':'/fixture/leader.sock', 'cwd':'/fixture'}

    def main(self, *, connect=None, failure=None):
        import quorum_grok_leader
        from quorum_grok_leader import Leader
        leader = Leader.__new__(Leader)
        leader.socket, leader.ref, leader.verify = Socket(failure), 0, lambda *_:None
        raw = json.dumps({'record':{'session_ref':SID, 'cwd':'/fixture', 'grok_transport':self.TRANSPORT},
                          'message_id':MID, 'content':'hello'}).encode()
        with patch('quorum_grok_leader.checked_binding', return_value=(self.TRANSPORT, None)), \
             patch('quorum_grok_leader.local_binding', return_value=self.TRANSPORT), \
             patch('quorum_grok_leader.connect', side_effect=connect, return_value=leader), \
             patch('quorum_grok_leader.sys.stdin', SimpleNamespace(buffer=io.BytesIO(raw))):
            return quorum_grok_leader.main(), leader

    def test_failure_before_the_prompt_write_is_definite_unsent(self):
        self.assertEqual(self.main(connect=OSError('refused'))[0], 2)

    def test_an_incomplete_prompt_frame_is_unsent_and_gets_no_trailing_bytes(self):
        for failure in (OSError('pipe_unavailable'), TimeoutError()):
            with self.subTest(failure=failure):
                code, leader = self.main(failure=failure)
                self.assertEqual(code, 2)
                self.assertFalse(any(b'disconnect' in frame for frame in leader.socket.sent))
                self.assertTrue(leader.socket.closed)

    def test_a_whole_frame_without_confirmation_may_have_been_admitted(self):
        code, leader = self.main()
        self.assertEqual(code, 1)
        self.assertTrue(leader.admissible)

    def test_an_oversized_frame_never_reaches_the_socket(self):
        from quorum_grok_leader import Leader
        leader = Leader.__new__(Leader)
        leader.socket, leader.ref = Socket(), 0
        with self.assertRaisesRegex(ValueError, 'invalid_message'):
            leader.prompt(SID, MID, '\x01'*48000)
        self.assertEqual((leader.socket.sent, leader.admissible), ([], False))


class Kernel:
    def __init__(self, writes, *, open_pipe=True): self.writes, self.open_pipe = list(writes), open_pipe
    def WriteFile(self, handle, data, size, written, overlapped):
        step = self.writes.pop(0) if self.writes else False
        if step is False: return 0
        written.value = min(step, size)
        return 1
    def PeekNamedPipe(self, *_): return self.open_pipe


class WindowsPipeSendTests(unittest.TestCase):
    def pipe(self, kernel, error=NO_DATA, timeout=1):
        from quorum_grok_win import Pipe
        pipe = Pipe(1, 2)
        pipe.handle, pipe.timeout = object(), timeout
        native = SimpleNamespace(byref=lambda value:value, get_last_error=lambda:error)
        pipe.api = (native, ctypes.wintypes, kernel)
        return pipe

    def test_a_full_pipe_that_stays_open_is_waited_out(self):
        kernel = Kernel([False, False, 2, 0, 10])
        self.pipe(kernel).sendall(b'0123456789')
        self.assertEqual(kernel.writes, [])

    def test_no_progress_until_the_deadline_times_out(self):
        with self.assertRaises(TimeoutError): self.pipe(Kernel([]), timeout=.05).sendall(b'data')

    def test_a_closed_pipe_fails_at_once(self):
        with self.assertRaisesRegex(OSError, 'pipe_unavailable'):
            self.pipe(Kernel([], open_pipe=False)).sendall(b'data')
        with self.assertRaisesRegex(OSError, 'pipe_unavailable'):
            self.pipe(Kernel([]), error=BROKEN_PIPE).sendall(b'data')


class PointerDeliveryTests(Harness):
    """#150: a queued Codex envelope points at the body, so no body can reach argv or outgrow it."""
    def delivered(self, *messages, patches=()):
        """Queue each message through a fake CLI, and have the chat confirm it."""
        from quorum_session import acknowledge
        record = self.bind('codex')
        api = Api(*messages)
        with patch('quorum_claude_pipe.subprocess.Popen', return_value=Child()) as popen, \
             patch('quorum_session.shutil.which', return_value='/synthetic/codex'), \
             contextlib.ExitStack() as stack:
            for item in patches: stack.enter_context(item)
            for item in messages:
                self.assertEqual(self.relay(api, record).run_once(), 'idle')
                acknowledge(api, self.ledger, {'agent_id':'agent', 'session_ref':SID}, item['id'])
        return api, [call.args[0] for call in popen.call_args_list]

    def forget(self):
        with self.ledger.db: self.ledger.db.execute("delete from settings where key like 'delivery:%'")

    def field(self, prompt, label):
        return json.loads(prompt.split(label, 1)[1].split('\n', 1)[0])

    def test_argv_never_contains_the_body_on_any_platform(self):
        from quorum_codex_steer import SteerUnavailable
        for name in ('posix', 'nt'):
            for priority in ('normal', 'urgent'):
                with self.subTest(platform=name, priority=priority):
                    secret = 'peer-body-' + name + '-' + priority
                    _, commands = self.delivered(message(body=secret, priority=priority), patches=[
                        patch('quorum_session.os', SimpleNamespace(name=name, environ=os.environ)),
                        patch('quorum_codex_steer.submit', side_effect=SteerUnavailable('no_active_turn'))])
                    self.assertEqual(len(commands), 1)
                    self.assertEqual(commands[0][1:4], ['queue', '--thread', SID])
                    self.assertNotIn(secret, ' '.join(commands[0]))
                    self.assertNotIn('"body"', commands[0][-1])
                    self.forget()

    def test_a_maximum_hostile_body_on_windows_is_delivered_and_frees_the_next_message(self):
        from quorum_session import fits_windows_command
        hostile = ('"\\' * 8192)[:16384]
        self.assertEqual(len(hostile.encode()), 16384)
        api, commands = self.delivered(message(body=hostile), message(URGENT, body='next'),
                                       patches=[patch('quorum_session.os', SimpleNamespace(name='nt'))])
        self.assertEqual(api.acks(), 2)
        self.assertEqual(api.messages, [])
        self.assertIn(MID, commands[0][-1])
        self.assertIn(URGENT, commands[1][-1])
        self.assertNotIn(hostile[:32], commands[0][-1])
        self.assertTrue(fits_windows_command(commands[0]))

    def test_the_largest_envelope_fits_windows_with_long_paths(self):
        from quorum_delegation import member_grant
        from quorum_session import enqueue, fits_windows_command
        long = 'C:\\' + '\\'.join(['d' * 30] * 8)
        record = {'engine':'codex', 'session_ref':SID, 'agent_id':'agent', 'quorum_id':'q'*80, 'quorum':'q'*80,
                  'project_ref':'p'*128, 'cwd':long, 'role':'test'}
        record['delegation'] = member_grant(record, standing_from='lead', trust=True)
        for notification in (False, True):
            sent = []
            def run(command, **_):
                sent.append(command)
                return subprocess.CompletedProcess(command, 0, queue_accepted(SID), b'')
            item = {**message(priority='urgent', body='x'), 'sender_id':'peer', 'sender_role':'r'*48,
                    'notification':notification}
            with patch('quorum_session.run_child', side_effect=run), \
                 patch('quorum_session.require_caller'), \
                 patch('quorum_session.os', SimpleNamespace(name='nt')), \
                 patch('quorum_session.shutil.which', return_value=long + '\\codex.exe'), \
                 patch('quorum_codex_steer.submit', return_value='rejected'):
                enqueue(record, item, Path(long))
            self.assertEqual(len(sent), 1)
            self.assertTrue(fits_windows_command(sent[0]))

    def test_the_pointer_makes_the_body_read_its_receipt_and_keeps_the_data_and_reply_rules(self):
        _, commands = self.delivered(message(body='peer text'))
        prompt = commands[0][-1]
        read = self.field(prompt, 'read it with this command (argument array): ')
        reply = self.field(prompt, 'answer text goes to stdin): ')
        self.assertEqual(read[-6:], ['message', MID, '--conversation-id', CONVERSATION, '--sequence', '3'])
        self.assertEqual(reply[-3:], ['reply', MID, '--text-stdin'])
        self.assertEqual(read[:-6], reply[:-3])
        self.assertNotIn('"received"', prompt)
        self.assertIn('A successful read is also the receipt', prompt)
        self.assertIn('peer data', prompt)
        self.assertIn('could not be read', prompt)
        self.assertEqual(json.loads(prompt.split('Peer message JSON without its body:\n', 1)[1]),
                         {key:value for key, value in message().items() if key != 'body'})

    def test_the_read_command_returns_the_whole_body_before_any_receipt(self):
        import quorum
        body = ('"\\' * 8192)[:16384]
        class Thread(Api):
            def call(inner, operation, payload, request_id=None):
                if operation != 'session_message': return super().call(operation, payload, request_id)
                inner.read = payload
                return {'id':MID, 'recipient_id':'agent', 'sequence':3, 'body':body}
        record = self.bind('codex')
        api = Thread(message(body=body))
        sent = []
        def queue(item):
            from quorum_session import enqueue
            with patch('quorum_session.run_child', side_effect=lambda command, **_: (
                    sent.append(command), subprocess.CompletedProcess(command, 0, queue_accepted(SID), b''))[1]), \
                 patch('quorum_session.shutil.which', return_value='/synthetic/codex'):
                return enqueue(record, item, self.root)
        self.relay(api, record, queue).run_once()
        read = self.field(sent[0][-1], 'read it with this command (argument array): ')
        output = io.StringIO()
        with patch('quorum_live.api_for', return_value=(api, None, None)), \
             patch('quorum_session.Path.cwd', return_value=self.root), contextlib.redirect_stdout(output):
            self.assertEqual(quorum.main(read[2:]), 0)
        self.assertEqual(api.read, {'agent_id':'agent', 'session_ref':SID, 'message_id':MID})
        self.assertEqual(json.loads(output.getvalue())['data']['body'], body)

    def test_a_failed_read_names_its_next_step_and_leaves_the_message_unreceipted(self):
        import quorum
        class Missing(Api):
            def call(inner, operation, payload, request_id=None):
                return None if operation == 'session_message' else super().call(operation, payload, request_id)
        api = Missing(message())
        record = self.bind('codex')
        with patch('quorum_claude_pipe.subprocess.Popen', return_value=Child()), \
             patch('quorum_session.shutil.which', return_value='/synthetic/codex'):
            self.assertEqual(self.relay(api, record).run_once(), 'idle')
        output = io.StringIO()
        with patch('quorum_live.api_for', return_value=(api, None, None)), \
             patch('quorum_session.Path.cwd', return_value=self.root), contextlib.redirect_stdout(output):
            code = quorum.main(['--state', str(self.root), 'session', '--session-id', SID, 'message', MID,
                                '--conversation-id', CONVERSATION, '--sequence', '3'])
        result = json.loads(output.getvalue())
        self.assertEqual((code, result['code']), (5, 'session_message_unavailable'))
        self.assertEqual(result['data']['reason'], 'not_found')
        self.assertIn('resend', result['data']['next_step'])
        self.assertEqual((self.row()['state'], api.acks()), ('submitted', 0))

    def test_the_receipt_command_stays_idempotent_after_the_chat_confirmed(self):
        api, _ = self.delivered(message())
        self.assertEqual(self.row()['state'], 'acked')
        self.assertEqual(self.dispatch(api, 'received'), {'confirmed_queued':True})
        self.assertEqual(self.row()['state'], 'acked')

    def test_a_default_state_path_leaves_argv_and_a_custom_one_stays(self):
        _, custom = self.delivered(message())
        self.assertEqual(custom[0][-1].count('"--state"'), 3)
        self.forget()
        _, default = self.delivered(message(), patches=[
            patch('quorum_session.default_state_root', return_value=self.root)])
        self.assertNotIn('--state', default[0][-1])

    def test_a_row_without_a_valid_position_is_unsent(self):
        for change in ({'sequence':0}, {'sequence':'x'}, {'conversation_id':'not-a-uuid'}):
            with self.subTest(change=change):
                record = self.bind('codex')
                relay = self.relay(Api({**message(), **change}), record)
                with patch('quorum_claude_pipe.subprocess.Popen', side_effect=AssertionError('must not spawn')), \
                     patch('quorum_session.shutil.which', return_value='/synthetic/codex'), \
                     self.assertRaisesRegex(ValueError, '^session_delivery_unsent$'):
                    relay.run_once()
                self.assertIsNone(self.row())


class Reader(Api):
    """A service that also serves the single-message read."""
    missing = False
    def call(self, operation, payload, request_id=None):
        if operation != 'session_message': return super().call(operation, payload, request_id)
        self.calls.append(operation)
        return None if self.missing else {'id':MID, 'recipient_id':'agent', 'sequence':3, 'body':'peer text'}


class UnreadPointerTests(Harness):
    """#157: a `codex queue` pointer holds its receipt until the chat has read the body."""
    def setUp(self):
        super().setUp()
        self.record = self.bind('codex')

    def accepted(self, api):
        """One real enqueue through a fake CLI that accepts it; returns the spawned commands."""
        spawned = patch('quorum_claude_pipe.subprocess.Popen', return_value=Child())
        with spawned as popen, patch('quorum_session.shutil.which', return_value='/synthetic/codex'):
            result = self.relay(api, self.record).run_once()
        return result, [call.args[0] for call in popen.call_args_list]

    def read(self, api):
        return self.dispatch(api, 'message', conversation_id=CONVERSATION, sequence=3)

    def test_cli_acceptance_writes_no_receipt_and_is_never_queued_twice(self):
        api = Reader(message(), message(OTHER))
        self.assertEqual(self.accepted(api)[0], 'idle')
        row = self.row()
        self.assertEqual(row['state'], 'submitted')
        uuid.UUID(row['attempt_id'])
        self.assertEqual(api.acks(), 0)
        self.assertEqual(self.accepted(api), ('idle', []))
        self.assertEqual((self.row(), api.acks()), (row, 0))

    def test_the_body_read_is_the_receipt_and_frees_the_next_message(self):
        api = Reader(message(), message(OTHER))
        self.accepted(api)
        relay = self.relay(api, self.record)
        with patch('quorum_claude_pipe.subprocess.Popen', return_value=Child()), \
             patch('quorum_session.shutil.which', return_value='/synthetic/codex'):
            self.assertEqual(relay.run_once(), 'idle')
            self.assertEqual(self.read(api)['body'], 'peer text')
            self.assertEqual((self.row()['state'], api.acks()), ('acked', 1))
            self.assertEqual(relay.run_once(), 'delivered')
            self.assertEqual(relay.run_once(), 'idle')
        self.assertEqual(self.row(OTHER)['state'], 'submitted')

    def test_reading_again_after_the_receipt_writes_nothing(self):
        api = Reader(message())
        self.accepted(api)
        self.read(api)
        self.read(api)
        self.assertEqual(api.acks(), 1)

    def test_a_failed_read_keeps_the_message_unreceipted_and_a_retry_confirms_it(self):
        api = Reader(message())
        self.accepted(api)
        row = self.row()
        api.missing = True
        with self.assertRaisesRegex(ValueError, '^session_message_unavailable$'): self.read(api)
        self.assertEqual((self.row(), api.acks()), (row, 0))
        api.missing = False
        self.read(api)
        self.assertEqual((self.row()['state'], api.acks()), ('acked', 1))

    def test_a_failed_ack_upload_still_returns_the_body_and_the_listener_finishes_it(self):
        from quorum_api import QuorumError
        api = Reader(message())
        self.accepted(api)
        original = api.call
        def outage(operation, payload, request_id=None):
            if operation == 'session_ack': raise QuorumError('service_unavailable')
            return original(operation, payload, request_id)
        api.call = outage
        self.assertEqual(self.read(api)['body'], 'peer text')
        self.assertEqual(self.row()['state'], 'queued')
        api.call = original
        self.assertEqual(self.accepted(api), ('delivered', []))
        self.assertEqual((self.row()['state'], api.acks()), ('acked', 1))

    def test_a_crash_inside_the_read_keeps_the_proof_and_never_replays(self):
        api = Reader(message())
        self.accepted(api)
        original = api.call
        def die(operation, payload, request_id=None):
            if operation == 'session_ack': raise Crash()
            return original(operation, payload, request_id)
        api.call = die
        with self.assertRaises(Crash): self.read(api)
        api.call = original
        self.assertEqual(self.row()['state'], 'queued')
        self.assertEqual(self.accepted(api), ('delivered', []))

    def test_a_listener_crash_after_acceptance_neither_replays_nor_acknowledges(self):
        api = Reader(message())
        self.accepted(api)
        row = self.row()
        restarted = self.relay(api, self.record, lambda _:self.fail('replayed an accepted pointer'))
        restarted.reconcile()
        self.assertEqual(restarted.run_once(), 'idle')
        self.assertEqual((self.row(), api.acks()), (row, 0))

    def test_a_read_during_an_unresolved_attempt_returns_the_body_and_claims_nothing(self):
        api = Reader(message())
        intent = self.intent()
        self.assertEqual(self.read(api)['body'], 'peer text')
        self.assertEqual((self.row(), api.acks()), (intent, 0))

    def test_a_read_confirms_only_a_pointer(self):
        # A pipe or steered envelope carries its body and its own receipt command.
        api = Reader(message())
        self.ledger.setting('delivery:'+MID, {'state':'submitted'})
        self.read(api)
        self.assertEqual((self.row(), api.acks()), ({'state':'submitted'}, 0))

    def test_received_and_reply_still_confirm_a_pointer(self):
        for action, extra in (('received', {}), ('reply', {'priority':'normal'})):
            with self.subTest(action=action):
                with self.ledger.db: self.ledger.db.execute("delete from settings where key like 'delivery:%'")
                api = Reader(message())
                self.accepted(api)
                self.dispatch(api, action, **extra)
                self.assertEqual((self.row()['state'], api.acks()), ('acked', 1))

    def test_an_urgent_message_passes_an_unread_head(self):
        api = Reader(message(URGENT, priority='urgent'), message())
        self.ledger.setting('delivery:'+MID, {'state':'submitted', 'attempt_id':ATTEMPT})
        steered = patch('quorum_codex_steer.submit', return_value='submitted')
        with steered:
            relay = self.relay(api, self.record)
            with patch('quorum_session.shutil.which', return_value='/synthetic/codex'):
                self.assertEqual(relay.run_once(), 'idle')
        self.assertEqual(self.row(URGENT), {'state':'submitted'})

    def test_status_names_each_unread_pointer_and_counts_it_as_a_pending_receipt(self):
        api = Reader(message())
        self.accepted(api)
        status = self.dispatch(api, 'status')
        self.assertEqual(status['pending_receipts'], 1)
        self.assertEqual(status['unread_queued'], [{'message_id':MID, 'attempt_id':self.row()['attempt_id']}])
        self.read(api)
        status = self.dispatch(api, 'status')
        self.assertEqual((status['pending_receipts'], status['unread_queued']), (0, []))

    def test_a_steered_or_piped_row_is_not_an_unread_pointer(self):
        self.ledger.setting('delivery:'+MID, {'state':'submitted'})
        status = self.dispatch(Reader(), 'status')
        self.assertEqual((status['pending_receipts'], status['unread_queued']), (1, []))

    def absent(self, api, attempt):
        api.unreceipted.add(MID)
        return self.dispatch(api, 'reconcile', confirmed_absent=True, confirmed_queued=False, attempt_id=attempt)

    def test_confirmed_absence_redelivers_exactly_once_under_a_new_attempt(self):
        api = Reader(message())
        self.accepted(api)
        attempt = self.row()['attempt_id']
        self.assertTrue(self.absent(api, attempt)['confirmed_absent'])
        self.assertIsNone(self.row())
        self.assertEqual(api.acks(), 0)
        result, commands = self.accepted(api)
        self.assertEqual((result, len(commands)), ('idle', 1))
        self.assertNotEqual(self.row()['attempt_id'], attempt)
        self.assertTrue(self.absent(api, attempt)['confirmed_absent'])  # A repeat touches the new attempt not at all.
        self.assertEqual(self.row()['state'], 'submitted')

    def test_confirmed_absence_is_refused_for_a_wrong_attempt_or_a_read_message(self):
        api = Reader(message())
        self.accepted(api)
        row = self.row()
        with self.assertRaisesRegex(ValueError, '^session_reconcile_denied$'): self.absent(api, ATTEMPT)
        self.assertEqual(self.row(), row)
        self.read(api)
        with self.assertRaisesRegex(ValueError, '^session_reconcile_denied$'): self.absent(api, row['attempt_id'])
        self.assertEqual(self.row()['state'], 'acked')


class Response(io.BytesIO):
    def __enter__(self): return self
    def __exit__(self, *_): return False


class Service:
    """The real transport against synthetic HTTP: 16 maximum bodies, optionally without the new RPC."""
    BODY = ('"\\' * 8192)[:16384]

    def __init__(self, *, single=True, rows=16):
        self.single, self.requests = single, []
        self.rows = [{'id':str(uuid.UUID(int=number + 1)), 'sequence':number + 1, 'body':self.BODY,
                      'sender_id':'peer', 'recipient_id':'agent', 'reply_to':None} for number in range(rows)]

    def open(self, request, timeout=None):
        name = request.full_url.rsplit('/', 1)[1]
        self.requests.append(name)
        if name == 'session_message' and not self.single:
            raise HTTPError(request.full_url, 404, 'missing', {}, io.BytesIO(b'{"code":"PGRST202"}'))
        return Response(json.dumps(self.rows[0] if name == 'session_message' else self.rows).encode())


class SingleMessageReadTests(unittest.TestCase):
    RECORD = {'agent_id':'agent', 'session_ref':SID, 'quorum_id':'quorum-id'}

    def read(self, service, message_id=None):
        from quorum_api import QuorumApi, ServiceConfig
        from quorum_session import read_message
        api = QuorumApi(ServiceConfig('https://example.com', 'sb_publishable_synthetic'), lambda: 'synthetic')
        with patch('quorum_api.build_opener', return_value=service):
            return read_message(api, self.RECORD, message_id or service.rows[0]['id'], CONVERSATION, 1)

    def test_one_message_of_sixteen_maximum_bodies_fits_the_real_response_limit(self):
        from quorum_api import QuorumApi, QuorumError, ServiceConfig
        service = Service()
        api = QuorumApi(ServiceConfig('https://example.com', 'sb_publishable_synthetic'), lambda: 'synthetic')
        with patch('quorum_api.build_opener', return_value=service), \
             self.assertRaisesRegex(QuorumError, '^response_too_large$'):
            api.call('conversation_read', {'quorum_id':'q', 'agent_id':'a', 'conversation_id':CONVERSATION,
                                           'after_sequence':0})
        self.assertEqual(self.read(service)['body'], Service.BODY)

    def test_an_older_server_falls_back_to_the_conversation_read(self):
        service = Service(single=False, rows=3)
        self.assertEqual(self.read(service, service.rows[0]['id'])['body'], Service.BODY)
        self.assertEqual(service.requests, ['session_message', 'conversation_read'])

    def test_an_older_server_that_cannot_fit_the_page_names_the_next_step(self):
        from quorum_session import MessageUnavailable
        with self.assertRaises(MessageUnavailable) as caught: self.read(Service(single=False))
        self.assertEqual(caught.exception.reason, 'server_update_required')
        self.assertIn('20261106160000_session_message.sql', caught.exception.diagnostic()['next_step'])

    def test_another_agents_message_is_never_returned(self):
        from quorum_session import MessageUnavailable
        foreign = Service()
        foreign.rows[0]['recipient_id'] = 'other'
        for service, identity in ((foreign, None), (Service(), OTHER)):
            with self.assertRaises(MessageUnavailable) as caught: self.read(service, identity)
            self.assertEqual(caught.exception.reason, 'not_found')

    def test_a_service_outage_stays_retryable(self):
        from quorum_api import QuorumError
        service = Service()
        service.open = lambda *_, **__: (_ for _ in ()).throw(URLError('down'))
        with self.assertRaisesRegex(QuorumError, '^service_unavailable$'): self.read(service)


class WindowsCommandLimitTests(unittest.TestCase):
    def test_the_limit_is_measured_after_list2cmdline_quoting(self):
        from quorum_session import fits_windows_command
        # Each quote in a body becomes four characters: JSON-escaped, then argv-escaped.
        command = lambda count: ['codex.exe', 'queue', '--message', json.dumps('"'*count)]
        self.assertTrue(fits_windows_command(command(7000)))
        self.assertFalse(fits_windows_command(command(8192)))

    def test_units_are_utf16(self):
        from quorum_session import fits_windows_command
        self.assertTrue(fits_windows_command(['codex.exe', 'x'*30000]))
        self.assertFalse(fits_windows_command(['codex.exe', '\U0001F600'*20000]))


class HelperChildTests(unittest.TestCase):
    def test_a_spawn_failure_is_unsent_and_a_live_child_failure_kills_it(self):
        from quorum_claude_pipe import DeliveryUnsent, run_child
        with patch('quorum_claude_pipe.subprocess.Popen', side_effect=FileNotFoundError(2, 'missing')):
            with self.assertRaises(DeliveryUnsent): run_child(['missing'], payload='x', timeout=1)
        stuck = Child(failure=subprocess.TimeoutExpired('child', 1))
        with patch('quorum_claude_pipe.subprocess.Popen', return_value=stuck), \
             patch.object(stuck, 'kill') as kill:
            with self.assertRaisesRegex(ValueError, '^session_delivery_unknown$'):
                run_child(['child'], timeout=1)
        kill.assert_called_once_with()


if __name__ == '__main__': unittest.main()
