"""#141: every cause of a pending task-check keeps its own reason; synthetic bindings and RPC boundaries only."""
import ast
import copy
import json
import ntpath
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

from quorum_api import CHECK_REASONS, QuorumError
import quorum_delegation
from quorum_delegation import (NEXT_STEPS, carried_grant, delegation_state, grant, member_grant, standing_check,
                               task_check)

AGENT = '11111111-1111-4111-8111-111111111111'
SESSION = '22222222-2222-4222-8222-222222222222'
QUORUM = '33333333-3333-4333-8333-333333333333'
SENDER = '44444444-4444-4444-8444-444444444444'
MESSAGE = '55555555-5555-4555-8555-555555555555'
OTHER_AGENT = '66666666-6666-4666-8666-666666666666'


def member(**options):
    record = dict(agent_id=AGENT, session_ref=SESSION, quorum_id=QUORUM, project_ref='project',
                  cwd=str(Path.cwd().resolve()))
    record['delegation'] = member_grant(record, **options)
    return record


def authority(**changes):
    return {**dict(authorized=True, reason='current_member', message_id=MESSAGE, agent_id=AGENT, session_ref=SESSION,
                   quorum_id=QUORUM, project_ref='project', sender_id=SENDER), **changes}


def without_grant(record): return {key: value for key, value in record.items() if key != 'delegation'}
def coordinator(record): return {**record, 'delegation': grant(record, SENDER, 'coordinator')}
def elsewhere(record): return {**record, 'cwd': '/synthetic/bound', 'delegation': {**record['delegation'],
                                                                                   'cwd': '/synthetic/bound'}}
def stale_pin(record): return {**record, 'delegation': {**record['delegation'], 'agent_id': OTHER_AGENT}}


def api(result=None, error=None):
    def call(*_):
        if error is not None: raise error
        return authority() if result is None else result
    return SimpleNamespace(call=call)


class LocalCauseTable(unittest.TestCase):
    """Before any RPC: the saved binding alone names the cause, so no authority call is made."""
    CASES = (
        ('outside the bound directory', elsewhere, 'cwd_mismatch'),
        ('no grant was ever given, or it was revoked', without_grant, 'delegation_absent'),
        ('coordinator-only grant', coordinator, 'delegation_not_members'),
        ('unknown grant scope', lambda r: {**r, 'delegation': {**r['delegation'], 'scope': 'other'}},
         'delegation_not_members'),
        ('grant pinned to another agent', stale_pin, 'delegation_changed'),
        ('grant lost its inline mode', lambda r: {**r, 'delegation': {**r['delegation'], 'dispatch_mode': 'other'}},
         'delegation_changed'),
    )

    def test_each_cause_maps_to_its_reason_before_any_rpc(self):
        def forbidden(*_): self.fail('no authority RPC for a local cause')
        for name, change, reason in self.CASES:
            with self.subTest(name):
                record = change(member())
                result = task_check(SimpleNamespace(call=forbidden), record, MESSAGE, lambda: copy.deepcopy(record))
                self.assertEqual((result['authorized'], result['state'], result['reason']), (False, 'pending', reason))
                self.assertTrue(result['next_step'])

    def test_status_reports_the_same_cause(self):
        for name, change, reason in self.CASES:
            with self.subTest(name):
                if change is coordinator: continue  # A coordinator grant is current for its own scope; see below.
                state = delegation_state(change(member()))
                self.assertEqual(state['reason'], reason)
                self.assertEqual(state['state'], 'absent' if reason == 'delegation_absent' else 'blocked')

    def test_status_reports_a_current_grant_of_either_scope(self):
        record = member()
        self.assertEqual(delegation_state(record), {'state': 'current', 'scope': 'members'})
        self.assertEqual(delegation_state(coordinator(record)), {'state': 'current', 'scope': 'coordinator'})
        self.assertIsNone(delegation_state(None))

    def test_status_reports_a_coordinator_grant_outside_its_directory_or_pin(self):
        record = coordinator(member())
        moved = {**record, 'cwd': '/synthetic/bound', 'delegation': {**record['delegation'], 'cwd': '/synthetic/bound'}}
        self.assertEqual(delegation_state(moved)['reason'], 'cwd_mismatch')
        stale = {**record, 'delegation': {**record['delegation'], 'agent_id': OTHER_AGENT}}
        self.assertEqual(delegation_state(stale)['reason'], 'delegation_changed')
        self.assertIn('session delegate --coordinator-role coordinator', delegation_state(stale)['next_step'])


