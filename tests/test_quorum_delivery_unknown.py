"""A settled delivery-unknown keeps native reception running, and a sender sees an offline recipient (#166).

Synthetic only: a scripted worker under the real run_listener and ledger, and fake RPCs for `session send` and
`session reply`. No client, chat, model or service is touched.
"""
import hang_guard  # noqa: F401
import io
import os
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
import uuid
from unittest.mock import patch

from quorum_test_support import isolated_profiles

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'skills/quorum/scripts'))

SID = '11111111-1111-4111-8111-111111111111'
MID = '22222222-2222-4222-8222-222222222222'
PEER = '55555555-5555-4555-8555-555555555555'
REPLY = '66666666-6666-4666-8666-666666666666'
RECORD = {'engine': 'codex', 'session_ref': SID, 'agent_id': 'a', 'quorum_id': 'q', 'project_ref': 'owner/repo',
          'quorum': 'team'}
CLIENT_SESSION_VARIABLES = ('CODEX_THREAD_ID', 'GROK_SESSION_ID', 'GROK_LEADER_SOCKET', 'CLAUDE_CODE_SESSION_ID',
                            'CLAUDE_CODE_MESSAGING_SOCKET', 'CLAUDE_CODE_MESSAGING_TOKEN')


def unknown():
    return ValueError('session_delivery_unknown')


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
    """Each step is an outcome, an exception, or a callable run first; an empty script stops the run."""
    def __init__(self, control, steps):
        self.control, self.steps = control, list(steps)
        self.reconciled = self.runs = 0

    def reconcile(self): self.reconciled += 1

    def run_once(self):
        self.runs += 1
        if not self.steps:
            self.control.request_stop()
            return 'idle'
        step = self.steps.pop(0)
        if callable(step) and not isinstance(step, BaseException): step = step()
        if isinstance(step, BaseException): raise step
        return step


class ListenerTests(unittest.TestCase):
    def run_scripted(self, ledger, steps, *, retry=True):
        from quorum_listener import Control, run_listener
        worker, seen = ScriptedWorker(Control(ledger), steps), []
        def sleep(_):
            status = Control(ledger).status()
            seen.append((status['state'], status.get('code'), status['listener']))
        result = run_listener(ledger, lambda: worker, str(uuid.uuid4()), sleep=sleep, retry=retry)
        return result, worker, seen

    def test_an_empty_ledger_reconciles_and_keeps_running(self):
        with Ledger() as ledger:
            result, worker, seen = self.run_scripted(ledger, [unknown(), unknown(), 'delivered', unknown()])
            self.assertEqual((result['state'], result['handled']), ('stopped', 1))
            # A short pause before each fresh reconcile, reset by the completed drain in between.
            self.assertEqual(len(seen), 10 + 20 + 10)
            self.assertEqual(set(seen), {('degraded', 'session_delivery_unknown', 'running')})
            self.assertEqual(worker.reconciled, 4, 'every resumption reconciles before any further delivery')
            self.assertIsNone(ledger.setting('presence'), 'nothing is pending, so presence is not changed')

    def test_an_owner_stop_during_the_pause_ends_the_run(self):
        from quorum_listener import Control, run_listener, stop_listener
        with Ledger() as ledger:
            sleeps = []
            def sleep(_):
                sleeps.append(1)
                if len(sleeps) == 3: stop_listener(ledger)
            worker = ScriptedWorker(Control(ledger), [unknown(), 'delivered'])
            result = run_listener(ledger, lambda: worker, str(uuid.uuid4()), sleep=sleep, retry=True)
            self.assertEqual((result['state'], result['handled'], len(sleeps)), ('stopped', 0, 3))

    def test_an_ambiguous_ledger_still_stops_and_reports(self):
        from quorum_delivery import Deliveries
        def intent(ledger):
            ledger.setting('delivery:' + MID, {'state': 'intent', 'attempt_id': str(uuid.uuid4())})
        def submitted(ledger):
            ledger.setting('delivery:' + MID, {'state': 'submitted'})
        def attempt(ledger):
            with ledger.db: ledger.db.execute("insert into attempts values (?,?,?,?)", (MID, 1, 'unknown', None))
        for name, leave in (('unknown_attempt', intent), ('pending_receipt', submitted), ('uncertain_attempt', attempt)):
            with self.subTest(name), Ledger() as ledger:
                def fail(): leave(ledger); return unknown()
                result, worker, seen = self.run_scripted(ledger, [fail, 'delivered'])
                self.assertEqual((result['state'], result['code'], result['listener']),
                                 ('error', 'session_delivery_unknown', 'offline'))
                self.assertEqual((seen, worker.reconciled, worker.runs), ([], 1, 1), 'no pause, reconcile or redelivery')
                self.assertTrue(Deliveries(ledger).rows() or name == 'uncertain_attempt', 'the row stays for the owner')

    def test_without_the_native_opt_in_it_still_stops(self):
        with Ledger() as ledger:
            result, _, seen = self.run_scripted(ledger, [unknown(), 'delivered'], retry=False)
            self.assertEqual((result['state'], result['code'], seen), ('error', 'session_delivery_unknown', []))

    def test_only_the_delivery_unknown_code_is_settled(self):
        with Ledger() as ledger:
            result, _, seen = self.run_scripted(ledger, [ValueError('session_binding_denied'), 'delivered'])
            self.assertEqual((result['state'], result['code'], seen), ('error', 'session_binding_denied', []))

    def test_a_reconciled_away_row_no_longer_breaks_the_relay(self):
        from quorum_session import Relay
        with Ledger() as ledger:
            class Api:
                def call(self, operation, payload, request_id=None):
                    from quorum_release import installed_release
                    return installed_release() if operation == 'session_requirements' else []
            relay = Relay(Api(), ledger, RECORD, lambda message: None)
            relay.awaiting.append(MID)
            self.assertEqual(relay.run_once(), 'idle')
            self.assertEqual(relay.awaiting, [])


