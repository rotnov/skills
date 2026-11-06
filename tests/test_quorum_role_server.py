"""Issue #105 server part: the client against the role-assignment migration and against an older service.

Synthetic services only; never a model, a real login or live quorum state.
"""
import contextlib
import io
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
from urllib.error import HTTPError
import uuid

SCRIPTS = Path(__file__).resolve().parents[1] / 'skills/quorum/scripts'
sys.path.insert(0, str(SCRIPTS))
from quorum_test_support import isolated_profiles
import test_quorum_invite_roles as roles
from test_quorum_invite_roles import CLIENT_VARIABLES, QUORUM, SID, Service

AGENT = '66666666-6666-4666-8666-666666666666'


class Cli(unittest.TestCase):
    def setUp(self):
        clean = {key: value for key, value in os.environ.items() if key not in CLIENT_VARIABLES}
        isolated = patch.dict(os.environ, {**clean, 'CODEX_THREAD_ID': SID}, clear=True)
        isolated.start()
        self.addCleanup(isolated.stop)
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)

    def cli(self, service, *argv):
        import quorum
        from quorum_api import ServiceConfig
        with patch('quorum_live.api_for', return_value=(service, None, None)), \
                patch('quorum_profiles.resolve_profile',
                      return_value=(ServiceConfig('https://synthetic.invalid', 'public'), None)), \
                patch('quorum.QuorumApi', return_value=service), patch('quorum.token_provider'), \
                patch('quorum_session.shutil.which', return_value='/synthetic/codex'), \
                contextlib.redirect_stdout(io.StringIO()) as output:
            code = quorum.main(['--state', str(self.root), *argv])
        text = output.getvalue()
        return code, json.loads(text), text

    def retired(self, agent_id):
        from quorum_claude_worker import RETIRED
        from quorum_state import LocalLedger
        ledger = LocalLedger(self.root)
        try: return ledger.setting(RETIRED + agent_id) is not None
        finally: ledger.db.close()


class ServerRoleConnectTests(Cli):
    def connect(self, service, role, *flags):
        return self.cli(service, 'session', 'connect', 'team', '--role', role, '--project-ref', 'project', *flags)

    def live(self, service, role):
        return sorted(agent_id for agent_id, agent in service.agents.items()
                      if agent['role'] == role and not agent['revoked'])

    @isolated_profiles
    def test_server_refuses_an_own_duplicate_even_without_a_flag(self):
        service = Service(new=True)
        stale = service.add(Service.ME, 'jekman')
        code, result, text = self.connect(service, 'jekman')
        self.assertEqual((code, result['code']), (3, 'role_held_by_own_agent'), text)
        self.assertEqual(result['data']['agent_id'], stale)
        self.assertIn('--supersede', result['data']['next_step'])
        self.assertEqual(self.live(service, 'jekman'), [stale])

    @isolated_profiles
    def test_server_supersede_retires_in_the_join_without_a_client_leave(self):
        service = Service(new=True)
        stale = service.add(Service.ME, 'jekman')
        code, result, text = self.connect(service, 'jekman', '--supersede')
        self.assertEqual(code, 0, text)
        self.assertEqual(result['data']['superseded_agent_id'], stale)
        self.assertNotIn('superseded', result['data'])
        self.assertEqual(self.live(service, 'jekman'), [result['data']['agent_id']])
        self.assertNotIn('agent_leave', service.ops())
        joins = [payload for operation, payload, _ in service.calls if operation == 'agent_join']
        self.assertEqual([join.get('supersede') for join in joins], [True])
        # Recorded like an owner's `leave`, so a chat still bound to it retires its binding.
        self.assertTrue(self.retired(stale))

    @isolated_profiles
    def test_older_server_supersede_falls_back_to_the_client_leave(self):
        service = Service()
        stale = service.add(Service.ME, 'jekman')
        code, result, text = self.connect(service, 'jekman', '--supersede')
        self.assertEqual(code, 0, text)
        self.assertEqual(result['data']['superseded_agent_id'], stale)
        self.assertIn(('agent_leave', {'agent_id': stale}, 'supersede:' + stale), service.calls)
        self.assertEqual(self.live(service, 'jekman'), [result['data']['agent_id']])

    @isolated_profiles
    def test_server_assignment_mismatch_names_the_assigned_binding(self):
        service = Service(new=True, assigned=('reviewer', 'project'))
        code, result, text = self.connect(service, 'jekman')
        self.assertEqual((code, result['code']), (3, 'role_assignment_mismatch'), text)
        self.assertEqual(result['data']['assigned'], {'role': 'reviewer', 'project_ref': 'project'})
        self.assertIn('do not pick another role', result['data']['next_step'])
        self.assertEqual(self.live(service, 'jekman'), [])
        code, result, text = self.connect(service, 'reviewer')
        self.assertEqual(code, 0, text)

    @isolated_profiles
    def test_server_refusal_for_another_identity_never_shows_its_agent(self):
        service = Service(new=True)
        service.add('someone-else', 'jekman')
        code, result, text = self.connect(service, 'jekman')
        self.assertEqual((code, result['code']), (3, 'role_held_by_other_identity'), text)
        self.assertNotIn('agent_id', result['data'])
        self.assertIn('agent-retire', result['data']['next_step'])

    @isolated_profiles
    def test_same_chat_reconnect_returns_the_original_agent_on_the_server(self):
        service = Service(new=True)
        code, first, text = self.connect(service, 'jekman')
        self.assertEqual(code, 0, text)
        code, again, text = self.connect(service, 'jekman', '--supersede')
        self.assertEqual((code, again['data']['agent_id']), (0, first['data']['agent_id']), text)
        self.assertNotIn('superseded_agent_id', again['data'])


class RegisterAgentTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        from quorum_state import LocalLedger
        self.ledger = LocalLedger(Path(temporary.name))
        self.addCleanup(self.ledger.db.close)
        self.data = {'engine': 'claude', 'project_ref': 'project', 'session_ref': SID, 'cwd': temporary.name}

    def register(self, service, **options):
        import quorum
        with patch('quorum_release.ReleaseGuard'):
            return quorum.register_agent(service, self.ledger, 'team', 'jekman', self.data, **options)

    def test_kept_parent_coexists_on_the_server(self):
        service = Service(new=True)
        parent = service.add(Service.ME, 'jekman')
        result = self.register(service, keep_agent_id=parent)
        self.assertEqual(len([a for a in service.agents.values() if not a['revoked']]), 2)
        self.assertNotIn('superseded', result)

    def test_older_server_without_keep_registers_with_the_same_request(self):
        service = Service()
        parent = service.add(Service.ME, 'jekman')
        result = self.register(service, keep_agent_id=parent)
        joins = [(payload, request) for operation, payload, request in service.calls if operation == 'agent_join']
        self.assertEqual([('keep_agent_id' in payload) for payload, _ in joins], [True, False])
        self.assertEqual(joins[0][1], joins[1][1])
        self.assertIn(result['agent_id'], service.agents)

    def test_older_server_supersede_is_left_to_the_caller(self):
        from quorum_api import rpc_missing, QuorumError
        with self.assertRaises(QuorumError) as raised:
            self.register(Service(), supersede=True)
        self.assertTrue(rpc_missing(raised.exception))


