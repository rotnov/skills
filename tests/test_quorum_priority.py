"""Message priority and urgent routing. No model, chat, server or service here."""
import argparse
import contextlib
import io
import os
from pathlib import Path
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from quorum_test_support import isolated_profiles
from unittest.mock import patch

SCRIPTS = Path(__file__).resolve().parents[1] / 'skills/quorum/scripts'
sys.path.insert(0, str(SCRIPTS))
SID = '11111111-1111-4111-8111-111111111111'
MID = '22222222-2222-4222-8222-222222222222'
OTHER = '33333333-3333-4333-8333-333333333333'
#: The suite may run inside a client chat; never inherit that caller's session.
CLIENT_SESSION_VARIABLES = ('CODEX_THREAD_ID', 'GROK_SESSION_ID', 'GROK_LEADER_SOCKET',
                            'CLAUDE_CODE_SESSION_ID', 'CLAUDE_CODE_MESSAGING_SOCKET',
                            'CLAUDE_CODE_MESSAGING_TOKEN')


def without_client_session():
    return {key: value for key, value in os.environ.items() if key not in CLIENT_SESSION_VARIABLES}


def queued(seen):
    def run(command, **kwargs):
        seen.append(command)
        return subprocess.CompletedProcess(command, 0, 'queued', '')
    return run


