"""Transient service outages: classification and opt-in native listener retry, synthetic only."""
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
import uuid
from unittest.mock import patch
from urllib.error import HTTPError, URLError

from quorum_test_support import isolated_profiles

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'skills/quorum/scripts'))

REQUIREMENTS = {'protocol_version':2, 'skill_version':'0.3.0'}


def raising(error):
    class Opener:
        def open(self, request, timeout):
            raise error
    return lambda *_: Opener()


class ClassificationTests(unittest.TestCase):
    def outcome(self, error):
        from quorum_api import QuorumError, ServiceConfig, QuorumApi, is_transient_service
        api = QuorumApi(ServiceConfig('https://example.com', 'pub'), lambda: 'private-token')
        with patch('quorum_api.build_opener', raising(error)):
            with self.assertRaises(QuorumError) as caught:
                api.call('session_inbox', {'agent_id':'a'})
        self.assertNotIn('private-token', repr(caught.exception.__dict__) + str(caught.exception))
        self.assertNotIn('example.com', str(caught.exception))
        return str(caught.exception), is_transient_service(caught.exception)

    def http(self, code):
        return HTTPError('https://example.com/rest/v1/rpc/x?secret=1', code, 'synthetic',
                         {'Authorization':'Bearer private-token'}, io.BytesIO(b'private body'))

    def test_transient_http_and_network_failures_are_marked_retryable(self):
        for code in (408, 429, 500, 502, 503, 504):
            with self.subTest(code=code):
                self.assertEqual(self.outcome(self.http(code)), ('service_error', True))
        self.assertEqual(self.outcome(URLError('synthetic')), ('service_unavailable', True))
        self.assertEqual(self.outcome(OSError('synthetic')), ('service_unavailable', True))

    def test_denial_missing_rpc_and_client_errors_stay_terminal(self):
        for code in (401, 403):
            with self.subTest(code=code):
                self.assertEqual(self.outcome(self.http(code)), ('auth_required', False))
        for code in (400, 404, 409, 422, 501):
            with self.subTest(code=code):
                self.assertEqual(self.outcome(self.http(code)), ('service_error', False))

    def test_only_typed_service_errors_are_transient(self):
        from quorum_api import QuorumError, is_transient_service
        self.assertTrue(is_transient_service(QuorumError('service_unavailable')))
        self.assertFalse(is_transient_service(QuorumError('service_error')))
        self.assertTrue(is_transient_service(QuorumError('service_error', transient=True)))
        for error in (QuorumError('auth_required', transient=True), ValueError('service_unavailable'),
                      ValueError('service_error'), RuntimeError('service_unavailable'),
                      QuorumError('coordination_unavailable')):
            with self.subTest(error=repr(error)):
                self.assertFalse(is_transient_service(error))

    def test_http_layer_still_makes_exactly_one_attempt(self):
        from quorum_api import QuorumError, ServiceConfig, QuorumApi
        attempts = []
        class Opener:
            def open(self, request, timeout):
                attempts.append(request.full_url)
                raise HTTPError(request.full_url, 503, 'synthetic', {}, io.BytesIO(b''))
        api = QuorumApi(ServiceConfig('https://example.com', 'pub'), lambda: 'token')
        with patch('quorum_api.build_opener', lambda *_: Opener()), self.assertRaises(QuorumError):
            api.call('message_send', {'body':'x'}, 'request')
        self.assertEqual(len(attempts), 1)


class PresenceTests(unittest.TestCase):
    @isolated_profiles
    def test_marked_transient_heartbeat_is_unreachable_but_unmarked_stays_terminal(self):
        from quorum_api import QuorumError
        from quorum_presence import Presence
        from quorum_state import LocalLedger
        with tempfile.TemporaryDirectory() as root:
            ledger = LocalLedger(Path(root))
            try:
                failure, ids, now = [None], [], [0]
                class Api:
                    def call(self, operation, payload):
                        if operation == 'session_requirements': return REQUIREMENTS
                        ids.append(payload['connection_id'])
                        raise failure[0]
                health = Presence(Api(), {'agent_id':'a', 'session_ref':'s'}, ledger, clock=lambda: now[0])
                failure[0] = QuorumError('service_error', transient=True)
                health.tick()
                self.assertEqual(ledger.setting('presence')['state'], 'unreachable')
                now[0] = 20
                failure[0] = QuorumError('service_error')
                with self.assertRaisesRegex(QuorumError, 'coordination_unavailable'): health.tick()
                self.assertEqual(len(set(ids)), 1, 'a transient retry keeps its heartbeat epoch')
            finally: ledger.db.close()


