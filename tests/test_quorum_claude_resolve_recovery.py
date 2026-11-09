"""A resolve-then-wait command killed with no result line (#206): the next receive reports and finishes it once.

A fake API, real ledgers and locks; a `Killed` BaseException stands in for taskkill, so no handler of the command
runs after it, as with a real kill. No model, client or service.
"""
import hang_guard  # noqa: F401
import json
import unittest
from unittest.mock import patch

from quorum_test_support import isolated_profiles
from test_quorum_claude_worker import OWNER_PROOF, M1, M2, Api, message
from test_quorum_claude_worker_turns import Sleepy, Steps


class Killed(BaseException):
    """The process ended mid-command: nothing after it runs, and no result line is printed."""


def killed():
    raise Killed()


class KillApi(Api):
    """`kill_before`: the next call of an operation dies before it reaches the service."""
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.kill_before = set()

    def call(self, operation, payload, request_id=None):
        if operation in self.kill_before:
            self.kill_before.discard(operation)
            raise Killed()
        return super().call(operation, payload, request_id)


@OWNER_PROOF
class InterruptedResolveTests(Steps):
    def offered(self, api):
        self.bound(api)
        _, woke = self.wait(api)
        self.assertEqual(woke['data']['id'], M1)

    def kill(self, api, action, stdin=b'', waiter=None):
        """Run the brief's resolve template; it must die with no result line."""
        with patch('quorum_native_inbox.RealtimeWait', lambda *_, **__: waiter or Sleepy()), \
                self.assertRaises(Killed):
            self.cli(api, *self.template(action, M1, '--max-seconds', '1'), stdin=stdin)

    def recover(self, api):
        """The brief's step for a missing result line."""
        code, result, text = self.receive(api)
        self.assertEqual(code, 0, text)
        return result['data']

    def journal(self):
        import quorum_native_inbox as inbox
        return self.worker_ledger(self.slot).setting(inbox.RESOLVE_JOURNAL)

    def replies(self, api):
        return [call for call in api.calls if call[0] == 'session_reply']

    def only(self, data):
        reports = data['interrupted_resolve']
        self.assertEqual(len(reports), 1, reports)
        return reports[0]

    @isolated_profiles
    def test_a_reply_killed_before_the_service_call_is_replayed_once(self):
        api = KillApi(inbox=[message(M1, body='ping')])
        self.offered(api)
        api.kill_before.add('session_reply')
        self.kill(api, 'reply', stdin=b'pong')
        self.assertEqual(self.journal()['state'], 'pending', 'persisted before the service call')
        self.assertEqual(api.replies, {}, 'nothing reached the service')
        data = self.recover(api)
        report = self.only(data)
        self.assertEqual((report['action'], report['message_id'], report['resolved_before_crash']),
                         ('reply', M1, False))
        self.assertEqual(report['reply_message_id'], api.replies[M1])
        self.assertEqual(report['request_id'], 'native-reply:' + self.worker_ledger(self.slot)
                         .setting('session-agent')['agent_id'] + ':' + M1)
        self.assertEqual([call[1]['body'] for call in self.replies(api)], ['pong'], 'replayed once, same answer')
        self.assertEqual(api.acked, [M1])
        self.assertEqual(data['state'], 'timeout', 'the answered message is not offered again')
        self.assertIsNone(self.journal())
        self.assertNotIn('interrupted_resolve', self.recover(api), 'reported once')
        self.assertEqual(len(self.replies(api)), 1)

    @isolated_profiles
    def test_a_reply_killed_after_the_service_call_is_reported_and_never_duplicated(self):
        api = KillApi(inbox=[message(M1, body='ping')])
        self.offered(api)
        api.hooks['session_reply'] = killed
        self.kill(api, 'reply', stdin=b'pong')
        sent = api.replies[M1]
        self.assertEqual(self.journal()['state'], 'pending', 'the answer arrived after the kill')
        data = self.recover(api)
        report = self.only(data)
        self.assertEqual((report['resolved_before_crash'], report['reply_message_id']), (True, sent))
        self.assertEqual(api.replies, {M1: sent}, 'the identical replay returned the first answer')
        self.assertEqual(api.acked, [M1])
        self.assertEqual(data['state'], 'timeout')

    @isolated_profiles
    def test_a_reply_killed_during_its_wait_is_reported_without_a_service_call(self):
        api = KillApi(inbox=[message(M1, body='ping')])
        self.offered(api)
        self.kill(api, 'reply', stdin=b'pong', waiter=Sleepy(killed))
        sent = api.replies[M1]
        self.assertEqual(self.journal()['state'], 'resolved')
        self.assertNotIn('body', self.journal(), 'the answer text leaves the journal once sent')
        before = len(api.calls)
        data = self.recover(api)
        self.assertEqual(self.only(data), {'action': 'reply', 'message_id': M1, 'request_id':
                                                       self.journal_request_id(), 'resolved_before_crash': True,
                                                       'reply_message_id': sent})
        self.assertNotIn('session_reply', [call[0] for call in api.calls[before:]])
        self.assertEqual(len(self.replies(api)), 1)

    def journal_request_id(self):
        return 'native-reply:' + self.worker_ledger(self.slot).setting('session-agent')['agent_id'] + ':' + M1

    @isolated_profiles
    def test_a_receipt_killed_before_its_ack_is_replayed_once(self):
        api = KillApi(inbox=[message(M1)])
        self.offered(api)
        api.kill_before.add('session_ack')
        self.kill(api, 'received')
        self.assertEqual(api.acked, [])
        data = self.recover(api)
        self.assertEqual(self.only(data),
                         {'action': 'received', 'message_id': M1, 'resolved_before_crash': False})
        self.assertEqual((api.acked, data['state']), ([M1], 'timeout'), 'finished, not offered again')

    @isolated_profiles
    def test_a_receipt_killed_after_its_ack_is_reported_as_resolved(self):
        api = KillApi(inbox=[message(M1)])
        self.offered(api)
        api.hooks['session_ack'] = killed
        self.kill(api, 'received')
        data = self.recover(api)
        self.assertEqual(self.only(data),
                         {'action': 'received', 'message_id': M1, 'resolved_before_crash': True})
        self.assertEqual((api.acked, data['state']), ([M1], 'timeout'))

    @isolated_profiles
    def test_a_park_killed_before_its_ack_is_replayed_once(self):
        api = KillApi(inbox=[message(M1)])
        self.offered(api)
        api.kill_before.add('session_ack')
        self.kill(api, 'park')
        data = self.recover(api)
        self.assertEqual(self.only(data),
                         {'action': 'park', 'message_id': M1, 'resolved_before_crash': False})
        self.assertEqual((api.acked, data['awaiting_owner'], data['state']), ([M1], 1, 'timeout'))

    @isolated_profiles
    def test_a_park_killed_after_its_ack_is_reported_as_resolved(self):
        api = KillApi(inbox=[message(M1)])
        self.offered(api)
        api.hooks['session_ack'] = killed
        self.kill(api, 'park')
        data = self.recover(api)
        self.assertEqual(self.only(data),
                         {'action': 'park', 'message_id': M1, 'resolved_before_crash': True})
        self.assertEqual((api.acked, data['awaiting_owner']), ([M1], 1))

    @isolated_profiles
    def test_a_resolve_still_running_keeps_its_journal(self):
        import quorum_native_inbox as inbox
        api = KillApi(inbox=[message(M1, body='ping')])
        self.offered(api)
        api.kill_before.add('session_reply')
        self.kill(api, 'reply', stdin=b'pong')
        with inbox.resolve_lock(self.worker_ledger(self.slot)):
            self.assertNotIn('interrupted_resolve', self.recover(api))
        self.assertEqual(self.journal()['state'], 'pending')
        self.assertEqual(api.replies, {})
        self.assertFalse(self.only(self.recover(api))['resolved_before_crash'])

    @isolated_profiles
    def test_a_wait_start_also_recovers_and_its_line_says_so(self):
        api = KillApi(inbox=[message(M1, body='ping')])
        self.offered(api)
        api.kill_before.add('session_reply')
        self.kill(api, 'reply', stdin=b'pong')
        _, line = self.wait(api)
        self.assertEqual(self.only(line['data']),
                         {'action': 'reply', 'message_id': M1, 'resolved_before_crash': False,
                          'reply_message_id': api.replies[M1]})
        self.assertEqual(self.only(self.full()['data'])['request_id'], self.journal_request_id())
        self.assertEqual(len(self.replies(api)), 1)

    @isolated_profiles
    def test_a_refused_resolve_has_a_result_line_and_leaves_no_journal(self):
        api = KillApi(inbox=[message(M1, body='ping')])
        self.offered(api)
        api.fail['session_reply'] = ['service_unavailable']
        code, result, _ = self.cli(api, *self.template('reply', M1, '--max-seconds', '1'), stdin=b'pong')
        self.assertEqual((code, result['code']), (5, 'service_unavailable'))
        self.assertIsNone(self.journal())
        self.assertNotIn('interrupted_resolve', self.recover(api))

    @isolated_profiles
    def test_a_completed_resolve_leaves_no_journal(self):
        api = KillApi(inbox=[message(M1, body='ping')])
        self.offered(api)
        _, done = self.run_step(api, self.template('reply', M1, '--max-seconds', '1'), stdin=b'pong')
        self.assertNotIn('interrupted_resolve', done['data'])
        self.assertIsNone(self.journal())
        self.assertEqual(json.loads(self.stderr)['data']['resolved']['message_id'], M1)

    @isolated_profiles
    def test_a_recovering_wait_killed_too_keeps_its_report_for_the_next_receive(self):
        import quorum_native_inbox as inbox
        api = KillApi(inbox=[message(M1, body='ping')])
        self.offered(api)
        api.kill_before.add('session_reply')
        self.kill(api, 'reply', stdin=b'pong')
        # The plain wait recovers the reply, then is killed during its (possibly unbounded) wait.
        with patch('quorum_native_inbox.RealtimeWait', lambda *_, **__: Sleepy(killed)), self.assertRaises(Killed):
            self.cli(api, 'session', '--worker-capability', str(self.cap), 'wait', '--max-seconds', '0')
        sent = api.replies[M1]
        self.assertIsNone(self.journal(), 'the step itself is settled')
        self.assertEqual(len(self.worker_ledger(self.slot).setting(inbox.RESOLVE_REPORTS)), 1, 'its report is kept')
        report = self.only(self.recover(api))
        self.assertEqual((report['resolved_before_crash'], report['reply_message_id']), (False, sent),
                         'the outcome of the original kill, not of the recovery')
        self.assertEqual(len(self.replies(api)), 1)
        self.assertIsNone(self.worker_ledger(self.slot).setting(inbox.RESOLVE_REPORTS))
        self.assertNotIn('interrupted_resolve', self.recover(api))

    @isolated_profiles
    def test_a_deferred_recovery_refuses_the_next_resolve_and_never_overwrites_the_journal(self):
        api = KillApi(inbox=[message(M1, body='ping'), message(M2, body='second')])
        self.offered(api)
        api.kill_before.add('session_reply')
        self.kill(api, 'reply', stdin=b'pong')
        pending = self.journal()
        api.fail['session_delivery_status'] = ['service_unavailable']
        code, result, _ = self.cli(api, *self.template('received', M2, '--max-seconds', '1'))
        self.assertEqual((code, result['code']), (5, 'service_unavailable'), 'a retryable refusal')
        self.assertEqual(self.journal(), pending, 'the unsent reply text survives')
        self.assertNotIn(M2, api.acked)
        report = self.only(self.recover(api))
        self.assertEqual((report['message_id'], report['resolved_before_crash']), (M1, False))
        self.assertEqual([call[1]['body'] for call in self.replies(api)], ['pong'])

    @isolated_profiles
    def test_a_journal_is_insert_only(self):
        import quorum_native_inbox as inbox
        from quorum_api import QuorumError
        api = KillApi(inbox=[message(M1, body='ping')])
        self.offered(api)
        ledger = self.worker_ledger(self.slot)
        record = ledger.setting('session-agent')
        first = inbox.journal(ledger, record, 'reply', M1, 'pong', 'normal')
        with self.assertRaises(QuorumError):
            inbox.journal(ledger, record, 'received', M2)
        self.assertEqual(self.journal(), first)


if __name__ == '__main__':
    unittest.main()
