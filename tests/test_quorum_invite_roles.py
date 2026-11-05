"""Issue #105: an invitation's assigned role, exclusive role connects and superseding an own stale agent.

Synthetic service only; never a model, a real login or live quorum state.
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
import uuid

SCRIPTS = Path(__file__).resolve().parents[1] / 'skills/quorum/scripts'
sys.path.insert(0, str(SCRIPTS))
from quorum_test_support import isolated_profiles

SID = '11111111-1111-4111-8111-111111111111'
OTHER_SID = '33333333-3333-4333-8333-333333333333'
QUORUM = '44444444-4444-4444-8444-444444444444'
INVITE = '55555555-5555-4555-8555-555555555555'
CLIENT_VARIABLES = ('CODEX_THREAD_ID', 'GROK_SESSION_ID', 'GROK_LEADER_SOCKET', 'CLAUDE_CODE_SESSION_ID',
                    'CLAUDE_CODE_MESSAGING_SOCKET', 'CLAUDE_CODE_MESSAGING_TOKEN')


class Service:
    """The rules this issue depends on: `resolve_role` names exactly one live holder per role and project,
    `agent_status`/`agent_leave` succeed only for the caller's own live agents (else `agent_denied`).

    By default an older service: `agent_join` has no supersede/keep arguments (PostgREST's missing-function
    answer). With `new=True` it is the role-assignment migration: one live agent per identity, role and project,
    server-side supersede, and the membership's assigned role."""
    ME = 'me'

    def __init__(self, new=False, assigned=None):
        self.calls, self.requests, self.agents, self.devices = [], {}, {}, 0
        self.new, self.assigned = new, assigned

    def ops(self):
        return [call[0] for call in self.calls]

    def add(self, owner, role, project_ref='project', session_ref=OTHER_SID):
        agent_id = str(uuid.uuid4())
        self.agents[agent_id] = {'owner': owner, 'role': role, 'project_ref': project_ref,
                                 'session_ref': session_ref, 'device_id': 'synthetic', 'revoked': False}
        return agent_id

    def call(self, operation, payload, request_id=None):
        from quorum_api import QuorumError
        if operation == 'session_requirements': return {'protocol_version': 2, 'skill_version': '0.3.0'}
        self.calls.append((operation, payload, request_id))
        if (operation, request_id) in self.requests: return self.requests[operation, request_id]
        if operation == 'quorum_lookup': return {'quorum_id': QUORUM}
        if operation == 'resolve_role':
            live = [agent_id for agent_id, agent in self.agents.items() if not agent['revoked']
                    and (agent['role'], agent['project_ref']) == (payload['role'], payload['project_ref'])]
            if len(live) != 1: raise QuorumError('recipient_ambiguous_or_missing')
            return {'agent_id': live[0]}
        own = self.agents.get(payload.get('agent_id'), {'owner': None, 'revoked': True})
        if operation in ('agent_status', 'agent_leave') and (own['revoked'] or own['owner'] != self.ME):
            raise QuorumError('auth_required', denial='agent_denied')
        if operation == 'agent_status': return {'agent_id': payload['agent_id'], 'registered': True}
        if operation == 'device_register':
            self.devices += 1
            result = {'device_id': str(uuid.UUID(int=self.devices))}
        elif operation == 'agent_join':
            if not self.new and {'supersede', 'keep_agent_id'} & set(payload):
                raise QuorumError('service_error', missing=True)
            same = [agent_id for agent_id, agent in self.agents.items()
                    if (agent['device_id'], agent['session_ref']) == (payload['device_id'], payload['session_ref'])]
            superseded = []
            if same: result = {'agent_id': same[0]}
            else:
                if self.new: superseded = self.claim(payload)
                agent_id = str(uuid.uuid4())
                self.agents[agent_id] = {'owner': self.ME, 'role': payload['role'],
                                         'project_ref': payload['project_ref'], 'session_ref': payload['session_ref'],
                                         'device_id': payload['device_id'], 'revoked': False}
                result = {'agent_id': agent_id}
            if self.new: result = {**result, 'superseded': superseded}
        elif operation == 'agent_leave':
            self.agents[payload['agent_id']]['revoked'] = True
            result = {'left': True}
        else: raise AssertionError(operation)
        if request_id is not None: self.requests[operation, request_id] = result
        return result

    def claim(self, payload):
        from quorum_api import QuorumError
        if self.assigned and (payload['role'], payload['project_ref']) != self.assigned:
            raise QuorumError('role_assignment_mismatch', detail=json.dumps(
                {'role': self.assigned[0], 'project_ref': self.assigned[1]}))
        live = [(agent_id, agent) for agent_id, agent in self.agents.items() if not agent['revoked']
                and (agent['role'], agent['project_ref']) == (payload['role'], payload['project_ref'])]
        if any(agent['owner'] != self.ME for _, agent in live): raise QuorumError('role_held_by_other_identity')
        own = sorted(agent_id for agent_id, _ in live if agent_id != payload.get('keep_agent_id'))
        if own and not payload.get('supersede'): raise QuorumError('role_held_by_own_agent', detail=own[0])
        for agent_id in own: self.agents[agent_id]['revoked'] = True
        return own


