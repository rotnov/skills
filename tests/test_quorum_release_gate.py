"""Mandatory urgent-priority release gate and Codex reply receipts.

No model, chat, server or service: fake RPC backends only. The disposable SQL
suite separately proves the real coordination-only and upgraded contracts.
"""
import io
import json
import os
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch
from quorum_test_support import isolated_profiles

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / 'skills/quorum/scripts'
MIGRATIONS = ROOT / 'skills/quorum/supabase/migrations'
sys.path.insert(0, str(SCRIPTS))
SID = '11111111-1111-4111-8111-111111111111'
MID = '22222222-2222-4222-8222-222222222222'
NEXT = '33333333-3333-4333-8333-333333333333'
CURRENT = {'protocol_version': 2, 'skill_version': '0.3.0'}
COORDINATION_ONLY = {'protocol_version': 1, 'skill_version': '0.2.0'}
CLIENT_SESSION_VARIABLES = ('CODEX_THREAD_ID', 'GROK_SESSION_ID', 'GROK_LEADER_SOCKET',
                            'CLAUDE_CODE_SESSION_ID', 'CLAUDE_CODE_MESSAGING_SOCKET',
                            'CLAUDE_CODE_MESSAGING_TOKEN')
RECORD = {'engine': 'codex', 'session_ref': SID, 'agent_id': 'a', 'quorum_id': 'q',
          'project_ref': 'owner/repo', 'quorum': 'team'}


class Backend:
    def __init__(self, required):
        self.required, self.calls = required, []

    def call(self, operation, payload, request_id=None):
        self.calls.append(operation)
        if operation == 'session_requirements': return dict(self.required)
        if operation == 'resolve_role': return {'agent_id': 'peer'}
        if operation == 'session_ack': return {'queued': True}
        return {'message_id': MID}


class Gate(unittest.TestCase):
    def setUp(self):
        isolated = patch.dict(os.environ, {key: value for key, value in os.environ.items()
                                           if key not in CLIENT_SESSION_VARIABLES}, clear=True)
        isolated.start()
        self.addCleanup(isolated.stop)
        root = tempfile.TemporaryDirectory()
        self.addCleanup(root.cleanup)
        self.root = Path(root.name)
        from quorum_state import LocalLedger
        session = LocalLedger(self.root / 'sessions' / SID)
        session.setting('session-agent', RECORD)
        session.db.close()

    def session(self):
        from quorum_state import LocalLedger
        ledger = LocalLedger(self.root / 'sessions' / SID)
        self.addCleanup(ledger.db.close)
        return ledger

    def run_action(self, backend, action, priority='normal', message_id=MID, caller=SID):
        import quorum_live
        from quorum_session import dispatch
        from quorum_state import LocalLedger
        if action == 'send':
            args = SimpleNamespace(session_id=SID, action='send', to_role='peer', request_id='r1',
                                   text_stdin=True, priority=priority)
        else:
            args = SimpleNamespace(session_id=SID, action='reply', message_id=message_id,
                                   text_stdin=True, priority=priority)
        auth = LocalLedger(self.root)
        try:
            with patch.object(quorum_live, 'api_for', lambda ledger: (backend, None, None)), \
                 patch.dict(os.environ, {'CODEX_THREAD_ID': caller}), \
                 patch('sys.stdin', SimpleNamespace(buffer=io.BytesIO(b'answer'))):
                return dispatch(args, auth, None)
        finally: auth.db.close()


def installed():
    from quorum_release import installed_release
    return installed_release()


