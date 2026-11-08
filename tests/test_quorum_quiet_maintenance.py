"""Maintenance proposals coalesce while an owner decision is pending (#191).

The first proposal reaches the chat once; later ones are receipted locally with no envelope, model turn or reply,
and `session status` shows the newest target. The owner's refresh to it ends the pending state. Fake APIs,
temporary ledgers and local fixtures only; no model, provider or Quorum service.
"""
import hang_guard  # noqa: F401
from pathlib import Path
import json
import os
import sys
import tempfile
import unittest
from unittest.mock import patch
import uuid

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'skills/quorum/scripts'))
from quorum_test_support import isolated_profiles
import test_quorum_claude_worker as claude_base
import test_quorum_grok_cloud as cloud_base
import test_quorum_native_inbox as inbox_base
import test_quorum_session as session_base
import test_quorum_updates as updates_base

AGENT = inbox_base.AGENT
PEER = inbox_base.PEER


def digest(seed):
    return (seed * 64)[:64]


def proposal(sha, *, agent=AGENT, peer=PEER, mid=None, notice=None):
    """One typed maintenance notice as the authenticated inbox projects it."""
    return {'id': mid or str(uuid.uuid4()), 'recipient_id': agent, 'sender_id': peer, 'sender_role': 'coordinator',
            'conversation_id': str(uuid.uuid4()), 'priority': 'normal', 'reply_to': None, 'notification': False,
            'body': 'Quorum maintenance proposal. Receiving-owner authorization is required for canonical refresh; '
                    'no installation, lifecycle or task permission is granted. Target protocol 2, sha256 '
                    + sha + '.',
            'maintenance': {'kind': 'quorum.update', 'notice_id': notice or str(uuid.uuid4()),
                            'target': {'protocol_version': 2, 'content_sha256': sha}}}


class ClassifierTests(unittest.TestCase):
    def test_only_the_typed_field_addressed_here_is_a_proposal(self):
        from quorum_notices import maintenance
        good = proposal(digest('a'))
        self.assertEqual(maintenance(good, AGENT)['content_sha256'], digest('a'))
        refused = {
            'another recipient': {**good, 'recipient_id': PEER},
            'a reply': {**good, 'reply_to': str(uuid.uuid4())},
            'untyped body only': {key: value for key, value in good.items() if key != 'maintenance'},
            'other kind': {**good, 'maintenance': {**good['maintenance'], 'kind': 'other'}},
            'bad hash': {**good, 'maintenance': {**good['maintenance'], 'target': {'content_sha256': 'x'}}},
            'bad notice id': {**good, 'maintenance': {**good['maintenance'], 'notice_id': 'nope'}},
        }
        for label, message in refused.items():
            self.assertIsNone(maintenance(message, AGENT), label)

    def test_the_instruction_never_asks_for_a_reply(self):
        from quorum_delegation import instruction
        text = instruction({'agent_id': AGENT}, proposal(digest('a')))
        self.assertIn('Never reply to its sender', text)
        self.assertNotIn('Return owner_action_required', text)