class Ledger:
    def __enter__(self):
        from quorum_state import LocalLedger
        self.temporary = tempfile.TemporaryDirectory()
        self.ledger = LocalLedger(Path(self.temporary.name))
        return self.ledger

    def __exit__(self, *_):
        self.ledger.db.close()
        self.temporary.cleanup()


class ScriptedWorker:
    """Each step is an outcome string or an exception; reconcile has its own script."""
    def __init__(self, control, steps, reconciles=()):
        self.control, self.steps, self.reconciles = control, list(steps), list(reconciles)
        self.reconciled = self.runs = 0

    def reconcile(self):
        self.reconciled += 1
        if self.reconciles:
            step = self.reconciles.pop(0)
            if isinstance(step, BaseException): raise step

    def run_once(self):
        self.runs += 1
        if not self.steps:
            self.control.request_stop()
            return 'idle'
        step = self.steps.pop(0)
        if isinstance(step, BaseException): raise step
        return step


class ListenerRetryTests(unittest.TestCase):
    def run_scripted(self, ledger, steps, reconciles=(), *, retry=True, waiter=None, sleeps=None):
        from quorum_listener import Control, run_listener
        control = Control(ledger)
        worker = ScriptedWorker(control, steps, reconciles)
        sleeps = [] if sleeps is None else sleeps
        made = []
        def make():
            made.append(1)
            return worker
        result = run_listener(ledger, make, str(uuid.uuid4()), sleep=sleeps.append, waiter=waiter, retry=retry)
        return result, worker, sleeps, made

    def test_default_listener_still_ends_on_the_first_outage(self):
        from quorum_api import QuorumError
        with Ledger() as ledger:
            result, worker, _, _ = self.run_scripted(ledger, [QuorumError('service_unavailable')], retry=False)
            self.assertEqual((result['state'], result['code'], result['listener']),
                             ('error', 'service_unavailable', 'offline'))
            self.assertEqual(worker.runs, 1)

    def test_outages_in_reconcile_and_inbox_back_off_then_resume_with_the_same_worker(self):
        from quorum_api import QuorumError
        with Ledger() as ledger:
            outage = QuorumError('service_unavailable')
            result, worker, sleeps, made = self.run_scripted(
                ledger, [outage, QuorumError('service_error', transient=True), 'delivered'],
                reconciles=[outage, None, None, None])
            self.assertEqual(result['state'], 'stopped')
            self.assertEqual(result['handled'], 1)
            self.assertEqual(len(made), 1, 'the same Relay keeps its awaiting receipts')
            # reconcile outage, inbox outage, second inbox outage: 10, 20, 40 seconds.
            self.assertEqual(sum(sleeps), 70)
            self.assertTrue(all(value == 1 for value in sleeps), 'stop-aware one-second steps')
            # Every resumption re-runs authoritative reconciliation first.
            self.assertEqual(worker.reconciled, 4)

    def test_backoff_is_capped_and_resets_after_a_successful_drain(self):
        from quorum_api import QuorumError
        with Ledger() as ledger:
            outage = QuorumError('service_unavailable')
            result, _, sleeps, _ = self.run_scripted(ledger, [outage] * 5 + ['idle', outage])
            # Five capped outages, the ordinary ten-second idle poll, then a reset first delay.
            self.assertEqual(sum(sleeps), 10 + 20 + 40 + 60 + 60 + 10 + 10)
            self.assertEqual(result['state'], 'stopped')

    def test_degraded_status_is_live_but_not_healthy(self):
        from quorum_api import QuorumError
        from quorum_listener import Control
        with Ledger() as ledger:
            seen = []
            def sleep(_):
                status = Control(ledger).status()
                seen.append((status['state'], status.get('code'), (ledger.setting('presence') or {}).get('state')))
                Control(ledger).request_stop()
            from quorum_listener import run_listener
            worker = ScriptedWorker(Control(ledger), [QuorumError('service_unavailable')])
            result = run_listener(ledger, lambda: worker, str(uuid.uuid4()), sleep=sleep, retry=True)
            self.assertEqual(seen, [('degraded', 'service_unavailable', 'unreachable')])
            self.assertEqual(result['state'], 'stopped')
            self.assertEqual(worker.runs, 1, 'a stop during backoff prevents any later dispatch')

    def test_terminal_errors_are_never_retried(self):
        from quorum_api import QuorumError
        terminal = (QuorumError('auth_required'), QuorumError('service_error'), QuorumError('coordination_unavailable'),
                    QuorumError('skill_update_required'), QuorumError('release_manifest_invalid'),
                    ValueError('session_recovery_denied'), ValueError('session_binding_denied'),
                    ValueError('session_delivery_unknown'), ValueError('service_unavailable'))
        for error in terminal:
            with self.subTest(error=repr(error)), Ledger() as ledger:
                result, worker, sleeps, _ = self.run_scripted(ledger, [error])
                self.assertEqual((result['state'], worker.runs, sleeps), ('error', 1, []))

    def test_outage_during_waiter_pulse_is_retried_without_replaying_the_inbox(self):
        from quorum_api import QuorumError
        class Waiter:
            stop_aware = True
            def __init__(self): self.pulses, self.prepared, self.closed = 0, 0, 0
            def prepare(self, stopping): self.prepared += 1
            def pulse(self, stopping=None):
                self.pulses += 1
                if self.pulses == 1: raise QuorumError('service_unavailable')
            def wait(self, stopping): pass
            def close(self): self.closed += 1
        with Ledger() as ledger:
            waiter = Waiter()
            result, worker, sleeps, _ = self.run_scripted(ledger, [], waiter=waiter)
            self.assertEqual((result['state'], waiter.prepared, waiter.closed, sum(sleeps)), ('stopped', 2, 1, 10))
            self.assertEqual(worker.runs, 1)


