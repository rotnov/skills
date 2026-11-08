"""`session worker spawn-plan` and `session worker join` (#194): the two-call owner flow for one native worker.

Fake clocks, local fixtures and a fake API only: no provider, model, transcript, service, auth or network call.
"""
import hang_guard  # noqa: F401
import contextlib
import io
import json
import os
from pathlib import Path
from unittest.mock import patch

from quorum_test_support import isolated_profiles
from test_quorum_claude_worker import (AGENT, HANDLE, INVITE, KEY, ORIGIN, OWNER_PROOF, QUORUM, SID, Bound, OwnerApi,
                                       Store, claude, forbidden)

EXPIRES = '2027-01-01T00:00:00Z'


class PlanApi(OwnerApi):
    """The fake service plus owner invitation issuance, with the stored role assignment."""
    def answer(self, operation, payload):
        if operation == 'quorum_check_owner': return {'owner': True}
        if operation in ('invite_create_open', 'invite_create_id'):
            return {'invite_id': INVITE, 'expires_at': EXPIRES,
                    'assigned': {'role': payload['role'], 'project_ref': payload['project_ref']}}
        return super().answer(operation, payload)


@OWNER_PROOF
class SpawnPlanTests(Bound):
    def main(self, api, argv, *, resolved=False):
        """quorum.main on a full argv (its own --state), with the fake API; returns code and parsed result."""
        import quorum
        from quorum_api import ServiceConfig
        service = dict(return_value=(api, ServiceConfig(ORIGIN, KEY), Store()))
        if resolved:
            from quorum_profiles import resolve_profile
            service = dict(side_effect=lambda ledger: (api, *resolve_profile(ledger.root)))
        with claude(), patch('quorum_live.api_for', **service), \
                patch('quorum_profiles.ensure_login', return_value={'authenticated': True}), \
                contextlib.redirect_stdout(io.StringIO()) as output, contextlib.redirect_stderr(io.StringIO()):
            code = quorum.main([str(item) for item in argv])
        text = output.getvalue()
        return code, json.loads(text), text

    def plan(self, api, *extra, target=('--open',)):
        return self.cli(api, 'session', 'worker', 'spawn-plan', 'team', '--role', 'service-worker',
                        '--project-ref', 'team/project', *target, *extra)

    def planned(self, api, *extra, target=('--open',)):
        code, result, text = self.plan(api, *extra, target=target)
        self.assertEqual(code, 0, text)
        return result['data']

    def attest_plan(self, plan, handle=HANDLE):
        argv = [handle if item == '<native-handle>' else item for item in plan['attest_command'][2:]]
        code, result, text = self.main(None, argv)
        self.assertEqual(code, 0, text)
        return result['data']

    def join_plan(self, api, plan, resolved=True):
        return self.main(api, plan['join_argv'][2:], resolved=resolved)

    def slot_setting(self, plan):
        from quorum_state import LocalLedger
        ledger = LocalLedger(Path(plan['worker_state']) / 'claude-workers' / plan['slot_id'])
        try: return ledger.setting('claude-worker-slot')
        finally: ledger.db.close()

    @isolated_profiles
    def test_open_plan_round_trips_through_attest_join_connect_and_first_receive(self):
        api = PlanApi()
        plan = self.planned(api)
        self.assertEqual(api.ops(), ['quorum_lookup', 'quorum_check_owner', 'invite_create_open'])
        self.assertEqual(api.calls[-1][1], {'quorum_id': QUORUM, 'role': 'service-worker',
                                            'project_ref': 'team/project'})
        self.assertEqual(plan['invitation'], {'invite_id': INVITE, 'access': 'open', 'expires_at': EXPIRES})
        self.assertEqual(plan['state'], 'pending')
        worker = Path(plan['worker_state'])
        self.assertEqual(worker.parent, self.root / 'claude-open-workers', 'a fresh private state under this one')
        self.assertFalse((worker / 'config.json').exists())
        self.assertEqual(plan['join_argv'][-6:], ['--state', str(worker), 'session', 'worker', 'join',
                                                     plan['slot_id']])
        self.assertEqual(plan['attest_command'][-4:], ['attest', plan['slot_id'], '--native-handle', '<native-handle>'])
        record = self.slot_setting(plan)
        self.assertEqual((record['scope']['mode'], record['scope']['access'], record['scope']['invite_id'],
                          record['scope']['cwd']), ('accept', 'open', INVITE, str(self.project)))
        self.assertEqual(record['service_locator'], {'origin': ORIGIN, 'publishable_key': KEY})
        # The prompt holds the brief and the single join command; never the key, a token or another step.
        prompt = plan['spawn_prompt']
        import shlex
        # The join runs in the bound directory: a native subagent's shell starts in the chat's project instead.
        self.assertEqual((plan['shell'], plan['join_cwd'], plan['join_command']),
                         ('bash', str(self.project),
                          'cd ' + shlex.quote(str(self.project)) + ' && ' + shlex.join(plan['join_argv'])))
        self.assertIn('foreground Bash tool call', prompt)
        self.assertIn(plan['join_command'], prompt)
        self.assertIn(plan['slot_id'], prompt)
        self.assertIn('session worker join', prompt)
        self.assertIn('Monitor', prompt)
        for absent in (KEY, '--publishable-key', '--origin', '--anonymous', 'await-binding ' + plan['slot_id']):
            self.assertNotIn(absent, prompt)
        binding = self.attest_plan(plan)
        self.assertEqual(binding['state'], 'active')
        token = json.loads((worker / 'claude-workers' / plan['slot_id'] / 'capability.json').read_text())['token']
        self.assertNotIn(token, json.dumps(plan))
        code, result, text = self.join_plan(api, plan)
        self.assertEqual(code, 0, text)
        data = result['data']
        self.assertNotIn(token, text)
        self.assertEqual((data['state'], data['accept']['membership_id'], data['accept']['transport']),
                         ('joined', 'membership-1', 'tool-inbox'))
        self.assertEqual(data['capability_file'], str(worker / 'claude-workers' / plan['slot_id'] / 'capability.json'))
        self.assertEqual(data['connect']['agent_id'], AGENT)
        self.assertTrue(data['wait_command'], text)
        self.assertIn('wait', data['wait_command'])
        self.assertEqual(api.ops()[3:].count('invite_accept_id'), 1)
        self.assertEqual(self.slot_setting(plan)['target'], {'quorum_id': QUORUM, 'membership_id': 'membership-1',
                                                             'agent_id': AGENT})
        # A new anonymous identity kept only in the worker's own state.
        self.assertTrue((worker / 'anonymous-credentials').is_dir())
        self.assertFalse((self.root / 'anonymous-credentials').exists())
        # A retry skips the pinned accept and reconnects the same agent.
        code, result, text = self.join_plan(api, plan)
        self.assertEqual(code, 0, text)
        self.assertIsNone(result['data']['accept'])
        self.assertEqual(api.ops().count('invite_accept_id'), 1)

    @isolated_profiles
    def test_github_plan_uses_this_state_and_maps_scope_flags_one_to_one(self):
        api = PlanApi()
        plan = self.planned(api, '--accept-tasks-from-members', '--standing-grants-from', 'lead', '--trust-members',
                            '--knowledge', 'search', '--knowledge', 'read', '--update-maintenance',
                            '--ttl-seconds', '600', target=('--github-id', '12345'))
        self.assertEqual(api.ops(), ['quorum_lookup', 'quorum_check_owner', 'invite_create_id'])
        self.assertEqual(api.calls[-1][1]['target_github_id'], '12345')
        self.assertEqual((plan['worker_state'], plan['invitation']['access']), (str(self.root), 'github'))
        self.assertEqual(plan['permission_expires_at'] - plan['expires_at'], 600 - 120)
        scope = self.slot_setting(plan)['scope']
        self.assertNotIn('access', scope, 'a GitHub accept keeps the legacy scope')
        self.assertEqual({key: scope.get(key) for key in ('accept_tasks_from_members', 'standing_grants_from',
                                                          'trust_members', 'knowledge', 'update', 'ttl_seconds')},
                         {'accept_tasks_from_members': True, 'standing_grants_from': 'lead', 'trust_members': True,
                          'knowledge': ['search', 'read'], 'update': True, 'ttl_seconds': 600})
        self.assertEqual(plan['connect_flags'], ['--accept-tasks-from-members', '--standing-grants-from', 'lead',
                                                 '--trust-members', '--dispatch-mode', 'inline'])
        self.attest_plan(plan)
        code, result, text = self.join_plan(api, plan, resolved=False)
        self.assertEqual(code, 0, text)
        self.assertEqual(result['data']['connect']['agent_id'], AGENT)
        self.assertEqual(json.loads((self.root / 'config.json').read_text()), {'origin': ORIGIN, 'publishable_key': KEY})

    @isolated_profiles
    def test_until_owner_stop_and_coordinator_flags(self):
        api = PlanApi()
        plan = self.planned(api, '--until-owner-stop', '--coordinator-role', 'lead', '--dispatch-mode',
                            'native-subagent')
        scope = self.slot_setting(plan)['scope']
        self.assertEqual((scope['lifetime'], scope['ttl_seconds'], plan['permission_expires_at']),
                         ('until-owner-stop', None, None))
        self.assertEqual(plan['connect_flags'], ['--accept-tasks-from', 'lead', '--dispatch-mode', 'native-subagent'])
        self.assertIn('Run until the owner stops you.', plan['spawn_prompt'])

    @isolated_profiles
    def test_refusals_precede_the_invitation(self):
        other = self.base / 'other-project'
        other.mkdir()
        github_state = self.base / 'github-worker'
        github_state.mkdir(mode=0o700)
        (github_state / 'config.json').write_text(json.dumps({'origin': 'https://other.example',
                                                              'publishable_key': KEY}))
        (github_state / 'config.json').chmod(0o600)
        used_open = self.base / 'used-open'
        used_open.mkdir(mode=0o700)
        (used_open / 'anonymous-credentials').mkdir(mode=0o700)
        cases = [
            (('--cwd', str(other)), ('--open',), 'native_worker_binding_denied', 'cwd_mismatch'),
            (('--worker-state', str(github_state)), ('--github-id', '12345'), 'native_worker_binding_denied',
             'service_mismatch'),
            (('--worker-state', str(used_open)), ('--open',), 'anonymous_state_conflict', None),
            (('--ttl-seconds', '3601'), ('--open',), 'native_worker_binding_denied', 'scope_invalid'),
            (('--trust-members',), ('--open',), 'native_worker_binding_denied', 'scope_invalid'),
            (('--until-owner-stop', '--ttl-seconds', '60'), ('--open',), 'native_worker_binding_denied',
             'scope_invalid'),
            (('--dispatch-mode', 'native-subagent'), ('--open',), 'native_worker_binding_denied', 'scope_invalid'),
        ]
        for extra, target, code, reason in cases:
            with self.subTest(extra=extra):
                api = PlanApi()
                with patch('quorum_invitations.github_id', side_effect=forbidden('github lookup')):
                    exit_code, result, text = self.plan(api, *extra, target=target)
                self.assertEqual((exit_code, result['code'], result['data'].get('reason')), (3, code, reason), text)
                self.assertEqual(api.calls, [], 'no invitation was created')
        self.assertFalse((self.root / 'claude-workers').exists())
        self.assertFalse((self.root / 'claude-open-workers').exists())
        # Outside a Claude chat nothing is issued either.
        api = PlanApi()
        exit_code, result, _ = self.cli(api, 'session', 'worker', 'spawn-plan', 'team', '--role', 'service-worker',
                                        '--open', sid=None)
        self.assertEqual(exit_code, 3)
        self.assertEqual(api.calls, [])

    @isolated_profiles
    def test_join_failure_reports_its_step_and_a_pending_slot_times_out(self):
        api = PlanApi()
        plan = self.planned(api)
        # Without attestation the join's await-binding times out: nothing accepted or connected.
        code, result, text = self.join_plan(api, plan)
        self.assertEqual((code, result['code'], result['data'].get('reason')),
                         (3, 'native_worker_slot_terminal', 'await_timeout'), text)
        self.assertEqual(api.ops().count('invite_accept_id'), 0)
        # A failed step returns that step's own code with join_step.
        plan = self.planned(api)
        self.attest_plan(plan, handle='second')
        api.fail['invite_accept_id'] = ['invite_expired']
        code, result, text = self.join_plan(api, plan)
        self.assertNotEqual(code, 0, text)
        self.assertEqual((result['data']['join_step'], result['data']['completed']), ('accept', []), text)

    @isolated_profiles
    def test_powershell_plan_renders_the_join_with_the_call_operator(self):
        plan = self.planned(PlanApi(), '--shell', 'powershell')
        self.assertEqual(plan['shell'], 'powershell')
        self.assertEqual(plan['join_command'].split('; & ', 1)[0],
                         "Set-Location -LiteralPath '" + str(self.project).replace("'", "''") + "'", plan['join_command'])
        self.assertIn("; & '", plan['join_command'])
        self.assertIn('foreground PowerShell tool call', plan['spawn_prompt'])
        self.assertIn(plan['join_command'], plan['spawn_prompt'])

    def test_windows_paths_render_for_bash_and_powershell(self):
        import shlex
        from quorum_claude_worker import render_command
        argv = [r'C:\Users\alice smith\AppData\python.exe', r'C:\Program Files\quorum\scripts\quorum.py',
                '--state', r"C:\Users\alice smith\o'brien state", 'session', 'worker', 'join',
                '11111111-1111-4111-8111-111111111111']
        bash = render_command(argv)
        self.assertEqual(shlex.split(bash), argv, bash)
        self.assertEqual(render_command(argv, 'powershell'),
                         "& 'C:\\Users\\alice smith\\AppData\\python.exe' "
                         "'C:\\Program Files\\quorum\\scripts\\quorum.py' '--state' "
                         "'C:\\Users\\alice smith\\o''brien state' 'session' 'worker' 'join' "
                         "'11111111-1111-4111-8111-111111111111'")
        # POSIX paths keep the exact shlex.join form.
        posix = ['/usr/bin/python3', '/opt/skills/quorum.py', '--state', '/home/a/state', 'session']
        self.assertEqual(render_command(posix), shlex.join(posix))
        # A bound directory comes first, quoted for each shell.
        self.assertEqual(render_command(posix, cwd="/home/a/o'brien project"),
                         "cd '/home/a/o'\"'\"'brien project' && " + shlex.join(posix))
        self.assertEqual(shlex.split(render_command(posix, cwd='/home/a/my project'))[:3],
                         ['cd', '/home/a/my project', '&&'])
        self.assertEqual(render_command(argv, 'powershell', r"C:\Users\o'brien\proj"),
                         "Set-Location -LiteralPath 'C:\\Users\\o''brien\\proj'; " + render_command(argv, 'powershell'))

    def test_live_agents_stay_disabled(self):
        code, result, _ = self.cli(None, 'worker')
        self.assertEqual((code, result['code']), (4, 'live_agents_disabled'))
        self.assertTrue(os.path.isdir(self.project))