class ParentRelayTests(unittest.TestCase):
    """Parent reception (Claude Code pipe, Codex queue, Grok TUI) shares the Relay."""
    def setUp(self):
        isolated = patch.dict(os.environ, session_base.without_client_session(), clear=True)
        isolated.start()
        self.addCleanup(isolated.stop)
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        from quorum_state import LocalLedger
        self.ledger = LocalLedger(Path(self.temporary.name))
        self.addCleanup(self.ledger.db.close)

    def relay(self, inbox, queued, calls):
        from quorum_session import Relay
        acked = []
        class Api:
            def call(self, operation, payload, request_id=None):
                calls.append(operation)
                if operation == 'session_requirements': return {'protocol_version': 2, 'skill_version': '0.3.0'}
                if operation == 'session_inbox': return [m for m in inbox if m['id'] not in acked]
                if operation == 'session_ack':
                    acked.append(payload['message_id'])
                    return {'queued': True}
                raise AssertionError(operation)
        def submit(message):
            queued.append(message['id'])
            return 'submitted'
        return Relay(Api(), self.ledger, {'agent_id': AGENT, 'session_ref': session_base.SID}, submit), acked

    def drain(self, relay, limit=10):
        for _ in range(limit):
            if relay.run_once() == 'idle': return
        self.fail('the relay never went idle')

    @isolated_profiles
    def test_first_reaches_the_chat_and_later_ones_stay_quiet_until_the_owner_acts(self):
        from quorum_notices import pending_update, settle_update
        first, second, third = proposal(digest('a')), proposal(digest('b')), proposal(digest('c'))
        inbox, queued, calls = [first], [], []
        relay, acked = self.relay(inbox, queued, calls)
        self.drain(relay)
        # The first proposal while none is pending reaches the chat once.
        self.assertEqual(queued, [first['id']])
        self.assertEqual(pending_update(self.ledger)['content_sha256'], digest('a'))
        # The chat's receipt of the first one; the next notices arrive while its decision is pending.
        self.ledger.setting('delivery:' + first['id'], {'state': 'acked'})
        acked.append(first['id'])
        inbox.extend([second, third])
        # The relay first counts the chat's confirmation of the first one, then receipts both notices itself.
        self.assertEqual([relay.run_once() for _ in range(4)], ['delivered', 'notice', 'notice', 'idle'])
        # Zero chat injections and zero replies; both receipted here.
        self.assertEqual(queued, [first['id']])
        self.assertEqual(acked[-2:], [second['id'], third['id']])
        self.assertNotIn('session_reply', calls)
        self.assertNotIn('message_send', calls)
        view = pending_update(self.ledger)
        self.assertEqual((view['content_sha256'], view['release'], view['quiet_notices']),
                         (digest('c'), digest('c')[:7], 2))
        self.assertEqual(view['notice_id'], third['maintenance']['notice_id'])
        self.assertNotIn('Quorum maintenance proposal', json.dumps(self.ledger.setting('pending-update')))
        # An older target does not end the pending state; the owner's refresh to the newest one does.
        settle_update(self.ledger, AGENT, digest('b'))
        self.assertEqual(pending_update(self.ledger)['content_sha256'], digest('c'))
        settle_update(self.ledger, AGENT, digest('c'))
        self.assertIsNone(pending_update(self.ledger))
        fourth = proposal(digest('d'))
        inbox.append(fourth)
        self.drain(relay)
        self.assertEqual(queued, [first['id'], fourth['id']])
        self.assertEqual(pending_update(self.ledger)['content_sha256'], digest('d'))

    @isolated_profiles
    def test_an_owner_refresh_between_the_quiet_decision_and_the_ack_never_loses_the_proposal(self):
        """The listener and the owner's update use separate ledger connections; settlement may land anywhere."""
        import quorum_notices
        from quorum_state import LocalLedger
        owner = LocalLedger(Path(self.temporary.name))
        self.addCleanup(owner.db.close)
        first, second = proposal(digest('a')), proposal(digest('b'))
        inbox, queued, calls = [first], [], []
        relay, acked = self.relay(inbox, queued, calls)
        self.drain(relay)
        self.ledger.setting('delivery:' + first['id'], {'state': 'acked'})
        acked.append(first['id'])
        inbox.append(second)
        original = quorum_notices.quiet
        def settle_after_decision(ledger, message, agent_id):
            decided = original(ledger, message, agent_id)
            # The owner's refresh to A commits on its own connection after B was judged quiet, before B's ACK.
            if message['id'] == second['id']: quorum_notices.settle_update(owner, AGENT, digest('a'))
            return decided
        with patch('quorum_notices.quiet', side_effect=settle_after_decision):
            self.assertEqual([relay.run_once() for _ in range(3)], ['delivered', 'notice', 'idle'])
        # B was acknowledged without an envelope, so it must stay visible as the newest pending target.
        self.assertEqual(acked[-1], second['id'])
        self.assertEqual(queued, [first['id']])
        self.assertEqual(quorum_notices.pending_update(owner)['content_sha256'], digest('b'))
        # Settled the other way round, the next proposal finds nothing pending and reaches the chat once.
        quorum_notices.settle_update(owner, AGENT, digest('b'))
        third = proposal(digest('c'))
        inbox.append(third)
        self.drain(relay)
        self.assertEqual(queued, [first['id'], third['id']])

    @isolated_profiles
    def test_a_lost_receipt_offers_the_surfaced_proposal_again(self):
        first = proposal(digest('a'))
        queued, calls = [], []
        relay, acked = self.relay([first], queued, calls)
        self.drain(relay)
        self.assertEqual((queued, acked), ([first['id']], []))
        # Still the head and still unreceipted: the same proposal is never swallowed as quiet.
        from quorum_notices import quiet
        self.assertIsNone(quiet(self.ledger, first, AGENT))

    @isolated_profiles
    def test_the_parent_envelope_has_no_reply_command(self):
        import quorum_session
        captured = []
        record = {'agent_id': AGENT, 'session_ref': session_base.SID, 'engine': 'claude', 'quorum': 'team'}
        with patch('quorum_session.check_native_route'), \
                patch('quorum_session.contracts.check_claude_session'), \
                patch('quorum_claude_pipe.submit', side_effect=lambda sid, mid, prompt: captured.append(prompt)):
            quorum_session.enqueue(record, proposal(digest('a')), Path(self.temporary.name))
        self.assertEqual(len(captured), 1)
        self.assertNotIn('Reply command', captured[0])
        self.assertIn('Do not answer it', captured[0])