class Service:
    def __init__(self, availability='online', *, reason=None, fail=()):
        self.availability, self.reason, self.fail, self.calls = availability, reason, set(fail), []

    def call(self, operation, payload, request_id=None):
        from quorum_api import QuorumError
        from quorum_release import installed_release
        self.calls.append(operation)
        if operation in self.fail: raise QuorumError('service_error')
        if operation == 'session_requirements': return installed_release()
        if operation == 'resolve_role': return {'agent_id': PEER}
        if operation == 'message_send': return {'message_id': MID, 'delivery_id': 'd'}
        if operation == 'session_reply': return {'reply_message_id': REPLY, 'conversation_id': str(uuid.uuid4())}
        if operation == 'session_delivery_status':
            return {'message_id': payload['message_id'], 'recipient_id': PEER, 'recipient_role': 'doer', 'status': 'sent'}
        if operation == 'session_peers':
            return [{'agent_id': str(uuid.uuid4()), 'role': 'other', 'availability': 'online'},
                    {'agent_id': PEER, 'role': 'doer', 'availability': self.availability, 'reason': self.reason,
                     'last_seen': '2026-11-07T08:18:00+00:00'}]
        return {'queued': True}


class SenderTests(unittest.TestCase):
    def setUp(self):
        from quorum_state import LocalLedger
        isolated = patch.dict(os.environ, {key: value for key, value in os.environ.items()
                                           if key not in CLIENT_SESSION_VARIABLES}, clear=True)
        isolated.start()
        self.addCleanup(isolated.stop)
        root = tempfile.TemporaryDirectory()
        self.addCleanup(root.cleanup)
        self.root = Path(root.name)
        self.ledger = LocalLedger(self.root / 'sessions' / SID)
        self.addCleanup(self.ledger.db.close)
        self.ledger.setting('session-agent', RECORD)

    def run_session(self, api, action, **fields):
        import quorum_live
        from quorum_session import dispatch
        from quorum_state import LocalLedger
        args = SimpleNamespace(session_id=SID, action=action, priority='normal', **fields)
        auth = LocalLedger(self.root)
        try:
            with patch.object(quorum_live, 'api_for', lambda ledger: (api, None, None)), \
                 patch.dict(os.environ, {'CODEX_THREAD_ID': SID}), \
                 patch('sys.stdin', SimpleNamespace(buffer=io.BytesIO(b'status?'))), \
                 patch('quorum_session.RECEIPT_WAIT_SECONDS', 0):
                return dispatch(args, auth, None)
        finally: auth.db.close()

    def send(self, api):
        return self.run_session(api, 'send', to_role='doer', request_id='r1', text_stdin=True)

    @isolated_profiles
    def test_send_to_an_unavailable_role_succeeds_with_a_warning(self):
        api = Service('unavailable', reason='receipt_timeout')
        result = self.send(api)
        self.assertEqual(result['message_id'], MID, 'the message is sent and durable')
        self.assertEqual((result['recipient_availability'], result['recipient_reason'], result['recipient_last_seen']),
                         ('unavailable', 'receipt_timeout', '2026-11-07T08:18:00+00:00'))
        self.assertIn('do not resend', result['recipient_warning'])
        self.assertEqual(api.calls.count('message_send'), 1)

    @isolated_profiles
    def test_send_to_an_online_role_has_no_warning(self):
        result = self.send(Service('online'))
        self.assertEqual(result['recipient_availability'], 'online')
        self.assertNotIn('recipient_warning', result)
        self.assertNotIn('recipient_reason', result)

    @isolated_profiles
    def test_a_failed_or_odd_peer_read_never_costs_the_send(self):
        for api, warned in ((Service(fail={'session_peers'}), False), (Service('something_new'), False),
                            (Service('unavailable', reason='x' * 99), True)):
            with self.subTest(availability=api.availability, fail=api.fail):
                result = self.send(api)
                self.assertEqual(result['message_id'], MID)
                self.assertEqual('recipient_warning' in result, warned)
                self.assertNotIn('recipient_reason', result, 'an unknown reason is dropped, never echoed')

    @isolated_profiles
    def test_reply_to_an_unavailable_sender_succeeds_with_a_warning(self):
        api = Service('client_restarted_unrecovered')
        result = self.run_session(api, 'reply', message_id=MID, text_stdin=True)
        self.assertEqual(result['reply_message_id'], REPLY)
        self.assertEqual(result['recipient_availability'], 'client_restarted_unrecovered')
        self.assertIn('recipient_warning', result)
        self.assertLess(api.calls.index('session_reply'), api.calls.index('session_delivery_status'))

    @isolated_profiles
    def test_reply_without_the_status_rpc_still_succeeds_quietly(self):
        result = self.run_session(Service('unavailable', fail={'session_delivery_status'}), 'reply', message_id=MID,
                                  text_stdin=True)
        self.assertEqual(result['reply_message_id'], REPLY)
        self.assertNotIn('recipient_availability', result)


if __name__ == '__main__':
    unittest.main()