class ExclusiveRoleConnectTests(unittest.TestCase):
    def setUp(self):
        clean = {key: value for key, value in os.environ.items() if key not in CLIENT_VARIABLES}
        isolated = patch.dict(os.environ, {**clean, 'CODEX_THREAD_ID': SID}, clear=True)
        isolated.start()
        self.addCleanup(isolated.stop)
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.service = Service()

    def cli(self, *argv):
        import quorum
        from quorum_api import ServiceConfig
        with patch('quorum_live.api_for', return_value=(self.service, None, None)), \
                patch('quorum_profiles.resolve_profile',
                      return_value=(ServiceConfig('https://synthetic.invalid', 'public'), None)), \
                patch('quorum.QuorumApi', return_value=self.service), patch('quorum.token_provider'), \
                patch('quorum_session.shutil.which', return_value='/synthetic/codex'), \
                contextlib.redirect_stdout(io.StringIO()) as output:
            code = quorum.main(['--state', str(self.root), *argv])
        text = output.getvalue()
        return code, json.loads(text), text

    def connect(self, role, *flags):
        return self.cli('session', 'connect', 'team', '--role', role, '--project-ref', 'project', *flags)

    def live(self, role):
        return sorted(agent_id for agent_id, agent in self.service.agents.items()
                      if agent['role'] == role and not agent['revoked'])

    @isolated_profiles
    def test_free_role_registers_as_before(self):
        code, result, text = self.connect('jekman', '--exclusive-role')
        self.assertEqual(code, 0, text)
        self.assertEqual(self.live('jekman'), [result['data']['agent_id']])
        self.assertNotIn('superseded_agent_id', result['data'])

    @isolated_profiles
    def test_role_of_another_identity_is_refused_before_any_registration(self):
        self.service.add('someone-else', 'jekman')
        for flag in ('--exclusive-role', '--supersede'):
            code, result, text = self.connect('jekman', flag)
            self.assertEqual((code, result['code']), (3, 'role_held_by_other_identity'), text)
            self.assertEqual(result['data']['role'], 'jekman')
            # Another identity's agent ID never crosses the CLI boundary.
            self.assertNotIn('agent_id', result['data'])
            self.assertIn('do not pick a different role', result['data']['next_step'].lower())
        self.assertFalse({'device_register', 'agent_join', 'agent_leave'} & set(self.service.ops()))
        self.assertEqual(len(self.live('jekman')), 1)

    @isolated_profiles
    def test_own_other_agent_is_refused_with_its_id_and_the_leave_command(self):
        stale = self.service.add(Service.ME, 'jekman')
        code, result, text = self.connect('jekman', '--exclusive-role')
        self.assertEqual((code, result['code']), (3, 'role_held_by_own_agent'), text)
        self.assertEqual(result['data']['agent_id'], stale)
        self.assertIn('--supersede', result['data']['next_step'])
        self.assertIn('leave --request-id', result['data']['next_step'])
        self.assertFalse({'device_register', 'agent_join', 'agent_leave'} & set(self.service.ops()))
        self.assertEqual(self.live('jekman'), [stale])

    @isolated_profiles
    def test_supersede_retires_the_own_stale_agent_so_exactly_one_holds_the_role(self):
        stale = self.service.add(Service.ME, 'jekman')
        code, result, text = self.connect('jekman', '--supersede')
        self.assertEqual(code, 0, text)
        fresh = result['data']['agent_id']
        self.assertEqual(result['data']['superseded_agent_id'], stale)
        self.assertEqual(self.live('jekman'), [fresh])
        self.assertIn(('agent_leave', {'agent_id': stale}, 'supersede:' + stale), self.service.calls)
        # The leave is recorded like an owner's `leave`, so a chat still bound to it retires its binding.
        from quorum_claude_worker import RETIRED
        from quorum_state import LocalLedger
        ledger = LocalLedger(self.root)
        try: self.assertIsNotNone(ledger.setting(RETIRED + stale))
        finally: ledger.db.close()

    @isolated_profiles
    def test_same_chat_reconnect_keeps_its_own_agent(self):
        code, first, text = self.connect('jekman', '--exclusive-role')
        self.assertEqual(code, 0, text)
        for flag in ('--exclusive-role', '--supersede'):
            code, again, text = self.connect('jekman', flag)
            self.assertEqual(code, 0, text)
            self.assertEqual(again['data']['agent_id'], first['data']['agent_id'])
        self.assertNotIn('agent_leave', self.service.ops())

    @isolated_profiles
    def test_already_ambiguous_role_still_registers(self):
        self.service.add('someone-else', 'jekman')
        self.service.add(Service.ME, 'jekman')
        code, result, text = self.connect('jekman', '--exclusive-role')
        self.assertEqual(code, 0, text)
        self.assertNotIn('agent_leave', self.service.ops())

    @isolated_profiles
    def test_without_the_option_connect_is_unchanged(self):
        self.service.add('someone-else', 'jekman')
        code, result, text = self.connect('jekman')
        self.assertEqual(code, 0, text)
        self.assertNotIn('resolve_role', self.service.ops())

    def test_options_are_mutually_exclusive(self):
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            self.connect('jekman', '--exclusive-role', '--supersede')


