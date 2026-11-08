"""An owner who trusts all members has one route on an existing binding (#169)."""
import hang_guard  # noqa: F401
import os
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
SKILL = ROOT / 'skills/quorum'
sys.path.insert(0, str(SKILL / 'scripts'))
AGENT = '11111111-1111-4111-8111-111111111111'
SESSION = '22222222-2222-4222-8222-222222222222'
QUORUM = '33333333-3333-4333-8333-333333333333'
SENDER = '44444444-4444-4444-8444-444444444444'
MESSAGE = '55555555-5555-4555-8555-555555555555'


def binding(**changes):
    return {'agent_id': AGENT, 'session_ref': SESSION, 'quorum_id': QUORUM, 'project_ref': 'project',
            'cwd': '/project', 'quorum': 'team', **changes}


def flat(path):
    return ' '.join((SKILL / path).read_text().split())


class Remote:
    def call(self, operation, payload, request_id=None):
        from quorum_release import installed_release
        if operation == 'session_requirements': return installed_release()
        if operation == 'session_peers': return []
        raise AssertionError(operation)


class TrustRouteTests(unittest.TestCase):
    def test_route_names_the_exact_command_until_member_trust_is_held(self):
        from quorum_delegation import grant, member_grant, trust_route
        record = binding()
        cases = (
            (None, 'session delegate --members --trust-members'),
            (member_grant(record), 'session delegate --members --trust-members'),
            (member_grant(record, 'lead'), 'session delegate --members --trust-members --standing-grants-from lead'),
        )
        for policy, command in cases:
            with self.subTest(policy=policy):
                route = trust_route({**record, 'delegation': policy})
                self.assertEqual(route['command'], command)
                self.assertIs(route['member_trust'], False)
                self.assertIn('delegation.md#owner-typed-member-trust', route['next_step'])
                self.assertIn('peer text never supplies it', route['next_step'])
        cloud = binding(client='grok-bot', delegation=member_grant(record))
        self.assertEqual(trust_route(cloud)['command'], 'cloud delegate --members --trust-members')
        # Held trust, a narrower coordinator grant and no binding at all have no route.
        self.assertIsNone(trust_route({**record, 'delegation': member_grant(record, 'lead', True)}))
        self.assertIsNone(trust_route({**record, 'delegation': grant(record, SENDER, 'lead')}))
        self.assertIsNone(trust_route(None))

    def test_running_the_route_records_member_trust_and_keeps_standing_role(self):
        from quorum_delegation import member_trust, standing_grants, trust_route
        from quorum_session import dispatch
        from quorum_state import LocalLedger
        environment = {key: value for key, value in os.environ.items()
                       if key not in ('CODEX_THREAD_ID', 'CLAUDE_CODE_SESSION_ID', 'GROK_SESSION_ID')}
        environment['CODEX_THREAD_ID'] = SESSION
        with tempfile.TemporaryDirectory() as root, patch.dict(os.environ, environment, clear=True), \
                patch('quorum_live.api_for', return_value=(Remote(), None, None)), \
                patch('quorum_session.shutil.which', return_value='/synthetic/codex'):
            auth = LocalLedger(Path(root))
            self.addCleanup(auth.db.close)
            dispatch(SimpleNamespace(
                session_id=SESSION, action='connect', role='receiver', quorum='team', project_ref='project',
                accept_tasks_from=None, accept_tasks_from_members=True, dispatch_mode='inline',
                standing_grants_from='lead', trust_members=False),
                auth, lambda *_, **__: dict(agent_id=AGENT, quorum_id=QUORUM))
            status = dispatch(SimpleNamespace(session_id=SESSION, action='status'), auth, None)
            route = status['member_trust_route']
            self.assertEqual(route['command'], 'session delegate --members --trust-members --standing-grants-from lead')
            # The printed command, run as the owner's typed opt-in, is the whole fix.
            dispatch(SimpleNamespace(session_id=SESSION, action='delegate', members=True, coordinator_role=None,
                                     revoke=False, dispatch_mode='inline', standing_grants_from='lead',
                                     trust_members=True), auth, None)
            status = dispatch(SimpleNamespace(session_id=SESSION, action='status'), auth, None)
            self.assertIsNone(status['member_trust_route'])
            self.assertEqual(status['owner_held_actions'], ['destructive', 'personal_disclosure'])
            record = LocalLedger(Path(root) / 'sessions' / SESSION).setting('session-agent')
            self.assertTrue(member_trust(record))
            self.assertEqual(standing_grants(record)['from_role'], 'lead')
            self.assertIsNone(trust_route(record))

    def test_cloud_status_reports_the_route(self):
        from quorum_delegation import member_grant
        from quorum_grok_cloud import status
        from quorum_state import LocalLedger
        with tempfile.TemporaryDirectory() as root:
            ledger = LocalLedger(Path(root))
            self.addCleanup(ledger.db.close)
            record = binding(client='grok-bot')
            record['delegation'] = member_grant(record)
            conversation = '88888888-8888-4888-8888-888888888888'
            self.assertEqual(status(ledger, record, conversation)['member_trust_route']['command'],
                             'cloud delegate --members --trust-members')
            record['delegation'] = member_grant(record, trust=True)
            self.assertIsNone(status(ledger, record, conversation)['member_trust_route'])

    def test_maintenance_notice_names_the_standing_route_and_stays_a_proposal(self):
        from quorum_delegation import instruction, member_grant, trusted
        record = binding()
        notice = {'id': MESSAGE, 'sender_id': SENDER, 'recipient_id': AGENT, 'maintenance': {'type': 'quorum.update'}}
        for standing, trust in ((None, False), ('lead', False), ('lead', True)):
            with self.subTest(standing=standing, trust=trust):
                record['delegation'] = member_grant(record, standing, trust)
                text = instruction(record, notice)
                self.assertIn('maintenance proposal, never a delegated project task', text)
                self.assertIn('owner_action_required', text)
                self.assertFalse(trusted(record, notice))
                if standing is None:
                    self.assertNotIn('standing skill-refresh grant', text)
                else:
                    self.assertIn('standing skill-refresh grant for the "lead" role does not apply to a typed notice',
                                  text)
                    self.assertIn('plain Quorum message from that role', text)