class InvitationStorageTests(unittest.TestCase):
    config = roles.InvitationRoleTests.config

    @isolated_profiles
    def test_new_server_stores_the_assignment_and_the_prompt_says_so(self):
        from quorum_invitations import create_id_invitation, create_open_invitation, SERVER_ASSIGNED
        api = roles.InvitationRoleTests.Api(new=True)
        github = create_id_invitation(api, self.config(), 'team', '123', 'r', role='jekman', project_ref='velvil')
        opened = create_open_invitation(api, self.config(), 'team', 'r2', role='jekman')
        self.assertEqual(api.payloads, [{'quorum_id': 'q', 'target_github_id': '123', 'role': 'jekman',
                                         'project_ref': 'velvil'},
                                        {'quorum_id': 'q', 'role': 'jekman', 'project_ref': 'team'}])
        for result in (github, opened):
            self.assertEqual(result['assignment_storage'], 'server')
            self.assertIn(SERVER_ASSIGNED, result['session_prompt'])
            self.assertIn('--exclusive-role', result['session_prompt'])

    @isolated_profiles
    def test_older_server_keeps_the_prompt_only_assignment(self):
        from quorum_invitations import create_id_invitation, SERVER_ASSIGNED
        api = roles.InvitationRoleTests.Api()
        result = create_id_invitation(api, self.config(), 'team', '123', 'r', role='jekman')
        self.assertEqual(result['assignment_storage'], 'prompt_only')
        self.assertEqual(result['assigned'], {'role': 'jekman', 'project_ref': 'team'})
        self.assertEqual(api.payloads[-1], {'quorum_id': 'q', 'target_github_id': '123'})
        self.assertNotIn(SERVER_ASSIGNED, result['session_prompt'])
        self.assertIn("--role jekman --project-ref 'team' --exclusive-role", result['session_prompt'])

    @isolated_profiles
    def test_only_a_missing_rpc_falls_back_and_a_wrong_echo_is_refused(self):
        from quorum_api import QuorumError
        from quorum_invitations import create_id_invitation

        class Denied(roles.InvitationRoleTests.Api):
            def call(self, operation, payload, request_id=None):
                if operation == 'invite_create_id': self.calls.append(operation); raise QuorumError('auth_required')
                return super().call(operation, payload, request_id)

        class Wrong(roles.InvitationRoleTests.Api):
            def call(self, operation, payload, request_id=None):
                result = super().call(operation, payload, request_id)
                return {**result, 'assigned': None} if operation == 'invite_create_id' else result

        denied = Denied()
        with self.assertRaisesRegex(QuorumError, 'auth_required'):
            create_id_invitation(denied, self.config(), 'team', '123', 'r', role='jekman')
        self.assertEqual(denied.calls.count('invite_create_id'), 1)
        with self.assertRaisesRegex(QuorumError, 'invalid_response'):
            create_id_invitation(Wrong(new=True), self.config(), 'team', '123', 'r', role='jekman')

    def test_accepted_assignment_is_shape_checked(self):
        from quorum_invitations import accepted_assignment
        good = {'role': 'jekman', 'project_ref': 'team/app'}
        self.assertEqual(accepted_assignment({'assigned': good}), {'assigned': good})
        self.assertEqual(accepted_assignment({'assigned': {'role': None, 'project_ref': 'team'}}),
                         {'assigned': {'role': None, 'project_ref': 'team'}})
        for bad in (None, {}, {'role': 'Bad', 'project_ref': 'x'}, {'role': 'a', 'project_ref': '$(id)'},
                    {'role': 'a', 'project_ref': 'x', 'token': 'secret'}):
            self.assertEqual(accepted_assignment({'assigned': bad}), {})


