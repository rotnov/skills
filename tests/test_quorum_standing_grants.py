"""Standing owner grants: invite wording, coordinator-role check, daily cap and old-binding default."""
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
MESSAGES = ['55555555-5555-4555-8555-55555555555' + str(index) for index in range(6)]
OTHER = '66666666-6666-4666-8666-666666666666'
INVITE = '77777777-7777-4777-8777-777777777777'


def binding():
    return dict(agent_id=AGENT, session_ref=SESSION, quorum_id=QUORUM,
                project_ref='project', cwd=str(Path.cwd().resolve()))


class Api:
    """Fresh member authority for the addressed message plus one server role resolution."""
    def __init__(self, coordinator=SENDER, sender=SENDER, role_error=None):
        self.coordinator, self.sender, self.role_error, self.calls = coordinator, sender, role_error, []

    def call(self, operation, payload, request_id=None):
        self.calls.append((operation, payload))
        if operation == 'session_task_authority':
            return dict(authorized=True, reason='current_member', message_id=payload['message_id'],
                        agent_id=AGENT, session_ref=SESSION, quorum_id=QUORUM, project_ref='project',
                        sender_id=self.sender)
        if operation == 'resolve_role':
            if self.role_error:
                from quorum_api import QuorumError
                raise QuorumError(self.role_error)
            return {'agent_id': self.coordinator}
        raise AssertionError(operation)


class InviteWordingTests(unittest.TestCase):
    def issue(self, **options):
        from quorum_api import ServiceConfig
        from quorum_invitations import create_id_invitation, create_open_invitation
        class Owner:
            def call(self, operation, payload, request_id=None):
                if operation == 'quorum_lookup': return {'quorum_id': 'q'}
                if operation == 'quorum_check_owner': return True
                return {'invite_id': INVITE, 'expires_at': '2026-11-03T12:00:00Z'}
        config = ServiceConfig('https://example.com', 'sb_publishable_synthetic')
        github = create_id_invitation(Owner(), config, 'team', '123', 'r', coordinator_role='lead', **options)
        anonymous = create_open_invitation(Owner(), config, 'team', 'r', coordinator_role='lead', **options)
        return github, [github['session_prompt'], github['configured_machine_prompt'], anonymous['session_prompt']]

    @isolated_profiles
    def test_invitations_carry_bounded_standing_grants_by_default(self):
        github, prompts = self.issue()
        self.assertTrue(github['standing_grants'])
        for prompt in prompts:
            for required in ('--standing-grants-from lead', 'The "lead" role may also ask for',
                             'one skill refresh per request', 'up to 3 local client test sessions per UTC day',
                             'references/delegation.md',
                             # The member grant and the hard stops still bound everything else.
                             '--accept-tasks-from-members --dispatch-mode inline', 'Hard stops'):
                self.assertIn(required, prompt)
            for procedure in ('session connect', 'session start', 'native-inbox.md', 'claude-worker.md'):
                self.assertNotIn(procedure, prompt)

    @isolated_profiles
    def test_owner_opt_out_removes_every_standing_grant(self):
        github, prompts = self.issue(standing_grants=False)
        self.assertFalse(github['standing_grants'])
        for prompt in prompts:
            for absent in ('Standing grants', '--standing-grants-from', 'standing-check', 'daily cap',
                           'background-worker route', 'may also ask for', 'client test'):
                self.assertNotIn(absent, prompt)
            self.assertIn('--accept-tasks-from-members --dispatch-mode inline', prompt)
            self.assertLess(len(prompt), 2300)

    def test_invite_id_flag_defaults_to_granted(self):
        import argparse
        import quorum_live
        parser = argparse.ArgumentParser()
        quorum_live.add_commands(parser.add_subparsers(dest='command'))
        granted = parser.parse_args(['invite-id', 'team', '--open', '--request-id', 'r'])
        withheld = parser.parse_args(['invite-id', 'team', '--open', '--request-id', 'r', '--no-standing-grants'])
        self.assertTrue(granted.standing_grants)
        self.assertFalse(withheld.standing_grants)

    def test_invalid_coordinator_role_is_refused(self):
        from quorum_invitations import authorizations
        from quorum_delegation import standing_grant
        for role in ('Lead', '', 'lead role', 'x' * 49):
            with self.assertRaisesRegex(ValueError, 'invalid_coordinator_role'):
                authorizations(role)
            with self.assertRaisesRegex(ValueError, 'invalid_coordinator_role'):
                standing_grant(role)