class OwnerActionTests(unittest.TestCase):
    """The receiving owner's explicit refresh to the pending target ends the pending state."""
    setUp = updates_base.OwnerUpdateTests.setUp
    refresh = updates_base.OwnerUpdateTests.refresh
    notice = updates_base.OwnerUpdateTests.notice

    def test_apply_notice_to_the_newest_target_settles_it(self):
        from quorum_notices import pending_update, quiet, record
        from quorum_update import validate_source
        self.notice()
        newest = validate_source(self.source)
        first = proposal(digest('a'), agent=updates_base.AID)
        later = proposal(newest, agent=updates_base.AID, notice=updates_base.NOTICE)
        self.assertIsNone(quiet(self.ledger, first, updates_base.AID))
        self.assertEqual(quiet(self.ledger, later, updates_base.AID), 'maintenance')
        record(self.ledger, later, 'maintenance', 'parent')
        self.assertEqual(pending_update(self.ledger)['content_sha256'], newest)
        # Without the owner's consent nothing is decided.
        self.module.apply_notice(self.api, self.ledger, self.record, updates_base.NOTICE, 'apply-1',
                                 owner_refresh=False, current=self.source, cache=self.root/'cache',
                                 refresh=self.refresh)
        self.assertEqual(pending_update(self.ledger)['content_sha256'], newest)
        result = self.module.apply_notice(self.api, self.ledger, self.record, updates_base.NOTICE, 'apply-1',
                                          owner_refresh=True, current=self.source, cache=self.root/'cache',
                                          refresh=self.refresh)
        self.assertEqual(result['state'], 'refreshed')
        self.assertIsNone(pending_update(self.ledger))
        # The next new target reaches the chat once again.
        self.assertIsNone(quiet(self.ledger, proposal(digest('e'), agent=updates_base.AID), updates_base.AID))


