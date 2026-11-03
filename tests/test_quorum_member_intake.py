"""Member opt-in and fresh task checks; synthetic bindings and RPC boundaries only."""
import copy
import json
import os
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from quorum_test_support import isolated_profiles

SCRIPTS = Path(__file__).resolve().parents[1] / 'skills/quorum/scripts'
sys.path.insert(0, str(SCRIPTS))
AGENT = '11111111-1111-4111-8111-111111111111'
SESSION = '22222222-2222-4222-8222-222222222222'
QUORUM = '33333333-3333-4333-8333-333333333333'
SENDER = '44444444-4444-4444-8444-444444444444'
MESSAGE = '55555555-5555-4555-8555-555555555555'


def binding():
    return dict(agent_id=AGENT, session_ref=SESSION, quorum_id=QUORUM,
                project_ref='project', cwd=str(Path.cwd().resolve()))


def authority(**changes):
    return dict(authorized=True, reason='current_member', message_id=MESSAGE,
                agent_id=AGENT, session_ref=SESSION, quorum_id=QUORUM,
                project_ref='project', sender_id=SENDER, **changes)


class MemberIntakeTests(unittest.TestCase):
    def test_explicit_member_grant_is_bound_and_does_not_convert_legacy_grants(self):
        from quorum_delegation import grant, member_grant, matches
        record = binding()
        policy = member_grant(record)
        self.assertEqual(policy, {**record, 'scope': 'members', 'dispatch_mode': 'inline'})
        record['delegation'] = policy
        for sender in (SENDER, AGENT):
            self.assertTrue(matches(record, dict(sender_id=sender, recipient_id=AGENT)))
        for field in ('agent_id', 'session_ref', 'quorum_id', 'project_ref', 'cwd'):
            self.assertFalse(matches({**record, field: 'other'}, dict(sender_id=SENDER, recipient_id=AGENT)))
        for terminal in ({'reply_to': MESSAGE}, {'notification': True},
                         {'maintenance': {'kind': 'quorum.update'}}):
            self.assertFalse(matches(record, dict(sender_id=SENDER, recipient_id=AGENT, **terminal)))
        record['delegation'] = grant(record, SENDER, 'coordinator')
        self.assertNotIn('scope', record['delegation'])
        self.assertFalse(matches(record, dict(sender_id=AGENT, recipient_id=AGENT)))

    def member(self):
        from quorum_delegation import member_grant
        record = binding()
        record['delegation'] = member_grant(record)
        return record

    def test_typed_maintenance_is_never_a_member_or_coordinator_task(self):
        from quorum_delegation import grant, instruction, matches
        record = self.member()
        message = dict(id=MESSAGE, sender_id=SENDER, recipient_id=AGENT,
                       maintenance={'kind': 'quorum.update'})
        for policy in (record['delegation'], grant(record, SENDER, 'coordinator')):
            record['delegation'] = policy
            self.assertFalse(matches(record, message))
            text = instruction(record, message)
            self.assertIn('maintenance proposal, never a delegated project task', text)
            self.assertNotIn('task-check', text)
            self.assertNotIn('Accept and carry out', text)

    def test_final_local_grant_save_serializes_a_revoke_at_the_read_write_boundary(self):
        from quorum_delegation import save_grant_binding
        from quorum_state import LocalLedger
        import sqlite3
        import threading
        with tempfile.TemporaryDirectory() as root:
            ledger = LocalLedger(Path(root))
            self.addCleanup(ledger.db.close)
            original = self.member()
            ledger.setting('session-agent', original)
            revoked = copy.deepcopy(original)
            revoked.pop('delegation')
            attempted, finished = threading.Event(), threading.Event()
            errors, writers = [], []
            def revoke():
                connection = sqlite3.connect(Path(root) / 'ledger.sqlite', timeout=2)
                try:
                    connection.set_trace_callback(lambda sql: attempted.set() if sql.startswith('update settings') else None)
                    with connection:
                        connection.execute('update settings set data=? where key=?',
                                           (json.dumps(revoked), 'session-agent'))
                except Exception as error:
                    errors.append(error)
                finally:
                    connection.close()
                    finished.set()
            def interleave(sql):
                if sql.lower().startswith('insert into settings') and not writers:
                    worker = threading.Thread(target=revoke)
                    writers.append(worker)
                    worker.start()
                    if not attempted.wait(2): errors.append('second writer did not reach the boundary')
                    if finished.is_set(): errors.append('revoke bypassed atomic reconnect save')
            ledger.db.set_trace_callback(interleave)
            try:
                save_grant_binding(ledger, 'session-agent', copy.deepcopy(original), original)
            finally:
                ledger.db.set_trace_callback(None)
                for worker in writers: worker.join(3)
            self.assertTrue(finished.is_set())
            self.assertEqual(errors, [])
            self.assertEqual(ledger.setting('session-agent'), revoked)

    def test_final_save_preserves_changed_binding_and_current_transport_endpoint(self):
        from quorum_delegation import save_grant_binding
        from quorum_state import LocalLedger
        with tempfile.TemporaryDirectory() as root:
            ledger = LocalLedger(Path(root))
            self.addCleanup(ledger.db.close)
            original = {**self.member(), 'messaging_socket': 'old-synthetic-endpoint'}
            latest = {**original, 'messaging_socket': 'new-synthetic-endpoint'}
            ledger.setting('session-agent', latest)
            saved = save_grant_binding(ledger, 'session-agent', copy.deepcopy(original), original)
            self.assertEqual(saved['messaging_socket'], 'new-synthetic-endpoint')
            replacement = {**saved, 'quorum_id': SENDER}
            ledger.setting('session-agent', replacement)
            with self.assertRaisesRegex(ValueError, 'delegation_scope_changed'):
                save_grant_binding(ledger, 'session-agent', original, saved)
            self.assertEqual(ledger.setting('session-agent'), replacement)

    def test_task_check_uses_only_addressed_message_id_and_current_local_scope(self):
        from quorum_delegation import task_check
        record = self.member()
        def call(operation, payload):
            self.assertEqual((operation, payload), ('session_task_authority', {
                'agent_id': AGENT, 'session_ref': SESSION, 'message_id': MESSAGE}))
            return authority()
        result = task_check(SimpleNamespace(call=call), record, MESSAGE, lambda: copy.deepcopy(record))
        self.assertTrue(result['authorized'])
        self.assertEqual(result['sender_id'], SENDER)

    def test_successful_member_intake_never_becomes_an_action_permission(self):
        from quorum_delegation import task_check
        record = self.member()
        result = task_check(SimpleNamespace(call=lambda *_: authority()), record, MESSAGE, lambda: record)
        self.assertEqual(set(result['owner_held_actions']), {
            'delete_overwrite_force_push', 'merge_release_deploy', 'ddl_migrations',
            'permissions_configuration_credentials_secrets', 'spend_paid_models_sessions_agents',
            'external_messages_publication', 'permission_prompts'})

    def test_member_envelope_requires_fresh_message_check_in_its_actual_worker_selector(self):
        from quorum_delegation import instruction
        record = self.member()
        text = instruction(record, dict(id=MESSAGE, sender_id=SENDER, recipient_id=AGENT),
                           status_label='session --worker-capability /synthetic/capability.json status', holder='worker')
        self.assertIn('session --worker-capability /synthetic/capability.json task-check ' + MESSAGE, text)
        self.assertNotIn('pinned coordinator', text)
        self.assertIn('parent gains no authority', text)
        self.assertIn('always require', text)

    def test_generated_invitation_grants_members_inline_without_paid_dispatch_right(self):
        from quorum_invitations import completion
        text = completion('coordinator')
        self.assertIn('--accept-tasks-from-members', text)
        self.assertNotIn('--accept-tasks-from coordinator', text)
        self.assertNotIn('I authorize built-in native subagents', text)
        self.assertIn('anonymous', text)
        self.assertIn('task-check', text)
        self.assertIn('always require', text)

    def test_revoked_or_different_grant_is_denied_before_network(self):
        from quorum_delegation import grant, task_check
        record = self.member()
        def forbidden(*_): self.fail('no authority RPC without a current member grant')
        for policy in (None, grant(record, SENDER, 'coordinator'),
                       {**record['delegation'], 'scope': 'unknown'},
                       {**record['delegation'], 'dispatch_mode': 'native-subagent'}):
            with self.subTest(policy=policy):
                current = {**record, 'delegation': policy}
                self.assertFalse(task_check(SimpleNamespace(call=forbidden), record, MESSAGE,
                                            lambda: current)['authorized'])
        for field in ('session_ref', 'agent_id', 'quorum_id', 'project_ref', 'cwd'):
            with self.subTest(field=field):
                current = {**record, field: 'other'}
                self.assertFalse(task_check(SimpleNamespace(call=forbidden), record, MESSAGE,
                                            lambda: current)['authorized'])

    def test_local_grant_revoked_while_rpc_runs_cannot_authorize_execution(self):
        from quorum_delegation import task_check
        record = self.member()
        current = copy.deepcopy(record)
        def call(*_):
            current['delegation'] = None
            return authority()
        self.assertFalse(task_check(SimpleNamespace(call=call), record, MESSAGE, lambda: current)['authorized'])

    def test_binding_or_transport_replaced_during_authority_check_stays_pending(self):
        from quorum_delegation import task_check
        record = {**self.member(), 'engine': 'codex', 'transport': 'tool-inbox',
                  'registration': {'session_ref': SESSION},
                  'worker_binding': {'parent_thread_id': SENDER},
                  'messaging_socket': 'synthetic-old', 'grok_transport': {'generation': 1}}
        for changes in ({'engine': 'claude'}, {'transport': 'native'},
                        {'registration': {'session_ref': SENDER}},
                        {'worker_binding': {'parent_thread_id': AGENT}},
                        {'messaging_socket': 'synthetic-new'}, {'grok_transport': {'generation': 2}}):
            with self.subTest(changes=changes):
                current = copy.deepcopy(record)
                def call(*_):
                    current.update(changes)
                    return authority()
                result = task_check(SimpleNamespace(call=call), record, MESSAGE, lambda: current)
                self.assertFalse(result['authorized'])
                self.assertEqual(result['reason'], 'delegation_changed')

    def test_current_working_directory_is_checked_before_network(self):
        from quorum_delegation import task_check
        record = self.member()
        with tempfile.TemporaryDirectory() as root:
            record['cwd'] = root
            record['delegation']['cwd'] = root
            def forbidden(*_): self.fail('no authority RPC outside the pinned directory')
            self.assertFalse(task_check(SimpleNamespace(call=forbidden), record, MESSAGE,
                                        lambda: record)['authorized'])

    def test_unavailable_backend_stays_pending_without_role_or_peer_fallback(self):
        from quorum_api import QuorumError
        from quorum_delegation import task_check
        record = self.member()
        for error in ('service_error', 'service_unavailable'):
            def call(operation, payload):
                self.assertEqual(operation, 'session_task_authority')
                raise QuorumError(error)
            result = task_check(SimpleNamespace(call=call), record, MESSAGE, lambda: record)
            self.assertEqual((result['authorized'], result['state'], result['reason']),
                             (False, 'pending', 'authority_unavailable'))

    def test_authority_metadata_must_match_exact_binding_and_fixed_result_schema(self):
        from quorum_delegation import task_check
        record = self.member()
        for changes in ({'agent_id': SENDER}, {'session_ref': SENDER}, {'quorum_id': SENDER},
                        {'project_ref': 'other'}, {'message_id': SENDER}, {'sender_id': 'invalid'},
                        {'authorized': 'true'}, {'reason': 'other'}, {'body': 'private-body-marker'}):
            with self.subTest(changes=changes):
                result = task_check(SimpleNamespace(call=lambda *_: {**authority(), **changes}),
                                    record, MESSAGE, lambda: record)
                self.assertFalse(result['authorized'])
                self.assertNotIn('private-body-marker', json.dumps(result))

    def test_sender_revocation_and_terminal_messages_are_held(self):
        from quorum_delegation import task_check
        record = self.member()
        for reason in ('sender_unavailable', 'not_task'):
            response = {**authority(), 'authorized': False, 'reason': reason}
            result = task_check(SimpleNamespace(call=lambda *_: response), record, MESSAGE, lambda: record)
            self.assertEqual((result['authorized'], result['reason']), (False, reason))