class RelayOutageTests(unittest.TestCase):
    """A real Relay under the retrying listener: one pipe write per message, idempotent ACK."""
    @isolated_profiles
    def test_submitted_message_survives_outages_and_is_never_piped_again(self):
        from quorum_api import QuorumError
        from quorum_listener import Control, run_listener
        from quorum_session import Relay
        message_id = str(uuid.uuid4())
        with Ledger() as ledger:
            control, pipes, acks, inbox_calls = Control(ledger), [], [], [0]
            record = {'agent_id':'agent', 'session_ref':'session'}
            class Api:
                def call(self, operation, payload, request_id=None):
                    if operation == 'session_requirements': return REQUIREMENTS
                    if operation == 'session_ack':
                        acks.append(payload['message_id'])
                        if len(acks) == 1: raise QuorumError('service_unavailable')
                        return {}
                    inbox_calls[0] += 1
                    if inbox_calls[0] in (3, 5): raise QuorumError('service_error', transient=True)
                    if acks[1:]:
                        control.request_stop()
                        return []
                    return [{'id':message_id, 'recipient_id':'agent', 'body':'synthetic'}]
            def pipe(message):
                pipes.append(message['id'])
                return 'submitted'
            relay = Relay(Api(), ledger, record, pipe)
            def sleep(_):
                # The bound chat confirms receipt while the service is down; its own ACK fails.
                if ledger.setting('delivery:' + message_id) == {'state':'submitted'}:
                    ledger.setting('delivery:' + message_id, {'state':'queued'})
            result = run_listener(ledger, lambda: relay, str(uuid.uuid4()), sleep=sleep, retry=True)
            self.assertEqual(pipes, [message_id])
            self.assertEqual(acks, [message_id, message_id])
            self.assertEqual(ledger.setting('delivery:' + message_id), {'state':'acked'})
            self.assertEqual(result['state'], 'stopped')


