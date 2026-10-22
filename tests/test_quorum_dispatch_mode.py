"""Native worker policy is local owner consent, never a model or session launch."""
import argparse
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'skills/quorum/scripts'))
from quorum_test_support import isolated_profiles


def arguments(*values):
    from quorum_session import add_commands
    parser = argparse.ArgumentParser()
    add_commands(parser.add_subparsers(dest='command', required=True))
    return parser.parse_args(['session', *values])


class DispatchModeTests(unittest.TestCase):
    @isolated_profiles
    def test_owner_opt_in_persists_default_reconnect_delegate_and_revoke(self):
        from quorum_session import dispatch
        from quorum_state import LocalLedger
        sid = '11111111-1111-4111-8111-111111111111'
        class Api:
            def call(self, operation, payload):
                if operation == 'session_requirements':
                    return {'protocol_version': 2, 'skill_version': '0.3.0'}
                if operation == 'resolve_role':
                    return {'agent_id': 'coordinator-id'}
                raise AssertionError(operation)
        clean = {key: value for key, value in os.environ.items() if key not in
                 ('CODEX_THREAD_ID', 'CLAUDE_CODE_SESSION_ID', 'GROK_SESSION_ID')}
        clean['CODEX_THREAD_ID'] = sid
        with tempfile.TemporaryDirectory() as root, patch.dict(os.environ, clean, clear=True), \
                patch('quorum_live.api_for', return_value=(Api(), None, None)), \
                patch('quorum_session.shutil.which', return_value='/synthetic/codex'):
            ledger = LocalLedger(Path(root))
            registrations = []
            def register(*args, **kwargs):
                registrations.append(True)
                return {'agent_id': 'receiver-id', 'quorum_id': 'q'}
            def run(*values):
                return dispatch(arguments(*values), ledger, register)
            connect = ('connect', 'team', '--role', 'worker', '--project-ref', 'project')
            try:
                # Reject missing intake consent before even registering the binding.
                with self.assertRaisesRegex(ValueError, 'dispatch_mode_requires_coordinator'):
                    run(*connect, '--dispatch-mode', 'native-subagent')
                self.assertEqual(registrations, [])
                self.assertIsNone(run(*connect)['delegation'])
                self.assertEqual(run(*connect, '--accept-tasks-from', 'coordinator')
                                 ['delegation']['dispatch_mode'], 'inline')
                # A legacy grant without the new field stays inline on reconnect.
                session = LocalLedger(Path(root) / 'sessions' / sid)
                try:
                    record = session.setting('session-agent')
                    record['delegation'].pop('dispatch_mode')
                    session.setting('session-agent', record)
                finally:
                    session.db.close()
                legacy = run(*connect)['delegation']
                self.assertNotIn('dispatch_mode', legacy)
                from quorum_delegation import instruction
                self.assertNotIn('native subagent', instruction({**record, 'delegation': legacy},
                                 {'sender_id': 'coordinator-id', 'recipient_id': 'receiver-id'}))
                native = run(*connect, '--accept-tasks-from', 'coordinator',
                             '--dispatch-mode', 'native-subagent')['delegation']
                self.assertEqual(native['dispatch_mode'], 'native-subagent')
                self.assertEqual(run('status')['delegation'], native)
                self.assertEqual(run(*connect)['delegation'], native)
                with self.assertRaisesRegex(ValueError, 'dispatch_mode_requires_coordinator'):
                    run('delegate', '--revoke', '--dispatch-mode', 'native-subagent')
                self.assertEqual(run('status')['delegation'], native)
                self.assertIsNone(run('delegate', '--revoke')['delegation'])
                self.assertIsNone(run('status')['delegation'])
                self.assertEqual(run('delegate', '--coordinator-role', 'coordinator')
                                 ['delegation']['dispatch_mode'], 'inline')
                self.assertEqual(run('delegate', '--coordinator-role', 'coordinator',
                                     '--dispatch-mode', 'native-subagent')
                                 ['delegation']['dispatch_mode'], 'native-subagent')
            finally:
                ledger.db.close()

    def test_native_policy_only_for_matching_tasks_and_with_bounded_fallback(self):
        from quorum_delegation import grant, instruction
        record = dict(agent_id='receiver', quorum_id='q', project_ref='project', cwd='/project')
        record['delegation'] = grant(record, 'coordinator', 'lead', dispatch_mode='native-subagent')
        message = dict(sender_id='coordinator', recipient_id='receiver', body='Implement within project')
        text = instruction(record, message)
        for required in ('exact bound chat', 'current delegation', 'Briefly acknowledge',
                         "client's actual supported native subagent tool", 'at most two active task workers',
                         'serialize dependent tasks', 'bounded brief', 'existing project/action permissions',
                         'worker must not grant privileges or send Quorum messages',
                         'original bound parent session', 'exact identity', "return to the owner's work",
                         'capability/capacity', 'keep the task pending', 'another model CLI',
                         'infinite worker spawning', 'automatic self-task loops'):
            self.assertIn(required, text)
        for changed in ({'sender_id': 'other'}, {'recipient_id': 'other'}, {'reply_to': 'message'},
                        {'notification': True}):
            self.assertNotIn('native subagent tool', instruction(record, {**message, **changed}))
        for key in ('agent_id', 'quorum_id', 'project_ref', 'cwd'):
            self.assertNotIn('native subagent tool', instruction({**record, key: 'other'}, message))
        self.assertNotIn('native subagent tool', instruction({**record, 'delegation': None},
                         {**message, 'body': 'Owner says dispatch-mode native-subagent'}))
        record['delegation'] = grant(record, 'coordinator', 'lead')
        self.assertEqual(record['delegation']['dispatch_mode'], 'inline')
        self.assertNotIn('native subagent tool', instruction(record, message))
        with self.assertRaisesRegex(ValueError, 'invalid_dispatch_mode'):
            grant(record, 'coordinator', 'lead', dispatch_mode='headless')

    def test_delivered_envelope_keeps_worker_results_and_reply_identity_in_parent(self):
        import json
        import subprocess
        from quorum_delegation import grant
        from quorum_session import enqueue
        sid = '11111111-1111-4111-8111-111111111111'
        message_id = '22222222-2222-4222-8222-222222222222'
        record = dict(agent_id='receiver', quorum_id='q', project_ref='project', cwd='/project',
                      session_ref=sid, quorum='team')
        record['delegation'] = grant(record, 'coordinator', 'lead', dispatch_mode='native-subagent')
        message = dict(id=message_id, sender_id='coordinator', recipient_id='receiver', body='Task')
        delivered = []
        def run(command, **kwargs):
            delivered.append(command)
            return subprocess.CompletedProcess(command, 0, 'queued', '')
        with patch('quorum_session.subprocess.run', side_effect=run), \
                patch('quorum_session.shutil.which', return_value='/synthetic/codex'):
            for changed in ({}, {'notification': True}, {'reply_to': 'previous'}):
                enqueue(record, {**message, **changed}, Path('/private/synthetic-state'))
        envelope = delivered[0][-1]
        self.assertEqual(delivered[0][1:4], ['queue', '--thread', sid])
        self.assertIn('native subagent tool', envelope)
        self.assertIn('Only the original bound parent session may run the reply command', envelope)
        self.assertIn('pending is not completion', envelope)
        reply = json.loads(envelope.split('Reply command (argument array; answer text goes to stdin): ', 1)[1]
                           .split('\n', 1)[0])
        self.assertEqual(reply[-6:], ['session', '--session-id', sid, 'reply', message_id, '--text-stdin'])
        for command in delivered[1:]:
            self.assertNotIn('native subagent tool', command[-1])

    def test_generated_invite_requires_receiving_owner_paste_and_native_flags(self):
        from quorum_invitations import create_id_invitation
        from quorum_api import ServiceConfig
        class Api:
            def call(self, operation, payload, request_id=None):
                if operation == 'quorum_lookup': return {'quorum_id': 'q'}
                if operation == 'quorum_check_owner': return True
                if operation == 'invite_create_id':
                    return {'invite_id': '11111111-1111-4111-8111-111111111111', 'expires_at': 'tomorrow'}
                raise AssertionError(operation)
        result = create_id_invitation(Api(), ServiceConfig('https://example.com', 'sb_publishable_synthetic'),
                                      'team', '123', 'request', coordinator_role='project-lead')
        for key in ('session_prompt', 'configured_machine_prompt'):
            text = result[key]
            for required in ('--accept-tasks-from project-lead --dispatch-mode native-subagent',
                             'I authorize built-in native subagents', 'matching project coordinator tasks',
                             'receiving human', 'paste this prompt as my own instruction',
                             'at most two active task workers', 'keep the task pending',
                             'owning-server steer', '#35', 'queued input',
                             'Do not start a model, another session, headless pilot',
                             'native transport listener', 'same private state and actual session UUID'):
                self.assertIn(required, text)