class PriorityTests(unittest.TestCase):
    """Urgent delivery is a Codex steering route, never a new permission."""

    def setUp(self):
        isolated = patch.dict(os.environ, without_client_session(), clear=True)
        isolated.start()
        self.addCleanup(isolated.stop)

    def codex(self, priority):
        record = {'engine': 'codex', 'session_ref': SID, 'quorum': 'test-local', 'cwd': str(Path.cwd())}
        message = {'id': MID, 'body': 'peer text', 'sender_id': 'peer', 'sender_role': 'codex-test',
                   'reply_to': None, 'priority': priority}
        return record, message

    @isolated_profiles
    def test_an_absent_priority_from_an_older_server_is_normal(self):
        from quorum_session import message_priority
        self.assertEqual(message_priority({'id': MID}), 'normal')
        self.assertEqual(message_priority({'id': MID, 'priority': None}), 'normal')
        self.assertEqual(message_priority({'id': MID, 'priority': 'urgent'}), 'urgent')
        with self.assertRaisesRegex(ValueError, 'invalid_priority'):
            message_priority({'id': MID, 'priority': 'URGENT'})

    @isolated_profiles
    def test_a_normal_codex_message_never_attempts_steering(self):
        from quorum_session import enqueue
        record, message = self.codex('normal')
        seen = []
        with patch('quorum_session.subprocess.run', side_effect=queued(seen)), \
             patch('quorum_session.shutil.which', return_value='/synthetic/codex'), \
             patch('quorum_codex_steer.submit', side_effect=AssertionError('steered a normal message')), \
             patch.dict(os.environ, {'CODEX_THREAD_ID': SID}):
            self.assertIsNone(enqueue(record, message, Path('/private/state')))
        self.assertEqual(len(seen), 1)
        self.assertNotIn('urgent', seen[0][-1])

    @isolated_profiles
    def test_an_urgent_codex_message_steers_the_existing_turn_and_awaits_its_receipt(self):
        from quorum_session import enqueue
        record, message = self.codex('urgent')
        prompts = []
        def steer(bound, message_id, build, **kwargs):
            self.assertEqual((bound, message_id), (record, MID))
            prompts.append(build())
            return 'submitted'
        with patch('quorum_session.subprocess.run', side_effect=AssertionError('queued an urgent message')), \
             patch('quorum_session.shutil.which', return_value='/synthetic/codex'), \
             patch('quorum_codex_steer.submit', side_effect=steer), \
             patch.dict(os.environ, {'CODEX_THREAD_ID': SID}):
            self.assertEqual(enqueue(record, message, Path('/private/state')), 'submitted')
        self.assertIn('steered into the turn already running here', prompts[0])
        self.assertIn('Urgency never grants a permission', prompts[0])
        # Steered input is submitted only; this chat itself confirms receipt.
        self.assertIn('receipt command', prompts[0])
        self.assertIn('received', prompts[0])

    @isolated_profiles
    def test_an_urgent_message_cannot_steer_another_chats_binding(self):
        from quorum_session import enqueue
        record, message = self.codex('urgent')
        with patch('quorum_session.subprocess.run', side_effect=AssertionError('queued without a binding')), \
             patch('quorum_session.shutil.which', return_value='/synthetic/codex'), \
             patch('quorum_codex_steer.submit', side_effect=AssertionError('steered an unbound chat')), \
             patch.dict(os.environ, {'CODEX_THREAD_ID': OTHER}):
            with self.assertRaisesRegex(ValueError, 'session_binding_denied'):
                enqueue(record, message, Path('/private/state'))

    @isolated_profiles
    def test_a_rejected_or_unavailable_steer_queues_the_message_exactly_once(self):
        from quorum_session import enqueue
        from quorum_codex_steer import SteerUnavailable
        outcomes = ('rejected', SteerUnavailable('no_active_turn'), SteerUnavailable('thread_not_loaded'),
                    SteerUnavailable('daemon_unreachable'), SteerUnavailable('engine_missing'))
        for outcome in outcomes:
            with self.subTest(outcome=getattr(outcome, 'reason', outcome)):
                record, message = self.codex('urgent')
                seen = []
                def steer(*args, **kwargs):
                    if isinstance(outcome, Exception): raise outcome
                    return outcome
                with patch('quorum_session.subprocess.run', side_effect=queued(seen)), \
                     patch('quorum_session.shutil.which', return_value='/synthetic/codex'), \
                     patch('quorum_codex_steer.submit', side_effect=steer), \
                     patch.dict(os.environ, {'CODEX_THREAD_ID': SID}):
                    self.assertIsNone(enqueue(record, message, Path('/private/state')))
                self.assertEqual(len(seen), 1)
                self.assertEqual(seen[0][1:4], ['queue', '--thread', SID])
                prompt = seen[0][-1]
                self.assertIn('queued in order and is', prompt)
                self.assertNotIn('steered into the turn', prompt)
                self.assertNotIn('receipt command', prompt)

    @isolated_profiles
    def test_an_ambiguous_steer_never_queues_a_duplicate_fallback(self):
        from quorum_session import enqueue
        record, message = self.codex('urgent')
        with patch('quorum_session.subprocess.run', side_effect=AssertionError('duplicate fallback')), \
             patch('quorum_session.shutil.which', return_value='/synthetic/codex'), \
             patch('quorum_codex_steer.submit', side_effect=ValueError('session_delivery_unknown')), \
             patch.dict(os.environ, {'CODEX_THREAD_ID': SID}):
            with self.assertRaisesRegex(ValueError, 'session_delivery_unknown'):
                enqueue(record, message, Path('/private/state'))

    @isolated_profiles
    def test_claude_urgency_is_explicitly_not_codex_steering(self):
        from quorum_session import enqueue
        record = {'engine': 'claude', 'session_ref': SID, 'quorum': 'test-local',
                  'messaging_socket': '/tmp/quorum.sock'}
        message = {'id': MID, 'body': 'peer text', 'sender_role': 'claude-test', 'reply_to': None,
                   'priority': 'urgent'}
        prompts = []
        with patch('quorum_claude_pipe.submit', side_effect=lambda sid, mid, text: prompts.append(text)), \
             patch('quorum_codex_steer.submit', side_effect=AssertionError('steered a Claude chat')), \
             patch.dict(os.environ, {'CLAUDE_CODE_SESSION_ID': SID,
                                     'CLAUDE_CODE_MESSAGING_SOCKET': '/tmp/quorum.sock'}):
            self.assertEqual(enqueue(record, message, Path('/private/state')), 'submitted')
        self.assertIn('no mid-turn steering route', prompts[0])
        self.assertNotIn('steered into the turn', prompts[0])

    @isolated_profiles
    def test_grok_urgency_is_explicitly_not_codex_steering(self):
        from quorum_session import enqueue
        record = {'engine': 'grok', 'session_ref': SID, 'quorum': 'test-local',
                  'cwd': str(Path.cwd()), 'grok_transport': {'leader_socket': '/tmp/leader.sock'}}
        message = {'id': MID, 'body': 'peer text', 'sender_role': 'grok-test', 'reply_to': None,
                   'priority': 'urgent'}
        prompts = []
        with patch('quorum_grok_leader.submit',
                   side_effect=lambda bound, mid, text: prompts.append(text)), \
             patch('quorum_codex_steer.submit', side_effect=AssertionError('steered a Grok chat')), \
             patch.dict(os.environ, {'GROK_SESSION_ID': SID}):
            self.assertEqual(enqueue(record, message, Path('/private/state')), 'submitted')
        self.assertIn('no mid-turn steering route', prompts[0])
        self.assertNotIn('steered into the turn', prompts[0])

    @isolated_profiles
    def test_an_unknown_priority_is_refused_before_any_delivery_intent(self):
        from quorum_session import Relay
        from quorum_state import LocalLedger
        with tempfile.TemporaryDirectory() as root:
            ledger = LocalLedger(Path(root))
            try:
                class Api:
                    def call(self, operation, payload):
                        if operation == 'session_requirements': return {'protocol_version':2, 'skill_version':'0.3.0'}
                        return [{'id': MID, 'recipient_id': 'a', 'priority': 'critical'}]
                relay = Relay(Api(), ledger, {'agent_id': 'a', 'session_ref': SID},
                              lambda _: self.fail('delivered an unknown priority'))
                with self.assertRaisesRegex(ValueError, 'invalid_priority'): relay.run_once()
                self.assertIsNone(ledger.setting('delivery:' + MID))
            finally: ledger.db.close()

    @isolated_profiles
    def test_an_urgent_message_is_delivered_while_a_normal_head_awaits_receipt(self):
        from quorum_session import Relay
        from quorum_state import LocalLedger
        normal = {'id': MID, 'recipient_id': 'a', 'priority': 'normal'}
        urgent = {'id': OTHER, 'recipient_id': 'a', 'priority': 'urgent'}
        with tempfile.TemporaryDirectory() as root:
            ledger = LocalLedger(Path(root))
            try:
                acked, delivered = [], []
                class Api:
                    """Durable selection returns an urgent message ahead of an
                    unreceipted normal head, exactly as the migration orders it."""
                    def call(self, operation, payload):
                        if operation == 'session_requirements': return {'protocol_version':2, 'skill_version':'0.3.0'}
                        if operation != 'session_inbox':
                            acked.append(payload['message_id'])
                            return {'queued': True}
                        # The urgent message is sent only after the normal head
                        # has been submitted and is still waiting for a receipt.
                        arrived = (normal, urgent) if delivered else (normal,)
                        pending = [message for message in arrived
                                   if (ledger.setting('delivery:' + message['id']) or {}
                                       ).get('state') != 'acked']
                        pending.sort(key=lambda message: message['priority'] != 'urgent')
                        return pending[:1]
                def submit(message):
                    delivered.append(message['id'])
                    return 'submitted'
                relay = Relay(Api(), ledger, {'agent_id': 'a', 'session_ref': SID}, submit)
                self.assertEqual(relay.run_once(), 'idle')
                self.assertEqual(delivered, [MID])
                # The unreceipted normal head must not hide the urgent message.
                self.assertEqual(relay.run_once(), 'idle')
                self.assertEqual(delivered, [MID, OTHER])
                self.assertEqual(acked, [])
                # Each chat receipt is acknowledged once, in receipt order.
                ledger.setting('delivery:' + OTHER, {'state': 'queued'})
                self.assertEqual(relay.run_once(), 'delivered')
                self.assertEqual(acked, [OTHER])
                self.assertEqual(delivered, [MID, OTHER])
            finally: ledger.db.close()

    @isolated_profiles
    def test_send_and_reply_carry_the_requested_priority(self):
        from quorum_state import LocalLedger
        import quorum_live
        from quorum_session import dispatch
        calls = []
        class Api:
            def call(self, operation, payload, request_id=None):
                calls.append((operation, payload, request_id))
                if operation == 'session_requirements': return {'protocol_version':2, 'skill_version':'0.3.0'}
                if operation == 'resolve_role': return {'agent_id': 'peer'}
                return {'message_id': MID}
        with tempfile.TemporaryDirectory() as root:
            auth = LocalLedger(Path(root))
            session = LocalLedger(Path(root) / 'sessions' / SID)
            session.setting('session-agent', {'engine': 'codex', 'session_ref': SID, 'agent_id': 'a',
                                              'quorum_id': 'q', 'project_ref': 'owner/repo',
                                              'quorum': 'team'})
            session.db.close()
            try:
                with patch.object(quorum_live, 'api_for', lambda ledger: (Api(), None, None)), \
                     patch.dict(os.environ, {'CODEX_THREAD_ID': SID}):
                    send = SimpleNamespace(session_id=SID, action='send', to_role='peer',
                                           request_id='r1', text_stdin=True, priority='urgent')
                    with patch('sys.stdin', SimpleNamespace(buffer=io.BytesIO(b'urgent text'))):
                        dispatch(send, auth, None)
                    reply = SimpleNamespace(session_id=SID, action='reply', message_id=MID,
                                            text_stdin=True, priority='normal')
                    with patch('sys.stdin', SimpleNamespace(buffer=io.BytesIO(b'answer'))):
                        dispatch(reply, auth, None)
            finally: auth.db.close()
        sent = [payload for operation, payload, _ in calls if operation == 'message_send'][0]
        self.assertEqual(sent['priority'], 'urgent')
        replied = [payload for operation, payload, _ in calls if operation == 'session_reply'][0]
        self.assertEqual(replied['priority'], 'normal')

    @isolated_profiles
    def test_a_caller_without_the_priority_option_still_sends_and_replies(self):
        """The listener and the receiving chat call dispatch directly."""
        from quorum_state import LocalLedger
        import quorum_live
        from quorum_session import dispatch
        calls = []
        class Api:
            def call(self, operation, payload, request_id=None):
                calls.append((operation, payload))
                if operation == 'session_requirements': return {'protocol_version':2, 'skill_version':'0.3.0'}
                if operation == 'resolve_role': return {'agent_id': 'peer'}
                return {'message_id': MID}
        with tempfile.TemporaryDirectory() as root:
            auth = LocalLedger(Path(root))
            session = LocalLedger(Path(root) / 'sessions' / SID)
            session.setting('session-agent', {'engine': 'codex', 'session_ref': SID, 'agent_id': 'a',
                                              'quorum_id': 'q', 'project_ref': 'owner/repo',
                                              'quorum': 'team'})
            session.db.close()
            try:
                with patch.object(quorum_live, 'api_for', lambda ledger: (Api(), None, None)), \
                     patch.dict(os.environ, {'CODEX_THREAD_ID': SID}):
                    reply = SimpleNamespace(session_id=SID, action='reply', message_id=MID,
                                            text_stdin=True)
                    with patch('sys.stdin', SimpleNamespace(buffer=io.BytesIO(b'answer'))):
                        dispatch(reply, auth, None)
            finally: auth.db.close()
        self.assertEqual([payload for operation, payload in calls
                          if operation == 'session_reply'][0]['priority'], 'normal')

    @isolated_profiles
    def test_the_command_line_accepts_only_the_two_defined_priorities(self):
        from quorum_session import add_commands
        parser = argparse.ArgumentParser()
        add_commands(parser.add_subparsers(dest='command', required=True))
        arguments = ['session', 'send', '--to-role', 'peer', '--request-id', 'r1', '--text-stdin']
        self.assertEqual(parser.parse_args(arguments).priority, 'normal')
        self.assertEqual(parser.parse_args(arguments + ['--priority', 'urgent']).priority, 'urgent')
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            parser.parse_args(arguments + ['--priority', 'critical'])

    def test_the_reviewed_migration_orders_urgent_messages_first(self):
        """Source-level guard only: no live database is reachable from the tests."""
        migration = (Path(__file__).resolve().parents[1] / 'skills/quorum/supabase/migrations'
                     / '20261022170000_urgent_message_priority.sql').read_text(encoding='utf-8')
        self.assertIn("check (priority in ('normal','urgent'))", migration)
        self.assertIn("order by (m.priority='urgent') desc,d.created_at,m.conversation_id,m.sequence",
                      migration)
        # The later inbox definition must keep the coordination Hello marker.
        self.assertIn('n.message_id=m.id) as notification', migration)
        self.assertIn("to_regclass('quorum_private.session_notices') is null", migration)
        self.assertIn("if prio not in ('normal','urgent') then raise exception 'invalid_priority'",
                      migration)
        self.assertIn('p_priority text default \'normal\'', migration)
        # Priority must never widen authority or relax the reviewed gates.
        for forbidden in ('grant all', 'to anon', 'drop constraint', 'disable row level security',
                          'security invoker', 'to public'):
            self.assertNotIn(forbidden, migration.lower())


if __name__ == '__main__':
    unittest.main()