@inbox_base.OWNER_PROOF
class ToolInboxTests(inbox_base.Harness):
    """A Codex tool-inbox worker's `receive` coalesces proposals and refuses a reply to one."""
    def setUp(self):
        super().setUp()
        inbox_base.rollout(self.home)

    @isolated_profiles
    def test_receive_offers_the_first_and_receipts_later_ones_quietly(self):
        first, second, third = proposal(digest('a')), proposal(digest('b')), proposal(digest('c'))
        api = inbox_base.Api(inbox=[first])
        with inbox_base.caller(self.home):
            self.connect(api)
            _, envelope, _ = self.receive(api)
            self.assertEqual(envelope['data']['message_id'], first['id'])
            code, result, _ = self.cli(api, 'session', 'reply', first['id'], '--text-stdin', stdin=b'owner_action')
            self.assertEqual((code, result['code']), (3, 'native_inbox_reply_loop_denied'))
            self.assertEqual(self.cli(api, 'session', 'received', first['id'])[0], 0)
            api.inbox.extend([second, third])
            _, waited, _ = self.receive(api)
            self.assertEqual(waited['data']['state'], 'timeout')
            _, status, _ = self.cli(api, 'session', 'status')
        self.assertEqual(api.acked, [first['id'], second['id'], third['id']])
        self.assertNotIn('session_reply', api.ops())
        pending = status['data']['pending_update']
        self.assertEqual((pending['content_sha256'], pending['quiet_notices']), (digest('c'), 2))


class ClaudeWorkerTests(claude_base.Bound):
    """A Claude worker's `receive` and `wait` share the offer, so both coalesce."""
    @isolated_profiles
    def test_worker_offers_the_first_and_receipts_later_ones_quietly(self):
        from quorum_notices import pending_update
        agent, peer = claude_base.AGENT, claude_base.PEER
        first, second = proposal(digest('a'), agent=agent, peer=peer), proposal(digest('b'), agent=agent, peer=peer)
        api = claude_base.Api(inbox=[first])
        self.bound(api)
        self.assertEqual(self.offer(api)['message_id'], first['id'])
        code, result, _ = self.worker(api, 'reply', first['id'], '--text-stdin', stdin=b'answer')
        self.assertEqual((code, result['code']), (3, 'native_inbox_reply_loop_denied'))
        self.assertEqual(self.worker(api, 'received', first['id'])[0], 0)
        api.inbox.append(second)
        self.assertEqual(self.offer(api)['state'], 'timeout')
        self.assertEqual(api.acked, [first['id'], second['id']])
        self.assertEqual(pending_update(self.worker_ledger(self.slot))['content_sha256'], digest('b'))


class CloudTests(cloud_base.Harness):
    """A Grok Bot cloud parent's receive coalesces proposals and refuses a reply to one."""
    def setUp(self):
        super().setUp()
        sys.modules.pop('quorum_grok_webhook', None)
        cloud_base.Connection.posts, cloud_base.Connection.mode = [], 'accepted'

    def connect(self, api):
        values = json.dumps({'url': cloud_base.SECRET_URL, 'key': cloud_base.SECRET_KEY}).encode()
        self.assertEqual(self.cli(api, 'cloud', 'configure', '--json-stdin', stdin=values)[0], 0)
        super().connect(api)

    @isolated_profiles
    def test_parent_receive_offers_the_first_and_receipts_later_ones_quietly(self):
        agent, peer = cloud_base.AGENT, cloud_base.PEER
        first, second = proposal(digest('a'), agent=agent, peer=peer), proposal(digest('b'), agent=agent, peer=peer)
        api = cloud_base.Api(inbox=[first])
        with cloud_base.context():
            self.connect(api)
            _, envelope, _ = self.cli(api, 'cloud', 'receive')
            self.assertEqual(envelope['data']['message_id'], first['id'])
            code, result, _ = self.cli(api, 'cloud', 'reply', first['id'], '--text-stdin', stdin=b'answer')
            self.assertEqual(result['code'], 'reply_loop_denied')
            self.assertNotEqual(code, 0)
            self.assertEqual(self.cli(api, 'cloud', 'received', first['id'])[0], 0)
            api.inbox.append(second)
            _, quiet, _ = self.cli(api, 'cloud', 'receive')
            self.assertIsNone(quiet['data']['message'])
            _, status, _ = self.cli(api, 'cloud', 'status')
        self.assertEqual(api.acked, [first['id'], second['id']])
        self.assertEqual(status['data']['pending_update']['content_sha256'], digest('b'))


if __name__ == '__main__':
    unittest.main()