class MemberCommandTests(unittest.TestCase):
    @isolated_profiles
    def test_native_connect_reconnect_delegate_and_task_check_preserve_authority(self):
        from quorum_delegation import grant
        from quorum_release import installed_release
        from quorum_session import dispatch
        from quorum_state import LocalLedger
        class Api:
            owned = True
            required = installed_release()
            def call(self, op, payload):
                if op == 'session_requirements': return self.required
                if op == 'session_peers':
                    if not self.owned:
                        from quorum_api import QuorumError
                        raise QuorumError('membership_required')
                    return []
                if op == 'session_task_authority': return authority()
                if op == 'resolve_role': return {'agent_id': SENDER}
                raise AssertionError(op)
        api = Api()
        environment = {key: value for key, value in os.environ.items()
                       if key not in ('CODEX_THREAD_ID', 'CLAUDE_CODE_SESSION_ID', 'GROK_SESSION_ID')}
        environment['CODEX_THREAD_ID'] = SESSION
        with tempfile.TemporaryDirectory() as root, patch.dict(os.environ, environment, clear=True), \
                patch('quorum_live.api_for', return_value=(api, None, None)), \
                patch('quorum_session.shutil.which', return_value='/synthetic/codex'):
            auth = LocalLedger(Path(root))
            self.addCleanup(auth.db.close)
            on_register = []
            def register(*_, **__):
                if on_register: on_register.pop()()
                return dict(agent_id=AGENT, quorum_id=QUORUM)
            connect = SimpleNamespace(session_id=SESSION, action='connect', role='receiver', quorum='team',
                                      project_ref='project', accept_tasks_from=None,
                                      accept_tasks_from_members=True, dispatch_mode='inline')
            result = dispatch(connect, auth, register)
            self.assertEqual(result['delegation']['scope'], 'members')
            self.assertTrue(dispatch(SimpleNamespace(session_id=SESSION, action='task-check', message_id=MESSAGE),
                                     auth, register)['authorized'])
            session = LocalLedger(Path(root) / 'sessions' / SESSION)
            self.addCleanup(session.db.close)
            record = session.setting('session-agent')
            record['delegation'] = grant(record, SENDER, 'coordinator')
            session.setting('session-agent', record)
            with self.assertRaisesRegex(ValueError, 'delegation_scope_changed'):
                dispatch(connect, auth, register)
            self.assertEqual(session.setting('session-agent')['delegation']['coordinator_id'], SENDER)
            connect.accept_tasks_from_members = False
            dispatch(connect, auth, register)
            self.assertEqual(session.setting('session-agent')['delegation']['coordinator_id'], SENDER)
            from quorum_api import QuorumError
            from quorum_release import ReleaseMismatch
            api.owned = False
            with self.assertRaisesRegex(QuorumError, 'membership_required'):
                dispatch(SimpleNamespace(session_id=SESSION, action='delegate', revoke=False,
                                         members=True, coordinator_role=None), auth, register)
            self.assertEqual(session.setting('session-agent')['delegation']['coordinator_id'], SENDER)
            api.owned = True
            api.required = {**installed_release(), 'skill_version': '999.0.0'}
            with self.assertRaises(ReleaseMismatch):
                dispatch(SimpleNamespace(session_id=SESSION, action='task-check', message_id=MESSAGE),
                         auth, register)
            api.required = installed_release()
            delegated = dispatch(SimpleNamespace(session_id=SESSION, action='delegate', revoke=False,
                                                 members=True, coordinator_role=None), auth, register)
            self.assertEqual(delegated['delegation']['scope'], 'members')
            dispatch(SimpleNamespace(session_id=SESSION, action='delegate', revoke=True), auth, register)
            self.assertFalse(dispatch(SimpleNamespace(session_id=SESSION, action='task-check', message_id=MESSAGE),
                                      auth, register)['authorized'])
            connect.accept_tasks_from_members = True
            with self.assertRaisesRegex(ValueError, 'delegation_scope_changed'):
                dispatch(connect, auth, register)
            self.assertNotIn('delegation', session.setting('session-agent'))
            dispatch(SimpleNamespace(session_id=SESSION, action='delegate', revoke=False,
                                     members=True, coordinator_role=None), auth, register)
            def revoke_during_registration():
                latest = session.setting('session-agent')
                latest.pop('delegation')
                session.setting('session-agent', latest)
            on_register.append(revoke_during_registration)
            connect.accept_tasks_from_members = False
            with self.assertRaisesRegex(ValueError, 'delegation_scope_changed'):
                dispatch(connect, auth, register)
            self.assertNotIn('delegation', session.setting('session-agent'))