class OwnerAgentCommandTests(Cli):
    class Owner:
        def __init__(self, missing=False, denial=None):
            self.calls, self.missing, self.denial = [], missing, denial

        def call(self, operation, payload, request_id=None):
            from quorum_api import QuorumError
            self.calls.append((operation, payload, request_id))
            if operation == 'quorum_lookup': return {'quorum_id': QUORUM}
            if self.missing: raise QuorumError('service_error', missing=True)
            if self.denial: raise QuorumError('auth_required', denial=self.denial)
            if operation == 'quorum_agents':
                return [{'agent_id': AGENT, 'role': 'jekman', 'project_ref': 'team', 'engine': 'claude',
                         'owner_label': 'github:1', 'member_role': 'member', 'own': False, 'live': True,
                         'revoked': False, 'last_seen': None, 'session_ref': SID, 'token': 'never-shown'}]
            if operation == 'agent_retire':
                return {'retired': True, 'agent_id': payload['agent_id'], 'already_revoked': False}
            raise AssertionError(operation)

    @isolated_profiles
    def test_roster_shows_only_documented_fields(self):
        code, result, text = self.cli(self.Owner(), 'agents', 'team')
        self.assertEqual(code, 0, text)
        self.assertEqual(set(result['data']['agents'][0]), {'agent_id', 'role', 'project_ref', 'engine', 'owner_label',
                                                          'member_role', 'own', 'live', 'revoked', 'last_seen'})
        self.assertNotIn(SID, text)
        self.assertNotIn('never-shown', text)

    @isolated_profiles
    def test_retire_is_idempotent_by_agent_and_recorded_like_leave(self):
        owner = self.Owner()
        code, result, text = self.cli(owner, 'agent-retire', 'team', AGENT)
        self.assertEqual(code, 0, text)
        self.assertEqual(result['data'], {'quorum_id': QUORUM, 'agent_id': AGENT, 'retired': True,
                                          'already_revoked': False})
        self.assertIn(('agent_retire', {'quorum_id': QUORUM, 'agent_id': AGENT}, 'agent-retire:' + AGENT), owner.calls)
        self.assertTrue(self.retired(AGENT))
        code, _, text = self.cli(owner, 'agent-retire', 'team', AGENT, '--request-id', 'retry-1')
        self.assertEqual(code, 0, text)
        self.assertEqual(owner.calls[-1][2], 'retry-1')

    @isolated_profiles
    def test_older_server_reports_owner_retire_unavailable(self):
        for argv in (('agents', 'team'), ('agent-retire', 'team', AGENT)):
            code, result, text = self.cli(self.Owner(missing=True), *argv)
            self.assertEqual((code, result['code']), (4, 'owner_retire_unavailable'), text)
        self.assertFalse(self.retired(AGENT))

    @isolated_profiles
    def test_non_owner_and_foreign_agent_keep_their_codes(self):
        for denial in ('owner_required', 'agent_denied'):
            code, result, text = self.cli(self.Owner(denial=denial), 'agent-retire', 'team', AGENT)
            self.assertEqual((code, result['code']), (3, denial), text)
        code, result, text = self.cli(self.Owner(), 'agent-retire', 'team', 'not-a-uuid')
        self.assertEqual((code, result['code']), (3, 'invalid_agent_id'), text)


class ServerErrorTests(unittest.TestCase):
    def error_for(self, status, body):
        from quorum_api import _open
        error = HTTPError('https://synthetic.invalid', status, 'x', {}, io.BytesIO(json.dumps(body).encode()))
        with patch('quorum_api.build_opener') as opener:
            opener.return_value.open.side_effect = error
            with self.assertRaises(Exception) as raised:
                _open(object())
        return raised.exception

    def test_missing_function_is_detected_only_from_postgrest_code(self):
        from quorum_api import rpc_missing
        self.assertTrue(rpc_missing(self.error_for(404, {'code': 'PGRST202', 'message': 'Could not find the function'})))
        self.assertFalse(rpc_missing(self.error_for(404, {'code': 'other'})))
        self.assertFalse(rpc_missing(self.error_for(400, {'code': 'PGRST202'})))

    def test_role_codes_keep_only_shape_checked_own_detail(self):
        error = self.error_for(400, {'message': 'role_held_by_own_agent', 'details': AGENT})
        self.assertEqual((str(error), error.detail), ('role_held_by_own_agent', AGENT))
        error = self.error_for(400, {'message': 'role_held_by_own_agent', 'details': 'secret text'})
        self.assertEqual((str(error), error.detail), ('role_held_by_own_agent', None))
        error = self.error_for(400, {'message': 'role_assignment_mismatch',
                                'details': json.dumps({'role': 'reviewer', 'project_ref': 'team'})})
        self.assertEqual(error.detail, {'role': 'reviewer', 'project_ref': 'team'})
        error = self.error_for(400, {'message': 'role_held_by_other_identity', 'details': AGENT})
        self.assertEqual((str(error), error.detail), ('role_held_by_other_identity', None))
        self.assertEqual(str(self.error_for(400, {'message': ['not', 'text']})), 'service_error')
        self.assertEqual(self.error_for(403, {'message': 'owner_required'}).denial, 'owner_required')


if __name__ == '__main__':
    unittest.main()