class RealtimeTerminalTests(unittest.TestCase):
    @isolated_profiles
    def test_recovery_denial_escapes_the_realtime_fallback(self):
        from quorum_realtime import RealtimeWait
        from quorum_state import LocalLedger
        with tempfile.TemporaryDirectory() as root:
            ledger = LocalLedger(Path(root))
            try:
                for code in ('session_recovery_denied', 'session_binding_denied'):
                    with self.subTest(code=code):
                        class Socket:
                            def send(self, frame): pass
                            def recv(self, timeout): raise TimeoutError
                            def close(self): pass
                        def deny(): raise ValueError(code)
                        waiter = RealtimeWait(object(), {'agent_id':str(uuid.uuid4())}, ledger,
                                              clock=lambda: 100, sleep=lambda _: None, authorize=deny)
                        waiter.socket, waiter.heartbeat_at, waiter.join_ref = Socket(), 0, '1'
                        waiter.api = type('Api', (), {'token': lambda self: None})()
                        waiter.jwt = None
                        with self.assertRaisesRegex(ValueError, code): waiter.wait(lambda: False)
                        self.assertIsNone(waiter.socket)
            finally: ledger.db.close()

    @isolated_profiles
    def test_repeated_prepare_closes_the_previous_socket(self):
        from quorum_realtime import RealtimeWait
        from quorum_state import LocalLedger
        with tempfile.TemporaryDirectory() as root:
            ledger = LocalLedger(Path(root))
            try:
                closed, agent = [], str(uuid.uuid4())
                class Old:
                    def close(self): closed.append(1)
                class New:
                    def send(self, frame): pass
                    def recv(self, timeout):
                        return json.dumps({'topic':'realtime:quorum:agent:' + agent, 'event':'phx_reply',
                                           'ref':'1', 'payload':{'status':'ok'}})
                    def close(self): pass
                class Api:
                    config = type('Config', (), {'origin':'https://example.com', 'publishable_key':'pub'})()
                    def token(self): return 'jwt'
                fresh = New()
                waiter = RealtimeWait(Api(), {'agent_id':agent}, ledger, clock=lambda: 0,
                                      connect=lambda *_, **__: fresh)
                waiter.socket = Old()
                waiter.prepare(lambda: False)
                self.assertEqual(closed, [1], 'resubscription after an outage must not leak the old socket')
                self.assertIs(waiter.socket, fresh)
            finally: ledger.db.close()



SID = '11111111-1111-4111-8111-111111111111'
AID = '22222222-2222-4222-8222-222222222222'
MID = '33333333-3333-4333-8333-333333333333'


class OrdinaryRunAuthorityTests(unittest.TestCase):
    """An explicit-start run retrying after an outage obeys the same reception authority as recovery."""
    def scenario(self, cancel, *, recovering=False, publish=True, startup_seconds=None):
        import os
        from types import SimpleNamespace
        from quorum_api import QuorumApi, QuorumError, ServiceConfig
        from quorum_listener import Control, run_listener, stop_listener
        from quorum_resume import enable
        from quorum_session import dispatch
        from quorum_state import LocalLedger
        with tempfile.TemporaryDirectory() as path:
            state = Path(path)
            ledger = LocalLedger(state / 'sessions' / SID)
            try:
                record = {'engine':'claude', 'session_ref':SID, 'agent_id':AID, 'device_id':'d', 'quorum_id':'q',
                          'project_ref':'p', 'cwd':str(state), 'role':'worker', 'quorum':'team',
                          'messaging_socket':'/synthetic/inbox'}
                ledger.setting('session-agent', record)
                if publish: enable(ledger, record)
                grant, calls, writes, cancelled, inbox = ledger.setting('session-reception'), [], [], [False], [0]
                def request(url, headers, body):
                    operation = url.rsplit('/', 1)[-1]
                    calls.append((operation, cancelled[0]))
                    if operation == 'session_requirements': return REQUIREMENTS
                    if operation == 'session_inbox':
                        inbox[0] += 1
                        if inbox[0] == 2: raise QuorumError('service_unavailable')
                        if cancel == 'owner_stop' and inbox[0] == 4:
                            stop_listener(ledger, revoke_reception=True)
                            cancelled[0] = True
                        return [] if inbox[0] == 1 else [{'id':MID, 'recipient_id':AID, 'body':'synthetic'}]
                    raise AssertionError(operation)
                api = QuorumApi(ServiceConfig('https://example.invalid', 'synthetic'), lambda: 'synthetic', request)
                def sleep(_):
                    if cancelled[0] or cancel in ('owner_stop', None): return
                    cancelled[0] = True
                    if cancel == 'binding_changed': ledger.setting('session-agent', {**record, 'agent_id':str(uuid.uuid4())})
                    else: ledger.setting('session-reception', {**grant, **({'enabled':False} if cancel == 'revoked'
                                                                             else {'expires_at':1})})
                def queue(record, message, auth):
                    writes.append(cancelled[0])
                    Control(ledger).request_stop()
                    return 'submitted'
                class Waiter:
                    def __init__(self, *args, **kwargs): pass
                    def prepare(self, stopping): pass
                    def wait(self, stopping): pass
                    def close(self): pass
                def launch(ledger, make, run_id, **options):
                    return run_listener(ledger, make, run_id, sleep=sleep, **options)
                from contextlib import ExitStack
                with ExitStack() as patches:
                    for context in (patch.dict(os.environ, {'CLAUDE_CODE_SESSION_ID':SID}, clear=True),
                                    patch('quorum_live.api_for', return_value=(api, None, None)),
                                    patch('quorum_claude_pipe.binding', return_value='/synthetic/inbox'),
                                    patch('signal.signal'), patch('quorum_realtime.RealtimeWait', Waiter),
                                    patch('quorum_session.enqueue', side_effect=queue),
                                    patch('quorum_session.run_listener', side_effect=launch)):
                        patches.enter_context(context)
                    if startup_seconds is not None:
                        patches.enter_context(patch('quorum_resume.STARTUP_SECONDS', startup_seconds))
                    result = dispatch(SimpleNamespace(action='run', session_id=SID, run_id=str(uuid.uuid4()),
                                                      recovering=recovering), SimpleNamespace(root=state), None)
                return result, writes, [operation for operation, after in calls if after]
            finally: ledger.db.close()

    @isolated_profiles
    def test_cancellation_during_an_outage_stops_ordinary_and_recovering_runs_alike(self):
        for recovering in (False, True):
            for cancel in ('revoked', 'expired', 'binding_changed', 'owner_stop'):
                with self.subTest(recovering=recovering, cancel=cancel):
                    result, writes, after = self.scenario(cancel, recovering=recovering)
                    self.assertEqual(writes, [], 'no IPC submission after cancellation')
                    self.assertEqual(after, [], 'no service dispatch after cancellation')
                    # An owner stop ends as stopped; expiry, revocation or a changed binding is a denial.
                    expected = ('stopped', None) if cancel == 'owner_stop' else ('error', 'session_recovery_denied')
                    self.assertEqual((result['state'], result.get('code')), expected)

    @isolated_profiles
    def test_ordinary_run_keeps_working_with_its_published_grant(self):
        result, writes, _ = self.scenario(None)
        self.assertEqual((writes, result['state']), ([False], 'stopped'))

    @isolated_profiles
    def test_unpublished_startup_authority_is_bounded(self):
        # Before the parent publishes the grant, a new run may start; it cannot outlive the readiness bound.
        result, writes, _ = self.scenario(None, publish=False)
        self.assertEqual(writes, [False])
        with self.assertRaisesRegex(ValueError, 'session_recovery_denied'):
            self.scenario(None, publish=False, startup_seconds=0)


