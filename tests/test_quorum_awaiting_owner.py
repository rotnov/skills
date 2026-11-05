"""Messages awaiting the owner's decision never stall a worker inbox (#113).

Synthetic rollouts, capability fixtures and a fake API only; no model, provider or Quorum service.
"""
import contextlib
import io
import json
import unittest
from unittest.mock import patch

from quorum_test_support import isolated_profiles
import test_quorum_claude_worker as claude_base
import test_quorum_native_inbox as base

M1, M2, M3 = base.M1, base.M2, base.M3


class Clock:
    """A settable wall clock for persisted offer windows and holds."""
    def __init__(self, now=1_800_000_000.0): self.now = now
    def __call__(self): return self.now


@base.OWNER_PROOF
class CodexAwaitingOwnerTests(base.Harness):
    def setUp(self):
        super().setUp()
        base.rollout(self.home)
        self.clock = Clock()
        guard = patch('quorum_native_inbox.wall', self.clock)
        guard.start()
        self.addCleanup(guard.stop)

    def run_cli(self, api, *argv, stdin=b''):
        with base.caller(self.home):
            return self.cli(api, 'session', *argv, stdin=stdin)

    def offered(self, api, expected):
        code, result, text = self.run_cli(api, 'receive', '--wait-seconds', '0')
        self.assertEqual(code, 0, text)
        self.assertEqual(result['data']['message_id'], expected, text)
        return result['data']

    def start(self, api):
        with base.caller(self.home): self.connect(api)

    @isolated_profiles
    def test_park_receipts_now_and_reception_and_heartbeat_continue(self):
        api = base.Api(inbox=[base.message(M1, body='may I publish this?'), base.message(M2, body='later task')])
        self.start(api)
        envelope = self.offered(api, M1)
        self.assertEqual(envelope['park_command'][-5:], ['session', '--session-id', base.CHILD, 'park', M1])
        self.assertEqual(envelope['awaiting_owner'], 0)
        self.assertIn('park command', envelope['notice'])
        self.assertIn('Never stop receiving', envelope['notice'])
        code, parked, text = self.run_cli(api, 'park', M1)
        self.assertEqual((code, parked['data']), (0, {'parked': True, 'state': 'parked', 'message_id': M1,
                                                      'awaiting_owner': 1}), text)
        # Consumed at the service, so the inbox head and the peer receipt timeout move on.
        self.assertEqual(api.acked, [M1])
        heartbeats = api.ops().count('session_heartbeat')
        later = self.offered(api, M2)
        self.assertEqual(later['awaiting_owner'], 1)
        self.assertGreater(api.ops().count('session_heartbeat'), heartbeats, 'presence continues while parked')
        self.assertEqual(self.ledger().setting('delivery:' + M1)['state'], 'parked')
        code, status, _ = self.run_cli(api, 'status')
        self.assertEqual((status['data']['awaiting_owner'], status['data']['pending_receipts']), (1, 1))
        self.assertNotIn('reception_hold', status['data'])
        # The parent sees the count by exact worker UUID, metadata only.
        with base.caller(self.home, sid=base.PARENT):
            code, supervised, _ = self.cli(api, 'session', '--session-id', base.CHILD, 'status')
        self.assertEqual((code, supervised['data']['awaiting_owner']), (0, 1))
        code, listing, _ = self.run_cli(api, 'awaiting')
        self.assertEqual(code, 0)
        self.assertEqual(listing['data']['awaiting_owner'], 1)
        item = listing['data']['items'][0]
        self.assertEqual((item['message_id'], item['reason'], item['terminal'], item['answer_pending'],
                          item['message']['body']), (M1, 'owner_decision', False, False, 'may I publish this?'))
        self.assertEqual(self.run_cli(api, 'awaiting', 'list')[1]['data'], listing['data'])

    @isolated_profiles
    def test_owner_decision_is_one_reply_to_the_parked_message(self):
        api = base.Api(inbox=[base.message(M1), base.message(M2)])
        self.start(api)
        self.offered(api, M1)
        self.run_cli(api, 'park', M1)
        self.offered(api, M2)
        api.fail['session_reply'] = ['service_unavailable']
        code, failed, _ = self.run_cli(api, 'awaiting', 'resolve', M1, '--text-stdin', stdin=b'yes, publish')
        self.assertEqual((code, failed['code']), (5, 'service_unavailable'))
        # Still listed with its persisted answer intent; the identical retry completes it.
        item = self.run_cli(api, 'awaiting')[1]['data']['items'][0]
        self.assertEqual((item['message_id'], item['answer_pending']), (M1, True))
        code, resolved, text = self.run_cli(api, 'awaiting', 'resolve', M1, '--text-stdin', stdin=b'yes, publish')
        self.assertEqual(code, 0, text)
        self.assertEqual((resolved['data']['resolved'], resolved['data']['message_id'],
                          resolved['data']['awaiting_owner']), (True, M1, 0))
        replies = [payload for operation, payload, _ in api.calls if operation == 'session_reply']
        self.assertEqual(replies[-1], {'agent_id': base.AGENT, 'session_ref': base.CHILD, 'message_id': M1,
                                       'body': 'yes, publish', 'priority': 'normal'})
        self.assertEqual(self.ledger().setting('delivery:' + M1)['state'], 'acked')
        self.assertEqual(self.run_cli(api, 'awaiting')[1]['data'], {'awaiting_owner': 0, 'items': []})
        code, again, _ = self.run_cli(api, 'awaiting', 'resolve', M1, '--text-stdin', stdin=b'changed')
        self.assertEqual((code, again['code']), (3, 'native_inbox_not_parked'))
        code, loop, _ = self.run_cli(api, 'reply', M1, '--text-stdin', stdin=b'changed')
        self.assertEqual((code, loop['code']), (3, 'native_inbox_reply_loop_denied'))
        # The unrelated current offer was never receipted by the resolution.
        self.assertEqual(self.ledger().setting('delivery:' + M2)['state'], 'offered')

    @isolated_profiles
    def test_dismiss_closes_the_entry_without_an_answer(self):
        api = base.Api(inbox=[base.message(M1)])
        self.start(api)
        self.offered(api, M1)
        code, result, _ = self.run_cli(api, 'awaiting', 'dismiss', M1)
        self.assertEqual((code, result['code']), (3, 'native_inbox_not_parked'), 'only a parked entry is dismissed')
        self.run_cli(api, 'park', M1)
        code, result, text = self.run_cli(api, 'awaiting', 'dismiss', M1)
        self.assertEqual((code, result['data']), (0, {'dismissed': True, 'state': 'acked', 'message_id': M1,
                                                      'awaiting_owner': 0}), text)
        code, loop, _ = self.run_cli(api, 'reply', M1, '--text-stdin', stdin=b'late')
        self.assertEqual((code, loop['code']), (3, 'native_inbox_reply_loop_denied'))
        self.assertNotIn('session_reply', api.ops())

    @isolated_profiles
    def test_lost_park_receipt_is_replayed_and_the_message_is_never_offered_again(self):
        api = base.Api(inbox=[base.message(M1), base.message(M2)])
        self.start(api)
        self.offered(api, M1)
        api.lost.append('session_ack')
        code, result, _ = self.run_cli(api, 'park', M1)
        self.assertEqual((code, result['code']), (5, 'service_unavailable'))
        self.assertEqual(self.ledger().setting('delivery:' + M1)['state'], 'parked')
        # The service applied it here; when it did not, the next receive repeats the idempotent ACK.
        api.acked.remove(M1)
        code, result, text = self.run_cli(api, 'receive', '--wait-seconds', '0')
        self.assertEqual((code, result['data']['state'], result['data']['awaiting_owner']), (0, 'timeout', 1), text)
        self.assertEqual(api.acked, [M1])
        self.offered(api, M2)
        acks = [payload['message_id'] for operation, payload, _ in api.calls if operation == 'session_ack']
        self.assertEqual(acks, [M1, M1])

    @isolated_profiles
    def test_unreceipted_offer_is_parked_when_its_window_ends(self):
        import quorum_native_inbox as inbox
        api = base.Api(inbox=[base.message(M1), base.message(M2)])
        self.start(api)
        first = self.offered(api, M1)
        self.assertEqual(first['park_after'], int(self.clock.now + inbox.OFFER_WINDOW))
        self.clock.now += inbox.OFFER_WINDOW - 1
        self.offered(api, M1)
        self.clock.now += 1
        later = self.offered(api, M2)
        self.assertEqual(later['awaiting_owner'], 1)
        parked = self.ledger().setting('delivery:' + M1)
        self.assertEqual((parked['state'], parked['park_reason']), ('parked', 'offer_window_elapsed'))
        self.assertEqual(api.acked, [M1])

    @isolated_profiles
    def test_hold_is_explicit_time_limited_and_reported(self):
        api = base.Api(inbox=[base.message(M1, priority='urgent'), base.message(M2)])
        self.start(api)
        self.offered(api, M1)
        code, held, text = self.run_cli(api, 'hold', M1, '--seconds', '1200')
        self.assertEqual(code, 0, text)
        until = int(self.clock.now + 1200)
        self.assertEqual(held['data']['reception_hold'], {'message_id': M1, 'until': until, 'reason': 'explicit_hold'})
        self.assertIn('Report this hold', held['data']['next_step'])
        status = self.run_cli(api, 'status')[1]['data']
        self.assertEqual(status['reception_hold'], {'message_id': M1, 'until': until, 'reason': 'explicit_hold'})
        # Beyond the default window, the explicit hold keeps this one offer in front.
        import quorum_native_inbox as inbox
        self.clock.now += inbox.OFFER_WINDOW + 1
        self.assertEqual(self.offered(api, M1)['park_after'], until)
        self.clock.now = until
        self.offered(api, M2)
        self.assertNotIn('reception_hold', self.run_cli(api, 'status')[1]['data'])
        self.assertEqual(self.ledger().setting('delivery:' + M1)['park_reason'], 'offer_window_elapsed')
        with base.caller(self.home), self.assertRaises(SystemExit), \
                contextlib.redirect_stderr(io.StringIO()):
            self.cli(api, 'session', 'hold', M2, '--seconds', str(inbox.HOLD_LIMIT + 1))

    @isolated_profiles
    def test_full_list_refuses_without_receipt_and_only_the_worker_parks(self):
        api = base.Api(inbox=[base.message(M1), base.message(M2)])
        self.start(api)
        self.offered(api, M1)
        with base.caller(self.home, sid=base.PARENT):
            code, result, _ = self.cli(api, 'session', '--session-id', base.CHILD, 'park', M1)
        self.assertEqual((code, result['code']), (3, 'session_binding_denied'))
        with patch('quorum_native_inbox.AWAITING_LIMIT', 1):
            self.run_cli(api, 'park', M1)
            self.offered(api, M2)
            code, result, _ = self.run_cli(api, 'park', M2)
            self.assertEqual((code, result['code']), (3, 'native_inbox_awaiting_full'))
            self.assertIn('resolve or dismiss', result['data']['next_step'])
        self.assertEqual(self.ledger().setting('delivery:' + M2)['state'], 'offered')
        self.assertEqual(api.acked, [M1])

    @isolated_profiles
    def test_revoked_membership_discloses_no_parked_message(self):
        api = base.Api(inbox=[base.message(M1, body='private parked body')])
        self.start(api)
        self.offered(api, M1)
        self.run_cli(api, 'park', M1)
        api.revoked = True
        for argv in (('awaiting',), ('awaiting', 'dismiss', M1), ('awaiting', 'resolve', M1, '--text-stdin')):
            code, result, text = self.run_cli(api, *argv, stdin=b'answer')
            self.assertEqual((code, result['ok']), (3, False), text)
            self.assertNotIn('private parked body', text)
        self.assertEqual(self.ledger().setting('delivery:' + M1)['state'], 'parked')