class StandingCheckTests(unittest.TestCase):
    def setUp(self):
        from quorum_delegation import member_grant
        from quorum_state import LocalLedger
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.ledger = LocalLedger(Path(self.temporary.name) / 'sessions' / SESSION)
        self.addCleanup(self.ledger.db.close)
        self.record = binding()
        self.record['delegation'] = member_grant(self.record, 'lead')

    def check(self, api, message, action='client-test', day='2026-11-03', record=None):
        from quorum_delegation import standing_check
        record = record or self.record
        return standing_check(api, record, message, action, lambda: record, self.ledger, day=day)

    def test_grant_is_persisted_inside_the_member_scope(self):
        from quorum_delegation import matches, standing_grants
        self.assertEqual(self.record['delegation']['standing_grants'],
                         {'from_role': 'lead', 'actions': ['skill-refresh', 'client-test'],
                          'client_tests_per_day': 3})
        self.assertEqual(standing_grants(self.record)['from_role'], 'lead')
        self.assertTrue(matches(self.record, dict(sender_id=SENDER, recipient_id=AGENT)))

    def test_only_the_named_coordinator_role_is_authorized(self):
        api = Api()
        result = self.check(api, MESSAGES[0])
        self.assertTrue(result['authorized'])
        self.assertEqual((result['sender_role'], result['sender_id']), ('lead', SENDER))
        self.assertIn('merge_release_deploy', result['owner_held_actions'])
        resolved = [payload for operation, payload in api.calls if operation == 'resolve_role']
        self.assertEqual(resolved, [{'quorum_id': QUORUM, 'role': 'lead', 'project_ref': 'project'}])
        # A current member that does not hold the coordinator role stays a proposal.
        denied = self.check(Api(coordinator=OTHER), MESSAGES[1])
        self.assertEqual((denied['authorized'], denied['reason']), (False, 'sender_not_coordinator'))
        for code in ('role_not_found', 'role_ambiguous'):
            denied = self.check(Api(role_error=code), MESSAGES[1])
            self.assertEqual(denied['reason'], 'sender_not_coordinator')
        self.assertEqual(self.check(Api(role_error='service_unavailable'), MESSAGES[1])['reason'],
                         'authority_unavailable')
        # Failed checks never consume the daily budget.
        from quorum_delegation import standing_status
        self.assertEqual(standing_status(self.record, self.ledger, day='2026-11-03')['client_tests_used_today'], 1)

    def test_grant_revoked_during_role_resolution_is_not_authorized(self):
        from quorum_delegation import standing_check
        current = dict(self.record)
        api = Api()
        original = api.call
        def call(operation, payload, request_id=None):
            if operation == 'resolve_role': current['delegation'] = None
            return original(operation, payload)
        api.call = call
        result = standing_check(api, self.record, MESSAGES[0], 'skill-refresh', lambda: current, self.ledger)
        self.assertEqual((result['authorized'], result['reason']), (False, 'delegation_changed'))

    def test_daily_cap_is_three_sessions_per_binding_and_resets_next_day(self):
        from quorum_delegation import standing_status
        from quorum_state import LocalLedger
        api = Api()
        for index, message in enumerate(MESSAGES[:3]):
            result = self.check(api, message)
            self.assertTrue(result['authorized'])
            self.assertEqual(result['client_tests_remaining_today'], 2 - index)
        capped = self.check(api, MESSAGES[3])
        self.assertEqual((capped['authorized'], capped['reason']), (False, 'daily_cap_reached'))
        self.assertEqual(capped['client_tests_used_today'], 3)
        # The counter is persisted in this binding's private ledger, not in process memory.
        reopened = LocalLedger(self.ledger.root)
        self.addCleanup(reopened.db.close)
        self.assertEqual(standing_status(self.record, reopened, day='2026-11-03')['client_tests_remaining_today'], 0)
        tomorrow = self.check(api, MESSAGES[4], day='2026-11-04')
        self.assertTrue(tomorrow['authorized'])
        self.assertEqual(tomorrow['client_tests_used_today'], 1)
        # Only the current day's counter is kept.
        keys = [key for (key,) in self.ledger.db.execute("select key from settings where key like 'standing-client%'")]
        self.assertEqual(keys, ['standing-client-tests:2026-11-04'])

    def test_skill_refresh_runs_once_per_request_and_skips_the_test_budget(self):
        api = Api()
        self.assertTrue(self.check(api, MESSAGES[0], action='skill-refresh')['authorized'])
        again = self.check(api, MESSAGES[0], action='skill-refresh')
        self.assertEqual((again['authorized'], again['reason']), (False, 'request_already_used'))
        self.assertTrue(self.check(api, MESSAGES[1], action='skill-refresh')['authorized'])
        from quorum_delegation import standing_status
        self.assertEqual(standing_status(self.record, self.ledger, day='2026-11-03')['client_tests_used_today'], 0)
        with self.assertRaisesRegex(ValueError, 'invalid_standing_action'):
            self.check(api, MESSAGES[2], action='merge')

    def test_old_bindings_have_no_grant_and_behave_as_before(self):
        from quorum_delegation import grant, instruction, member_grant, standing_grants, standing_status
        def forbidden(*_): self.fail('no RPC without a persisted standing grant')
        legacy = binding()
        legacy['delegation'] = member_grant(legacy)
        self.assertEqual(legacy['delegation'], {**binding(), 'scope': 'members', 'dispatch_mode': 'inline'})
        coordinator = {**binding(), 'delegation': grant(binding(), SENDER, 'lead')}
        tampered = {**binding(), 'delegation': {**legacy['delegation'],
                    'standing_grants': {'from_role': 'lead', 'actions': ['skill-refresh', 'client-test'],
                                        'client_tests_per_day': 99}}}
        for record in (legacy, coordinator, binding(), tampered):
            with self.subTest(record=record.get('delegation')):
                self.assertIsNone(standing_grants(record))
                self.assertIsNone(standing_status(record, self.ledger))
                result = self.check(SimpleNamespace(call=forbidden), MESSAGES[0], record=record)
                self.assertEqual((result['authorized'], result['reason']), (False, 'standing_grant_absent'))
        message = dict(id=MESSAGES[0], sender_id=SENDER, recipient_id=AGENT)
        self.assertNotIn('standing-check', instruction(legacy, message))
        self.assertIn('session standing-check ' + MESSAGES[0], instruction(self.record, message))


