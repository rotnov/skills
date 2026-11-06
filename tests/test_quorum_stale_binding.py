"""Issue #101: a left or revoked agent's local binding. Synthetic service only; never a model or real login."""
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
QUORUM = '44444444-4444-4444-8444-444444444444'
SPACE = '22222222-2222-4222-8222-222222222222'
CLIENT_VARIABLES = ('CODEX_THREAD_ID', 'GROK_SESSION_ID', 'GROK_LEADER_SOCKET', 'CLAUDE_CODE_SESSION_ID',
                    'CLAUDE_CODE_MESSAGING_SOCKET', 'CLAUDE_CODE_MESSAGING_TOKEN')


class Service:
    """The service rules this issue depends on: idempotent requests, one agent per device and session,
    `agent_leave` revokes, and a revoked agent is denied with `agent_denied` (403 `auth_required`)."""
    def __init__(self):
        self.calls, self.requests, self.agents, self.devices = [], {}, {}, 0

    def ops(self):
        return [call[0] for call in self.calls]

    def call(self, operation, payload, request_id=None):
        from quorum_api import QuorumError
        if operation == 'session_requirements': return {'protocol_version': 2, 'skill_version': '0.3.0'}
        self.calls.append((operation, payload, request_id))
        if (operation, request_id) in self.requests: return self.requests[operation, request_id]
        if operation == 'quorum_lookup': return {'quorum_id': QUORUM}
        if operation == 'device_register':
            self.devices += 1
            result = {'device_id': str(uuid.UUID(int=self.devices))}
        elif operation == 'agent_join':
            same = [agent_id for agent_id, agent in self.agents.items()
                    if (agent['device_id'], agent['session_ref']) == (payload['device_id'], payload['session_ref'])]
            if same:
                agent = self.agents[same[0]]
                # The server raises `binding_conflict`, which reaches the client as a generic service error.
                if agent['revoked'] or agent['role'] != payload['role']: raise QuorumError('service_error')
                result = {'agent_id': same[0]}
            else:
                agent_id = str(uuid.uuid4())
                self.agents[agent_id] = {**payload, 'revoked': False}
                result = {'agent_id': agent_id}
        elif operation == 'agent_leave':
            self.agents[payload['agent_id']]['revoked'] = True
            result = {'left': True}
        elif operation == 'agent_status' or operation.startswith('knowledge_'):
            if self.agents.get(payload['agent_id'], {'revoked': True})['revoked']:
                raise QuorumError('auth_required', denial='agent_denied')
            return {'agent_id': payload['agent_id'], 'registered': True} if operation == 'agent_status' else {'ok': True}
        else: raise AssertionError(operation)
        if request_id is not None: self.requests[operation, request_id] = result
        return result