class DegradedReuseTests(unittest.TestCase):
    @isolated_profiles
    def test_start_never_reports_or_grants_a_degraded_running_listener(self):
        import threading
        from quorum_api import QuorumError
        from quorum_listener import Control, run_listener
        from quorum_resume import enable
        from quorum_session import Relay, start
        from quorum_state import LocalLedger
        class Api:
            def __init__(self): self.calls = []
            def call(self, operation, payload, request_id=None):
                self.calls.append(operation)
                if operation == 'session_requirements': return REQUIREMENTS
                raise QuorumError('service_unavailable')
        with tempfile.TemporaryDirectory() as path:
            state = Path(path)
            ledger = LocalLedger(state / 'sessions' / SID)
            record = {'engine':'claude', 'session_ref':SID, 'agent_id':AID, 'cwd':str(state),
                      'messaging_socket':'/synthetic/inbox'}
            ledger.setting('session-agent', record)
            enable(ledger, record)
            ledger.setting('session-reception', {**ledger.setting('session-reception'), 'expires_at':1})
            api, run_id, degraded, release = Api(), str(uuid.uuid4()), threading.Event(), threading.Event()
            def worker():
                own = LocalLedger(ledger.root)
                def sleep(_):
                    degraded.set()
                    release.wait(timeout=5)
                try: run_listener(own, lambda: Relay(api, own, record, lambda _: None), run_id, retry=True, sleep=sleep)
                finally: own.db.close()
            thread = threading.Thread(target=worker)
            thread.start()
            try:
                self.assertTrue(degraded.wait(5))
                before = ledger.setting('session-reception')
                with patch('quorum_session.READY_SECONDS', .3):
                    with self.assertRaisesRegex(QuorumError, 'listener_not_ready'):
                        start(state, ledger, SID, api=api, endpoint='/synthetic/inbox')
                self.assertEqual(ledger.setting('session-reception'), before, 'no grant while degraded')
                self.assertFalse(Control(ledger).status()['stop_requested'], 'another run is never stopped here')
            finally:
                Control(ledger).request_stop(run_id)
                release.set()
                thread.join(5)
                ledger.db.close()

if __name__ == '__main__':
    unittest.main()