@claude_base.OWNER_PROOF
class ClaudeAwaitingOwnerTests(claude_base.Bound):
    @isolated_profiles
    def test_claude_worker_parks_and_resolves_with_its_existing_scope(self):
        api = claude_base.Api(inbox=[claude_base.message(M1, body='decide please'), claude_base.message(M2)])
        self.bound(api)
        envelope = self.offer(api)
        self.assertEqual(envelope['park_command'][-5:], ['session', '--worker-capability', str(self.cap), 'park', M1])
        code, parked, text = self.worker(api, 'park', M1)
        self.assertEqual((code, parked['data']['awaiting_owner']), (0, 1), text)
        self.assertEqual(self.offer(api)['message_id'], M2)
        code, listing, _ = self.worker(api, 'awaiting')
        self.assertEqual([item['message_id'] for item in listing['data']['items']], [M1])
        self.assertNotIn(self.token, json.dumps(listing))
        code, resolved, text = self.worker(api, 'awaiting', 'resolve', M1, '--text-stdin', stdin=b'owner says no')
        self.assertEqual((code, resolved['data']['resolved']), (0, True), text)
        self.assertEqual([payload['message_id'] for operation, payload, _ in api.calls if operation == 'session_reply'],
                         [M1])

    @isolated_profiles
    def test_claude_resolve_needs_the_scope_reply_operation(self):
        api = claude_base.Api(inbox=[claude_base.message(M1)])
        scope = self.accept_scope(operations=['accept', 'connect', 'receive', 'received', 'status', 'stop'])
        self.bound(api, scope)
        self.offer(api)
        self.assertEqual(self.worker(api, 'park', M1)[0], 0)
        code, result, _ = self.worker(api, 'awaiting', 'resolve', M1, '--text-stdin', stdin=b'answer')
        self.assertEqual((code, result['code'], result['data'].get('reason')),
                         (3, 'native_worker_binding_denied', 'operation_denied'))
        self.assertEqual(self.worker(api, 'awaiting', 'dismiss', M1)[0], 0)
        self.assertNotIn('session_reply', api.ops())


if __name__ == '__main__':
    unittest.main()