class CwdTests(unittest.TestCase):
    def test_cwd_mismatch_names_the_bound_directory_not_a_changed_grant(self):
        record = elsewhere(member())
        result = task_check(api(), record, MESSAGE, lambda: record)
        self.assertEqual(result['expected_cwd'], '/synthetic/bound')
        self.assertIn('unchanged', result['next_step'])
        self.assertEqual(delegation_state(record)['expected_cwd'], '/synthetic/bound')

    def test_the_bound_directory_passes_again_with_the_same_grant(self):
        record = member()
        with tempfile.TemporaryDirectory() as root:
            original = os.getcwd()
            self.addCleanup(os.chdir, original)
            os.chdir(root)
            self.assertEqual(task_check(api(), record, MESSAGE, lambda: record)['reason'], 'cwd_mismatch')
            os.chdir(original)
        self.assertTrue(task_check(api(), record, MESSAGE, lambda: record)['authorized'])

    def test_a_deleted_directory_is_a_mismatch_not_a_crash(self):
        record = member()
        with patch('pathlib.Path.cwd', side_effect=FileNotFoundError):
            self.assertEqual(task_check(api(), record, MESSAGE, lambda: record)['reason'], 'cwd_mismatch')

    def test_a_windows_path_differing_only_in_case_is_the_same_directory(self):
        with patch('os.path.normcase', ntpath.normcase), \
                patch('pathlib.Path.cwd', return_value=Path('c:/Project')), \
                patch('pathlib.Path.resolve', lambda self: self):
            self.assertTrue(quorum_delegation.in_bound_directory('C:/project'))
            self.assertFalse(quorum_delegation.in_bound_directory('C:/other'))


class ChangeDuringCheckTable(unittest.TestCase):
    CASES = (
        ('owner revoked the grant', {'delegation': None}, 'delegation_changed'),
        ('binding removed', None, 'binding_changed'),
        ('listener endpoint rebound', {'messaging_socket': 'synthetic-new'}, 'binding_changed'),
        ('Grok transport moved', {'grok_transport': {'generation': 2}}, 'binding_changed'),
        ('transport replaced', {'transport': 'tool-inbox'}, 'binding_changed'),
        ('agent replaced', {'agent_id': OTHER_AGENT}, 'binding_changed'),
    )

    def test_only_a_grant_that_differs_is_delegation_changed(self):
        for name, changes, reason in self.CASES:
            with self.subTest(name):
                record = member()
                state = {'current': copy.deepcopy(record)}
                def call(*_):
                    state['current'] = None if changes is None else {**state['current'], **changes}
                    return authority()
                result = task_check(SimpleNamespace(call=call), record, MESSAGE, lambda: state['current'])
                self.assertEqual((result['authorized'], result['reason']), (False, reason))

    def test_a_local_cause_wins_over_a_simultaneous_service_failure(self):
        record = member()
        current = copy.deepcopy(record)
        def call(*_):
            current['delegation'] = None
            raise QuorumError('service_unavailable')
        result = task_check(SimpleNamespace(call=call), record, MESSAGE, lambda: current)
        self.assertEqual(result['reason'], 'delegation_changed')