class StaleBindingTests(unittest.TestCase):
    def setUp(self):
        clean = {key: value for key, value in os.environ.items() if key not in CLIENT_VARIABLES}
        isolated = patch.dict(os.environ, {**clean, 'CODEX_THREAD_ID': SID}, clear=True)
        isolated.start()
        self.addCleanup(isolated.stop)
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.service = Service()

    def cli(self, *argv, stdin=b''):
        import quorum
        from quorum_api import ServiceConfig
        with patch('quorum_live.api_for', return_value=(self.service, None, None)), \
                patch('quorum_profiles.resolve_profile',
                      return_value=(ServiceConfig('https://synthetic.invalid', 'public'), None)), \
                patch('quorum.QuorumApi', return_value=self.service), patch('quorum.token_provider'), \
                patch('quorum_session.shutil.which', return_value='/synthetic/codex'), \
                patch('sys.stdin', io.TextIOWrapper(io.BytesIO(stdin))), \
                contextlib.redirect_stdout(io.StringIO()) as output:
            code = quorum.main(['--state', str(self.root), *argv])
        text = output.getvalue()
        return code, json.loads(text), text

    def connect(self, role):
        return self.cli('session', 'connect', 'team', '--role', role, '--project-ref', 'project')

    def leave(self, agent_id):
        return self.cli('leave', '--request-id', 'leave-1', '--json-stdin',
                        stdin=json.dumps({'agent_id': agent_id}).encode())

    def knowledge(self):
        return self.cli('knowledge', 'admin', '--json-stdin', '--owner-action', '--request-id', 'admin-1',
                        stdin=json.dumps({'operation': 'create',
                                          'payload': {'space_id': SPACE, 'name': 'team'}}).encode())

    def session_setting(self, key):
        from quorum_state import LocalLedger
        ledger = LocalLedger(self.root / 'sessions' / SID)
        try: return ledger.setting(key)
        finally: ledger.db.close()

    def connected(self, role):
        code, result, text = self.connect(role)
        self.assertEqual(code, 0, text)
        return result['data']['agent_id']

    @isolated_profiles
    def test_leave_of_own_agent_retires_binding_and_reconnect_registers_a_new_agent(self):
        curator = self.connected('curator')
        code, result, text = self.leave(curator)
        self.assertEqual((code, result['data']), (0, {'left': True, 'local_binding': 'retired'}), text)
        self.assertIsNone(self.session_setting('session-agent'))
        self.assertEqual(self.session_setting('session-agent-retired')['agent_id'], curator)
        code, result, text = self.cli('session', 'status')
        self.assertEqual(result['data']['retired_binding']['reason'], 'left', text)
        # Knowledge names the retired binding instead of acting as the revoked agent.
        code, result, text = self.knowledge()
        self.assertEqual((code, result['code']), (3, 'binding_revoked'), text)
        self.assertEqual(result['data']['agent_id'], curator)
        self.assertNotIn('knowledge_admin', self.service.ops())
        # A different role now connects with a new device; the revoked agent is never reused.
        before = len(self.service.calls)
        owner = self.connected('owner')
        self.assertNotEqual(owner, curator)
        self.assertEqual([op for op in self.service.ops()[before:] if op != 'quorum_lookup'],
                         ['device_register', 'agent_join'])
        self.assertEqual(self.session_setting('session-agent')['role'], 'owner')
        code, result, text = self.knowledge()
        self.assertEqual(code, 0, text)
        self.assertEqual(self.service.calls[-1][1]['agent_id'], owner)

    @isolated_profiles
    def test_same_role_reconnect_after_leave_does_not_reuse_the_saved_agent(self):
        curator = self.connected('curator')
        self.assertEqual(self.leave(curator)[0], 0)
        again = self.connected('curator')
        self.assertNotEqual(again, curator)
        self.assertFalse(self.service.agents[again]['revoked'])

    @isolated_profiles
    def test_leave_of_another_agent_keeps_this_chat_binding(self):
        curator = self.connected('curator')
        other = str(uuid.uuid4())
        self.service.agents[other] = {'revoked': False}
        code, result, text = self.leave(other)
        self.assertEqual((code, result['data']), (0, {'left': True}), text)
        self.assertEqual(self.session_setting('session-agent')['agent_id'], curator)

    @isolated_profiles
    def test_agent_revoked_elsewhere_is_retired_by_the_next_conflicting_connect(self):
        curator = self.connected('curator')
        self.service.agents[curator]['revoked'] = True  # Left from another state or by the quorum owner.
        code, result, text = self.knowledge()
        self.assertEqual((code, result['code'], result['data']['agent_id']), (3, 'binding_revoked', curator), text)
        self.assertNotEqual(self.connected('owner'), curator)
        self.assertEqual(self.session_setting('session-agent-retired')['reason'], 'revoked')

    @isolated_profiles
    def test_agent_revoked_elsewhere_is_retired_by_a_same_role_reconnect(self):
        curator = self.connected('curator')
        self.service.agents[curator]['revoked'] = True
        again = self.connected('curator')
        self.assertNotEqual(again, curator)
        self.assertEqual(self.session_setting('session-agent')['agent_id'], again)

    @isolated_profiles
    def test_conflict_with_an_active_agent_reports_binding_conflict_with_safe_data(self):
        curator = self.connected('curator')
        before = len(self.service.calls)
        code, result, text = self.connect('owner')
        self.assertEqual((code, result['code']), (3, 'binding_conflict'), text)
        self.assertEqual({key: result['data'][key] for key in ('role', 'quorum', 'agent_id')},
                         {'role': 'curator', 'quorum': 'team', 'agent_id': curator})
        self.assertIn('leave', result['data']['next_step'])
        self.assertEqual(set(result['data']), {'role', 'quorum', 'agent_id', 'next_step'})
        self.assertNotIn('agent_join', self.service.ops()[before:])
        self.assertEqual(self.session_setting('session-agent')['agent_id'], curator)

    @isolated_profiles
    def test_retirement_archives_old_deliveries_and_disables_reception(self):
        from quorum_session import retire_binding
        from quorum_state import LocalLedger
        self.connected('curator')
        ledger = LocalLedger(self.root / 'sessions' / SID)
        try:
            record = ledger.setting('session-agent')
            ledger.setting('delivery:' + SPACE, {'state': 'intent', 'attempt_id': SID})
            self.assertFalse(retire_binding(ledger, {**record, 'role': 'changed'}, 'left'))
            self.assertEqual(ledger.setting('session-agent'), record)
            self.assertTrue(retire_binding(ledger, record, 'left'))
            self.assertIsNone(ledger.setting('delivery:' + SPACE))
            self.assertEqual(ledger.setting('session-agent-retired')['deliveries'],
                             {'delivery:' + SPACE: {'state': 'intent', 'attempt_id': SID}})
            self.assertEqual(ledger.setting('session-reception'), {'enabled': False})
            self.assertEqual(ledger.setting('session-generation'), 1)
        finally: ledger.db.close()
        code, result, text = self.cli('session', 'peers')
        self.assertEqual((code, result['code']), (3, 'binding_revoked'), text)


if __name__ == '__main__':
    unittest.main()