class StandingCommandTests(unittest.TestCase):
    def test_connect_persists_status_exposes_and_reconnect_never_widens(self):
        from quorum_release import installed_release
        from quorum_session import dispatch
        from quorum_state import LocalLedger
        class Remote(Api):
            def call(self, operation, payload, request_id=None):
                if operation == 'session_requirements': return installed_release()
                if operation == 'session_peers': return []
                return super().call(operation, payload)
        api = Remote()
        environment = {key: value for key, value in os.environ.items()
                       if key not in ('CODEX_THREAD_ID', 'CLAUDE_CODE_SESSION_ID', 'GROK_SESSION_ID')}
        environment['CODEX_THREAD_ID'] = SESSION
        with tempfile.TemporaryDirectory() as root, patch.dict(os.environ, environment, clear=True), \
                patch('quorum_live.api_for', return_value=(api, None, None)), \
                patch('quorum_session.shutil.which', return_value='/synthetic/codex'):
            auth = LocalLedger(Path(root))
            self.addCleanup(auth.db.close)
            def register(*_, **__): return dict(agent_id=AGENT, quorum_id=QUORUM)
            def connect(**changes):
                return dispatch(SimpleNamespace(**{**dict(
                    session_id=SESSION, action='connect', role='receiver', quorum='team', project_ref='project',
                    accept_tasks_from=None, accept_tasks_from_members=True, dispatch_mode='inline',
                    standing_grants_from=None), **changes}), auth, register)
            def status():
                return dispatch(SimpleNamespace(session_id=SESSION, action='status'), auth, register)
            def delegate(**changes):
                return dispatch(SimpleNamespace(**{**dict(session_id=SESSION, action='delegate', revoke=False,
                                members=True, coordinator_role=None, standing_grants_from=None), **changes}),
                                auth, register)
            with self.assertRaisesRegex(ValueError, 'standing_grants_require_members'):
                connect(accept_tasks_from_members=False, standing_grants_from='lead')
            # An old-style connect holds no standing grant and status says so.
            connect()
            self.assertIsNone(status()['standing_grants'])
            check = SimpleNamespace(session_id=SESSION, action='standing-check', message_id=MESSAGES[0],
                                    standing_action='skill-refresh')
            self.assertEqual(dispatch(check, auth, register)['reason'], 'standing_grant_absent')
            # A reconnect cannot add one; only the receiving owner's explicit delegate can.
            with self.assertRaisesRegex(ValueError, 'delegation_scope_changed'):
                connect(standing_grants_from='lead')
            self.assertIsNone(status()['standing_grants'])
            granted = delegate(standing_grants_from='lead')
            self.assertEqual(granted['delegation']['standing_grants']['from_role'], 'lead')
            shown = status()['standing_grants']
            self.assertEqual((shown['from_role'], shown['client_tests_per_day'],
                              shown['client_tests_remaining_today']), ('lead', 3, 3))
            # Reconnecting without the flag keeps the grant; a different role is refused.
            connect()
            self.assertEqual(status()['standing_grants']['from_role'], 'lead')
            with self.assertRaisesRegex(ValueError, 'delegation_scope_changed'):
                connect(standing_grants_from='other')
            self.assertTrue(dispatch(check, auth, register)['authorized'])
            self.assertEqual(dispatch(check, auth, register)['reason'], 'request_already_used')
            check.standing_action, check.message_id = 'client-test', MESSAGES[1]
            self.assertTrue(dispatch(check, auth, register)['authorized'])
            self.assertEqual(status()['standing_grants']['client_tests_remaining_today'], 2)
            # Re-delegating members without the flag withdraws the standing grant.
            self.assertNotIn('standing_grants', delegate()['delegation'])
            self.assertIsNone(status()['standing_grants'])

    def test_skill_refresh_recovers_a_stale_binding_but_client_tests_stay_release_gated(self):
        from quorum_release import ReleaseMismatch, installed_release
        from quorum_session import dispatch
        from quorum_state import LocalLedger
        self.assertEqual(installed_release()['protocol_version'], 2)
        class Remote(Api):
            required = installed_release()
            def call(self, operation, payload, request_id=None):
                if operation == 'session_requirements':
                    self.calls.append((operation, payload))
                    return self.required
                return super().call(operation, payload)
        api = Remote()
        environment = {key: value for key, value in os.environ.items()
                       if key not in ('CODEX_THREAD_ID', 'CLAUDE_CODE_SESSION_ID', 'GROK_SESSION_ID')}
        environment['CODEX_THREAD_ID'] = SESSION
        with tempfile.TemporaryDirectory() as root, patch.dict(os.environ, environment, clear=True), \
                patch('quorum_live.api_for', return_value=(api, None, None)), \
                patch('quorum_session.shutil.which', return_value='/synthetic/codex'):
            auth = LocalLedger(Path(root))
            self.addCleanup(auth.db.close)
            register = lambda *_, **__: dict(agent_id=AGENT, quorum_id=QUORUM)
            dispatch(SimpleNamespace(session_id=SESSION, action='connect', role='receiver', quorum='team',
                                     project_ref='project', accept_tasks_from=None, accept_tasks_from_members=True,
                                     dispatch_mode='inline', standing_grants_from='lead'), auth, register)
            api.required = {'protocol_version': 3}
            def check(message, action='skill-refresh'):
                return dispatch(SimpleNamespace(session_id=SESSION, action='standing-check', message_id=message,
                                                standing_action=action), auth, register)
            # A current member that is not the coordinator is still refused under the mismatch.
            api.coordinator = OTHER
            api.calls.clear()
            denied = check(MESSAGES[0])
            self.assertEqual((denied['authorized'], denied['reason']), (False, 'sender_not_coordinator'))
            self.assertEqual(denied['release']['code'], 'skill_update_required')
            self.assertEqual([op for op, _ in api.calls],
                             ['session_requirements', 'session_task_authority', 'resolve_role'])
            api.coordinator = SENDER
            granted = check(MESSAGES[0])
            self.assertTrue(granted['authorized'])
            self.assertEqual(granted['sender_id'], SENDER)
            self.assertEqual(granted['release']['required'], {'protocol_version': 3})
            # The once-per-message marker still applies.
            self.assertEqual(check(MESSAGES[0])['reason'], 'request_already_used')
            api.calls.clear()
            with self.assertRaisesRegex(ReleaseMismatch, 'skill_update_required'):
                check(MESSAGES[1], action='client-test')
            self.assertEqual([op for op, _ in api.calls], ['session_requirements'])

    def test_first_connect_with_flag_persists_the_grant(self):
        from quorum_release import installed_release
        from quorum_session import dispatch
        from quorum_state import LocalLedger
        class Remote(Api):
            def call(self, operation, payload, request_id=None):
                if operation == 'session_requirements': return installed_release()
                return super().call(operation, payload)
        environment = {key: value for key, value in os.environ.items()
                       if key not in ('CODEX_THREAD_ID', 'CLAUDE_CODE_SESSION_ID', 'GROK_SESSION_ID')}
        environment['CODEX_THREAD_ID'] = SESSION
        with tempfile.TemporaryDirectory() as root, patch.dict(os.environ, environment, clear=True), \
                patch('quorum_live.api_for', return_value=(Remote(), None, None)), \
                patch('quorum_session.shutil.which', return_value='/synthetic/codex'):
            auth = LocalLedger(Path(root))
            self.addCleanup(auth.db.close)
            result = dispatch(SimpleNamespace(session_id=SESSION, action='connect', role='receiver', quorum='team',
                                              project_ref='project', accept_tasks_from=None,
                                              accept_tasks_from_members=True, dispatch_mode='inline',
                                              standing_grants_from='lead'),
                              auth, lambda *_, **__: dict(agent_id=AGENT, quorum_id=QUORUM))
            self.assertEqual(result['delegation']['standing_grants']['from_role'], 'lead')
            session = LocalLedger(Path(root) / 'sessions' / SESSION)
            self.addCleanup(session.db.close)
            self.assertEqual(session.setting('session-agent')['delegation']['standing_grants']['client_tests_per_day'], 3)


if __name__ == '__main__':
    unittest.main()