class MandatoryReleaseTests(Gate):
    def test_the_next_release_is_skill_0_3_0_protocol_2(self):
        manifest = json.loads((ROOT / 'skills/quorum/assets/release.json').read_text(encoding='utf-8'))
        self.assertEqual(manifest, CURRENT)
        self.assertIn('version = "0.3.0"', (ROOT / 'skills/quorum/pyproject.toml').read_text(encoding='utf-8'))
        # Owner-facing guidance and the invitation grant name the same exact tuple.
        for name in ('README.md', 'references/update.md', 'references/sessions.md'):
            text = (ROOT / 'skills/quorum' / name).read_text(encoding='utf-8')
            self.assertIn('0.3.0', text, name)
            self.assertNotIn('0.2.0', text, name)
        # Invitation grants derive the tuple from the installed release manifest.
        from quorum_invitations import completion
        self.assertIn('required protocol 2 and skill 0.3.0 plus running status', completion('coordinator'))
        source = (ROOT / 'skills/quorum/scripts/quorum_invitations.py').read_text(encoding='utf-8')
        self.assertNotIn('0.2.0', source)

    @isolated_profiles
    def test_a_coordination_only_server_is_refused_before_any_send_or_reply_write(self):
        from quorum_release import ReleaseMismatch
        for action in ('send', 'reply'):
            for priority in ('normal', 'urgent'):
                with self.subTest(action=action, priority=priority):
                    backend = Backend(COORDINATION_ONLY)
                    with self.assertRaisesRegex(ReleaseMismatch, 'skill_update_required') as caught:
                        self.run_action(backend, action, priority)
                    self.assertEqual(backend.calls, ['session_requirements'])
                    self.assertEqual(caught.exception.diagnostic['required'], COORDINATION_ONLY)

    @isolated_profiles
    def test_an_upgraded_server_handles_normal_and_urgent_send_and_reply(self):
        for action, write in (('send', 'message_send'), ('reply', 'session_reply')):
            for priority in ('normal', 'urgent'):
                with self.subTest(action=action, priority=priority):
                    backend = Backend(CURRENT)
                    self.assertEqual(self.run_action(backend, action, priority)['message_id'], MID)
                    self.assertEqual(backend.calls[0], 'session_requirements')
                    self.assertEqual(backend.calls.count(write), 1)

    def test_only_the_urgent_migration_advances_the_server_requirement(self):
        coordination = (MIGRATIONS / '20261022152603_native_session_coordination.sql').read_text(encoding='utf-8')
        urgent = (MIGRATIONS / '20261022170000_urgent_message_priority.sql').read_text(encoding='utf-8')
        # The historical bootstrap tuple stays as it was deployed.
        self.assertIn("jsonb_build_object('protocol_version',1,'skill_version','0.2.0')", coordination)
        # The urgent migration upgrades only that reviewed tuple, last, in its own transaction.
        self.assertIn("raise exception 'reviewed_release_changed'", urgent)
        requirement = urgent.split('create or replace function public.session_requirements()', 1)[1]
        self.assertIn("jsonb_build_object('protocol_version',2,'skill_version','0.3.0')", requirement)
        self.assertNotIn('create ', requirement.split('end $function$;', 1)[1])
        # No old normal signature survives next to the priority entry points.
        self.assertIn('drop function public.message_send(uuid,uuid,uuid,uuid,uuid,text,text);', urgent)
        self.assertIn('drop function public.session_reply(uuid,text,uuid,text);', urgent)


class CodexReplyReceiptTests(Gate):
    @isolated_profiles
    def test_a_bound_codex_reply_confirms_a_steered_message_and_the_relay_moves_on(self):
        from quorum_session import Relay
        ledger = self.session()
        inbox = [{'id': MID, 'recipient_id': 'a', 'priority': 'urgent'},
                 {'id': NEXT, 'recipient_id': 'a', 'priority': 'normal'}]
        acks, delivered = [], []
        class Inbox(Backend):
            """Server receipts remove a message from the durable inbox."""
            def call(self, operation, payload, request_id=None):
                if operation == 'session_inbox':
                    self.calls.append(operation)
                    return [message for message in inbox if message['id'] not in acks][:1]
                if operation == 'session_ack': acks.append(payload['message_id'])
                return super().call(operation, payload, request_id)
        backend = Inbox(installed())
        def deliver(message):
            delivered.append(message['id'])
            return 'submitted' if message['priority'] == 'urgent' else None
        relay = Relay(backend, ledger, RECORD, deliver)
        self.assertEqual(relay.run_once(), 'idle')
        self.assertEqual(ledger.setting('delivery:' + MID), {'state': 'submitted'})
        before = len(backend.calls)
        # The steered chat answers directly; it never runs a separate `received`.
        self.run_action(backend, 'reply', 'normal')
        self.assertEqual(backend.calls[before:], ['session_requirements', 'session_reply',
                                                  'session_requirements', 'session_ack'])
        self.assertEqual(acks, [MID])
        self.assertEqual(ledger.setting('delivery:' + MID), {'state': 'acked'})
        self.assertEqual(relay.run_once(), 'delivered')
        # The next inbox message is delivered and acknowledged normally.
        self.assertEqual(relay.run_once(), 'delivered')
        self.assertEqual(delivered, [MID, NEXT])
        self.assertEqual(acks, [MID, NEXT])
        # A repeated reply is idempotent and claims no second receipt.
        self.run_action(backend, 'reply', 'normal')
        self.assertEqual(acks, [MID, NEXT])

    @isolated_profiles
    def test_a_codex_reply_cannot_claim_a_receipt_for_an_unsubmitted_message(self):
        backend = Backend(installed())
        self.run_action(backend, 'reply', 'normal', message_id=NEXT)
        self.assertNotIn('session_ack', backend.calls)
        self.assertIsNone(self.session().setting('delivery:' + NEXT))

    @isolated_profiles
    def test_a_normal_codex_receipt_already_acknowledged_is_not_repeated(self):
        self.session().setting('delivery:' + MID, {'state': 'acked'})
        backend = Backend(installed())
        self.run_action(backend, 'reply', 'normal')
        self.assertNotIn('session_ack', backend.calls)
        self.assertEqual(self.session().setting('delivery:' + MID), {'state': 'acked'})

    @isolated_profiles
    def test_another_chat_cannot_confirm_this_bindings_steered_message(self):
        self.session().setting('delivery:' + MID, {'state': 'submitted'})
        backend = Backend(installed())
        with self.assertRaisesRegex(ValueError, 'session_binding_denied'):
            self.run_action(backend, 'reply', 'normal', caller=NEXT)
        self.assertEqual(backend.calls, [])
        self.assertEqual(self.session().setting('delivery:' + MID), {'state': 'submitted'})


if __name__ == '__main__':
    unittest.main()