class RemoteCauseTable(unittest.TestCase):
    def test_each_service_failure_maps_to_its_reason(self):
        for error, reason in ((QuorumError('service_unavailable'), 'authority_unavailable'),
                              (QuorumError('service_error'), 'authority_unavailable'),
                              (QuorumError('service_error', missing=True), 'authority_migration_required'),
                              (QuorumError('auth_required'), 'auth_required'),
                              (QuorumError('auth_required', denial='agent_denied'), 'auth_required'),
                              (QuorumError('session_binding_denied'), 'session_binding_denied'),
                              (QuorumError('task_message_denied'), 'task_message_denied'),
                              (QuorumError('invalid_response'), 'authority_invalid'),
                              (QuorumError('response_too_large'), 'authority_invalid')):
            with self.subTest(reason=reason, error=str(error)):
                record = member()
                result = task_check(api(error=error), record, MESSAGE, lambda: record)
                self.assertEqual((result['authorized'], result['state'], result['reason']), (False, 'pending', reason))
                self.assertEqual(result['next_step'], NEXT_STEPS[reason])

    def test_an_unrelated_typed_failure_still_stops_the_command(self):
        record = member()
        with self.assertRaisesRegex(QuorumError, 'skill_update_required'):
            task_check(api(error=QuorumError('skill_update_required')), record, MESSAGE, lambda: record)

    def test_each_server_denial_keeps_its_reason_and_gains_a_next_step(self):
        for reason in ('sender_unavailable', 'not_task'):
            with self.subTest(reason=reason):
                record = member()
                result = task_check(api(authority(authorized=False, reason=reason)), record, MESSAGE, lambda: record)
                self.assertEqual((result['authorized'], result['reason']), (False, reason))
                self.assertEqual(result['next_step'], NEXT_STEPS[reason])

    def test_a_malformed_answer_is_invalid_authority(self):
        record = member()
        result = task_check(api({'authorized': True}), record, MESSAGE, lambda: record)
        self.assertEqual(result['reason'], 'authority_invalid')


class ReattestTests(unittest.TestCase):
    def test_a_really_changed_grant_tells_the_owner_the_exact_command(self):
        for options, expected in (({}, 'session delegate --members.'),
                                  ({'trust': True}, 'session delegate --members --trust-members.'),
                                  ({'standing_from': 'coordinator'},
                                   'session delegate --members --standing-grants-from coordinator.')):
            with self.subTest(options=options):
                record = stale_pin(member(**options))
                result = task_check(api(), record, MESSAGE, lambda: record)
                self.assertEqual(result['reason'], 'delegation_changed')
                self.assertIn(expected, result['next_step'])
                self.assertIn('receiving owner', result['next_step'])

    def test_a_cloud_binding_is_told_its_own_command(self):
        record = {**stale_pin(member()), 'client': 'grok-bot'}
        self.assertIn('cloud delegate --members',
                      task_check(api(), record, MESSAGE, lambda: record)['next_step'])

    def test_a_standing_check_keeps_the_task_checks_cause_and_step(self):
        record = elsewhere(member(standing_from='coordinator'))
        result = standing_check(api(), record, MESSAGE, 'skill-refresh', lambda: record, None)
        self.assertEqual((result['authorized'], result['reason']), (False, 'cwd_mismatch'))
        self.assertEqual(result['expected_cwd'], '/synthetic/bound')
        self.assertIn('rerun it from expected_cwd', result['next_step'])