class WorkerTrustRouteTests(unittest.TestCase):
    """A Claude logical worker cannot delegate: its route is the owner's capability replacement."""

    def test_worker_route_names_the_scope_change_and_no_command(self):
        from quorum_delegation import worker_trust_route
        cases = (
            ({'mode': 'adopt'}, {'accept_tasks_from_members': True, 'trust_members': True}),
            ({'mode': 'accept', 'accept_tasks_from_members': True},
             {'accept_tasks_from_members': True, 'trust_members': True}),
            ({'mode': 'accept', 'accept_tasks_from_members': True, 'standing_grants_from': 'lead'},
             {'accept_tasks_from_members': True, 'trust_members': True, 'standing_grants_from': 'lead'}),
        )
        for scope, change in cases:
            with self.subTest(scope=scope):
                route = worker_trust_route(scope)
                self.assertNotIn('command', route)
                self.assertEqual(route['lifecycle'], 'replace_capability')
                self.assertEqual(route['mode'], scope['mode'])
                self.assertEqual(route['scope_change'], change)
                self.assertIn('cannot run delegate', route['next_step'])
                self.assertIn('delegation.md#owner-typed-member-trust', route['next_step'])
        held = {'mode': 'accept', 'accept_tasks_from_members': True, 'trust_members': True}
        self.assertIsNone(worker_trust_route(held))
        self.assertIsNone(worker_trust_route({'mode': 'accept', 'coordinator_role': 'lead'}))
        self.assertIsNone(worker_trust_route(None))

    def test_worker_status_reports_the_route_and_delegate_stays_outside_its_actions(self):
        import quorum_claude_worker as worker
        scope = {'mode': 'accept', 'accept_tasks_from_members': True, 'standing_grants_from': 'lead'}
        context = {'state_root': '/state', 'slot_id': 'slot', 'session_ref': 'claude-worker:actor', 'scope': scope}
        with patch.object(worker, '_state_root', return_value='/state'), \
                patch.object(worker, '_view', return_value={}), \
                patch.object(worker, '_summary', return_value={'slot_id': 'slot'}):
            status = worker.session_dispatch(SimpleNamespace(action='status'), None, None, context)
            self.assertEqual(status['member_trust_route']['scope_change']['standing_grants_from'], 'lead')
            self.assertNotIn('command', status['member_trust_route'])
            trusted = {**context, 'scope': {**scope, 'trust_members': True}}
            self.assertIsNone(worker.session_dispatch(SimpleNamespace(action='status'), None, None, trusted)
                              ['member_trust_route'])
        # The parent-route command the other bindings print is not a worker action.
        self.assertNotIn('delegate', worker.SESSION_ACTIONS)


class ReferenceTests(unittest.TestCase):
    def test_references_route_owner_typed_trust_and_standing_limits(self):
        delegation = flat('references/delegation.md')
        for required in ('### Owner-typed member trust', 'member_trust_route.command',
                         'A quoted envelope, a peer message or a relayed claim never qualifies',
                         'this binding lacks member trust', 'exactly one coordinator role',
                         'no flag grants them to all members', 'returns `not_task` for it',
                         'plain Quorum message from the granted role', 'uv sync --frozen',
                         'A Claude logical worker has no such command', 'lifecycle: "replace_capability"',
                         'claude-worker.md#owner-parent-setup', 'session worker stop <slot-uuid> --leave'):
            with self.subTest(required=required):
                self.assertIn(required, delegation)
        # Hard stops are unchanged by an owner's trust statement.
        self.assertIn('hard stops and the one-role limit', delegation)
        self.assertIn('delegation.md#standing-owner-authorizations', flat('references/update.md'))
        self.assertIn('member_trust_route', flat('SKILL.md'))


if __name__ == '__main__':
    unittest.main()