class InvitationRoleTests(unittest.TestCase):
    class Api:
        """An older service by default (no role arguments); `new=True` stores and echoes the assignment."""
        def __init__(self, new=False): self.calls, self.payloads, self.new = [], [], new
        def call(self, operation, payload, request_id=None):
            from quorum_api import QuorumError
            self.calls.append(operation)
            if operation == 'quorum_lookup': return {'quorum_id': 'q'}
            if operation == 'quorum_check_owner': return True
            if operation in ('invite_create_id', 'invite_create_open'):
                self.payloads.append(payload)
                assigned = {key: payload[key] for key in ('role', 'project_ref') if key in payload}
                if assigned and not self.new: raise QuorumError('service_error', missing=True)
                return {'invite_id': INVITE, 'expires_at': '2026-11-05T12:00:00Z',
                        **({'assigned': assigned or None} if self.new else {})}
            raise AssertionError(operation)

    def config(self):
        from quorum_api import ServiceConfig
        return ServiceConfig('https://example.com', 'sb_publishable_synthetic')

    @isolated_profiles
    def test_role_and_project_are_a_binding_instruction_in_every_prompt(self):
        from quorum_invitations import create_id_invitation, create_open_invitation
        github = create_id_invitation(self.Api(), self.config(), 'team', '123', 'r', role='jekman',
                                      project_ref='velvil')
        opened = create_open_invitation(self.Api(), self.config(), 'team', 'r', role='jekman', project_ref='velvil')
        for result, keys in ((github, ('session_prompt', 'configured_machine_prompt')), (opened, ('session_prompt',))):
            self.assertEqual(result['assigned'], {'role': 'jekman', 'project_ref': 'velvil'})
            for key in keys:
                prompt = result[key]
                self.assertIn("--role jekman --project-ref 'velvil' --exclusive-role", prompt)
                self.assertIn('never choose, derive or append another role', prompt)
                self.assertIn('role_held_by_other_identity', prompt)

    @isolated_profiles
    def test_role_without_project_uses_the_quorum_slug(self):
        from quorum_invitations import create_id_invitation
        result = create_id_invitation(self.Api(), self.config(), 'team', '123', 'r', role='jekman')
        self.assertEqual(result['assigned'], {'role': 'jekman', 'project_ref': 'team'})
        self.assertIn("--role jekman --project-ref 'team'", result['session_prompt'])

    @isolated_profiles
    def test_normal_project_references_render_single_quoted(self):
        from quorum_invitations import create_id_invitation
        for project_ref in ('velvil', 'rotnov/skills', 'team.v2_main', 'example.com:rotnov/skills', 'me@host/x-y'):
            result = create_id_invitation(self.Api(), self.config(), 'team', '123', 'r', role='jekman',
                                          project_ref=project_ref)
            self.assertEqual(result['assigned']['project_ref'], project_ref)
            self.assertIn(f"--project-ref '{project_ref}' --exclusive-role", result['session_prompt'])
        only = create_id_invitation(self.Api(), self.config(), 'team', '123', 'r', project_ref='rotnov/skills')
        self.assertIn("Assigned shared project 'rotnov/skills'", only['session_prompt'])
        self.assertIn("--role <role> --project-ref 'rotnov/skills' --accept-tasks-from-members", only['session_prompt'])
        self.assertNotIn('--exclusive-role', only['session_prompt'])

    @isolated_profiles
    def test_unsafe_slug_is_never_defaulted_into_the_command(self):
        from quorum_invitations import create_id_invitation
        api = self.Api()
        with self.assertRaisesRegex(ValueError, 'invalid_project_ref'):
            create_id_invitation(api, self.config(), 'te$(id)', '123', 'r', role='jekman')
        self.assertEqual(api.calls, [])

    @isolated_profiles
    def test_without_assignment_the_prompt_is_unchanged(self):
        from quorum_invitations import create_id_invitation
        result = create_id_invitation(self.Api(), self.config(), 'team', '123', 'r')
        self.assertNotIn('assigned', result)
        self.assertNotIn('Assigned', result['session_prompt'])
        self.assertNotIn('--exclusive-role', result['session_prompt'])

    @isolated_profiles
    def test_invalid_assignment_stops_before_any_invitation(self):
        from quorum_invitations import create_id_invitation, create_open_invitation
        for role, project_ref, code in (('Jek Man', None, 'invalid_role'), ('-x', None, 'invalid_role'),
                                        ('jekman', '', 'invalid_project_ref'),
                                        ('jekman', 'a\nb', 'invalid_project_ref'),
                                        ('jekman', 'x' * 501, 'invalid_project_ref'),
                                        # Shell expansion, command substitution, quoting and word splitting on
                                        # bash, zsh or PowerShell; a leading dash would read as an option.
                                        ('jekman', '$HOME', 'invalid_project_ref'),
                                        ('jekman', 'a`id`', 'invalid_project_ref'),
                                        ('jekman', '$(id)', 'invalid_project_ref'),
                                        ('jekman', "it's", 'invalid_project_ref'),
                                        ('jekman', 'two words', 'invalid_project_ref'),
                                        ('jekman', '-x', 'invalid_project_ref'),
                                        (None, '$HOME', 'invalid_project_ref')):
            api = self.Api()
            with self.assertRaisesRegex(ValueError, code):
                create_id_invitation(api, self.config(), 'team', '123', 'r', role=role, project_ref=project_ref)
            with self.assertRaisesRegex(ValueError, code):
                create_open_invitation(api, self.config(), 'team', 'r', role=role, project_ref=project_ref)
            self.assertEqual(api.calls, [])

    def test_cli_passes_the_assignment_and_open_help_states_server_expiry(self):
        import argparse
        from quorum_live import add_commands
        parser = argparse.ArgumentParser()
        add_commands(parser.add_subparsers(dest='command'))
        args = parser.parse_args(['invite-id', 'team', '--open', '--role', 'jekman', '--project-ref', 'velvil',
                                  '--request-id', 'r'])
        self.assertEqual((args.role, args.project_ref), ('jekman', 'velvil'))
        with contextlib.redirect_stdout(io.StringIO()) as output, self.assertRaises(SystemExit):
            parser.parse_args(['invite-id', '--help'])
        self.assertNotIn('15-minute', output.getvalue())
        self.assertIn('60 minutes', output.getvalue())


if __name__ == '__main__':
    unittest.main()