class UnchangedGrantTests(unittest.TestCase):
    def test_a_grant_pins_no_member_list_so_a_new_or_reconnected_sender_passes_unchanged(self):
        record = member()
        self.assertEqual(set(record['delegation']), {'agent_id', 'quorum_id', 'session_ref', 'project_ref', 'cwd',
                                                     'scope', 'dispatch_mode'})
        for sender in (SENDER, OTHER_AGENT):
            with self.subTest(sender=sender):
                result = task_check(api(authority(sender_id=sender)), record, MESSAGE, lambda: copy.deepcopy(record))
                self.assertEqual((result['authorized'], result['sender_id']), (True, sender))

    def test_a_reconnect_with_a_new_agent_id_carries_the_member_grant(self):
        old = member(trust=True, standing_from='coordinator')
        record = {**without_grant(old), 'agent_id': OTHER_AGENT}
        carried = carried_grant(old, record)
        self.assertEqual(carried, {**old['delegation'], 'agent_id': OTHER_AGENT})

    def test_a_reconnect_never_moves_a_grant_that_no_longer_fits(self):
        old = member()
        for name, record in (('project', {**old, 'agent_id': OTHER_AGENT, 'project_ref': 'other'}),
                             ('directory', {**old, 'agent_id': OTHER_AGENT, 'cwd': '/synthetic/other'})):
            with self.subTest(name):
                self.assertEqual(carried_grant(old, record), old['delegation'])
        stale = stale_pin(old)
        self.assertEqual(carried_grant(stale, {**old, 'agent_id': SENDER}), stale['delegation'])

    def test_a_reconnect_never_moves_a_coordinator_grant(self):
        old = coordinator(member())
        self.assertEqual(carried_grant(old, {**old, 'agent_id': OTHER_AGENT}), old['delegation'])

    @isolated_profiles
    def test_session_route_status_and_reconnect_keep_an_all_members_grant(self):
        from quorum_release import installed_release
        from quorum_session import dispatch
        from quorum_state import LocalLedger
        class Api:
            required = installed_release()
            def call(self, operation, payload):
                if operation == 'session_requirements': return self.required
                if operation == 'session_task_authority':
                    return authority(agent_id=payload['agent_id'])
                raise AssertionError(operation)
        environment = {key: value for key, value in os.environ.items()
                       if key not in ('CODEX_THREAD_ID', 'CLAUDE_CODE_SESSION_ID', 'GROK_SESSION_ID')}
        environment['CODEX_THREAD_ID'] = SESSION
        with tempfile.TemporaryDirectory() as root, patch.dict(os.environ, environment, clear=True), \
                patch('quorum_live.api_for', return_value=(Api(), None, None)), \
                patch('quorum_session.shutil.which', return_value='/synthetic/codex'):
            auth = LocalLedger(Path(root))
            self.addCleanup(auth.db.close)
            agents = [AGENT, OTHER_AGENT]
            def register(*_, **__): return dict(agent_id=agents[0], quorum_id=QUORUM)
            connect = dict(session_id=SESSION, action='connect', role='receiver', quorum='team', project_ref='project',
                           accept_tasks_from=None, dispatch_mode='inline')
            check = SimpleNamespace(session_id=SESSION, action='task-check', message_id=MESSAGE)
            status = SimpleNamespace(session_id=SESSION, action='status')
            dispatch(SimpleNamespace(**connect, accept_tasks_from_members=True), auth, register)
            self.assertEqual(dispatch(status, auth, register)['delegation_state'],
                             {'state': 'current', 'scope': 'members'})
            self.assertTrue(dispatch(check, auth, register)['authorized'])
            # A plain reconnect that re-registered this same chat under a new agent ID.
            agents.pop(0)
            connected = dispatch(SimpleNamespace(**connect, accept_tasks_from_members=False), auth, register)
            self.assertEqual(connected['delegation']['agent_id'], OTHER_AGENT)
            self.assertEqual(dispatch(status, auth, register)['delegation_state']['state'], 'current')
            result = dispatch(check, auth, register)
            self.assertTrue(result['authorized'], result)
            # The same grant, from outside the bound directory.
            original = os.getcwd()
            self.addCleanup(os.chdir, original)
            os.chdir(root)
            self.assertEqual(dispatch(check, auth, register)['reason'], 'cwd_mismatch')
            state = dispatch(status, auth, register)['delegation_state']
            self.assertEqual((state['state'], state['reason'], state['expected_cwd']),
                             ('blocked', 'cwd_mismatch', str(Path(original).resolve())))


class RegistryTests(unittest.TestCase):
    def test_every_reason_is_registered_and_has_one_next_step(self):
        self.assertEqual(set(NEXT_STEPS), CHECK_REASONS)

    def test_every_literal_reason_task_check_can_return_is_registered(self):
        tree = ast.parse((SCRIPTS / 'quorum_delegation.py').read_text(encoding='utf-8'))
        functions = {node.name: node for node in ast.walk(tree) if isinstance(node, ast.FunctionDef)}
        literals = {node.value.value for node in ast.walk(functions['local_cause'])
                    if isinstance(node, ast.Return) and isinstance(node.value, ast.Constant)
                    and isinstance(node.value.value, str)}
        literals |= {node.args[0].value for node in ast.walk(functions['task_check'])
                     if isinstance(node, ast.Call) and getattr(node.func, 'id', None) == 'pending'
                     and isinstance(node.args[0], ast.Constant)}
        self.assertGreaterEqual(literals, {'cwd_mismatch', 'delegation_changed', 'authority_unavailable'})
        self.assertLessEqual(literals, CHECK_REASONS)
        self.assertLessEqual(set(quorum_delegation.BINDING_REFUSALS), CHECK_REASONS)

    def test_no_next_step_prints_a_placeholder_or_a_secret_field(self):
        record = member()
        for reason in CHECK_REASONS:
            text = json.dumps(quorum_delegation.explain(reason, record))
            self.assertNotIn('{', text.replace('{"', '', 1))
            for marker in ('token', 'apikey', 'Authorization'): self.assertNotIn(marker, text)


if __name__ == '__main__':
    unittest.main()
