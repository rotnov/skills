"""Local contracts; never launch a provider process or use real credentials."""
import importlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

SCRIPTS = Path(__file__).resolve().parents[1] / 'skills/quorum/scripts'
sys.path.insert(0, str(SCRIPTS))


class ClientTests(unittest.TestCase):
    def setUp(self):
        self.api = importlib.import_module('quorum_api')
        self.state = importlib.import_module('quorum_state')

    def test_https_origin_only(self):
        for url in ('http://example.com', 'https://user:pass@example.com',
                    'https://example.com/path', 'https://example.com?x=1'):
            with self.subTest(url=url), self.assertRaises(ValueError):
                self.api.ServiceConfig(url, 'public')

    def test_rpc_auth_and_error_do_not_leak_credentials(self):
        seen = []
        def request(url, headers, body):
            seen.append((url, headers, json.loads(body)))
            return {'agent_id': 'a'}
        api = self.api.QuorumApi(self.api.ServiceConfig('https://example.com', 'pub'),
                                lambda: 'private-token', request)
        self.assertEqual(api.call('agent_join', {'role': 'backend'}, 'r'), {'agent_id': 'a'})
        self.assertTrue(seen[0][0].endswith('/rest/v1/rpc/agent_join'))
        self.assertEqual(seen[0][1]['Authorization'], 'Bearer private-token')
        self.assertEqual(seen[0][2]['p_request_id'], 'r')
        with self.assertRaises(ValueError):
            api.call('../arbitrary', {})

    def test_refresh_failure_stops_before_request(self):
        def token():
            raise self.api.QuorumError('auth_required')
        api = self.api.QuorumApi(self.api.ServiceConfig('https://example.com', 'pub'), token,
                                lambda *_: self.fail('request after auth failure'))
        with self.assertRaisesRegex(self.api.QuorumError, 'auth_required'):
            api.call('context_read', {})

    def test_local_capability_scope_expiry_and_revocation(self):
        with tempfile.TemporaryDirectory() as root:
            ledger = self.state.LocalLedger(Path(root), clock=lambda: 100)
            token = ledger.issue_capability('a', ttl=10)
            self.assertEqual(ledger.authorize(token), 'a')
            ledger.clock = lambda: 111
            with self.assertRaisesRegex(ValueError, 'capability_denied'):
                ledger.authorize(token)
            ledger.clock = lambda: 100
            token = ledger.issue_capability('a', ttl=10)
            ledger.revoke('a')
            with self.assertRaises(ValueError):
                ledger.authorize(token)

    def test_ledger_rejects_secret_event_fields(self):
        with tempfile.TemporaryDirectory() as root:
            ledger = self.state.LocalLedger(Path(root))
            with self.assertRaises(ValueError):
                ledger.record_event('attempt', 'started', {'refresh_token': 'secret'})

    def test_device_lock_excludes_second_worker_and_releases(self):
        with tempfile.TemporaryDirectory() as root:
            one = self.state.LocalLedger(Path(root))
            two = self.state.LocalLedger(Path(root))
            with one.worker_lock():
                with self.assertRaisesRegex(ValueError, 'worker_already_running'):
                    with two.worker_lock():
                        self.fail('second worker entered')
            with two.worker_lock():
                pass

    def test_session_binding_is_explicit_and_conflicts_fail(self):
        with tempfile.TemporaryDirectory() as root:
            ledger = self.state.LocalLedger(Path(root))
            binding = self.state.SessionBinding('q', 'p', 'd', 'a', 'c', 'codex', root, 'main')
            ledger.bind_session(binding)
            ledger.bind_session(binding)
            self.assertEqual(ledger.binding('c'), binding)
            changed = self.state.SessionBinding('q', 'p', 'd', 'other', 'c', 'codex', root, 'main')
            with self.assertRaisesRegex(ValueError, 'binding_conflict'):
                ledger.bind_session(changed)

    def test_relocated_individual_copy_cli(self):
        with tempfile.TemporaryDirectory() as root:
            copy = Path(root) / 'quorum'
            shutil.copytree(SCRIPTS.parent, copy)
            completed = subprocess.run([sys.executable, str(copy / 'scripts/quorum.py'),
                                        '--state', str(Path(root) / 'state'), 'status'],
                                       cwd=root, capture_output=True, text=True)
            self.assertEqual(completed.returncode, 0, completed.stderr)
            self.assertEqual(json.loads(completed.stdout)['data']['worker'], 'offline')
            failed = subprocess.run([sys.executable, str(copy / 'scripts/quorum.py'), '--state', str(Path(root) / 'state'), 'join',
                                     'amplifier', '--role', 'backend'], cwd=root,
                                    capture_output=True, text=True)
            self.assertNotEqual(failed.returncode, 0)
            self.assertNotIn('Traceback', failed.stderr + failed.stdout)

    def test_mcp_rejects_invite_and_sender_impersonation(self):
        module = importlib.import_module('quorum_mcp')
        calls = []
        tools = module.McpTools(lambda token: 'agent', lambda name, data: calls.append((name, data)),
                               scope=lambda agent: {'quorum_id': 'q', 'project_ref': 'scratch/repo'})
        with self.assertRaises(ValueError):
            tools.call('cap', 'quorum_invite', {})
        with self.assertRaises(ValueError):
            tools.call('cap', 'quorum_send', {'sender_agent_id': 'victim', 'body': 'x'})
        tools.call('cap', 'quorum_send', {'recipient_agent_id': 'b', 'body': 'hello',
                                        'conversation_id': 'c', 'quorum_id': 'q', 'request_id': 'r'})
        self.assertEqual(calls[0][1]['sender_agent_id'], 'agent')

    def test_mcp_capability_cannot_read_another_quorum_or_project(self):
        module = importlib.import_module('quorum_mcp')
        tools = module.McpTools(lambda token: 'agent', lambda *_: self.fail('cross-scope request forwarded'),
                               scope=lambda agent: {'quorum_id': 'q', 'project_ref': 'scratch/repo'})
        with self.assertRaisesRegex(ValueError, 'scope_denied'):
            tools.call('cap', 'quorum_context', {'quorum_id': 'other', 'project_ref': 'scratch/repo', 'after_version': 0})
        with self.assertRaisesRegex(ValueError, 'scope_denied'):
            tools.call('cap', 'quorum_context', {'quorum_id': 'q', 'project_ref': 'secret/repo', 'after_version': 0})

    def test_join_registers_once_without_starting_worker(self):
        module = importlib.import_module('quorum')
        calls = []
        class Api:
            def call(self, operation, payload, request_id=None):
                calls.append(operation)
                return {'quorum_lookup': {'quorum_id': 'q'}, 'device_register': {'device_id': 'd'},
                        'agent_join': {'agent_id': 'a'}, 'agent_status': {'registered': True}}[operation]
        with tempfile.TemporaryDirectory() as root:
            ledger = self.state.LocalLedger(Path(root))
            data = dict(engine='claude', project_ref='scratch/repo', session_ref='main', cwd=root)
            first = module.register_agent(Api(), ledger, 'amplifier', 'backend', data)
            second = module.register_agent(Api(), ledger, 'amplifier', 'backend', data)
            self.assertEqual(first, second)
            self.assertEqual(first['worker'], 'offline')
            self.assertEqual(calls.count('device_register'), 1)
            self.assertEqual(calls.count('agent_join'), 1)

    def test_cached_join_rechecks_live_agent_authorization(self):
        module = importlib.import_module('quorum')
        class Api:
            revoked = False
            def call(self, operation, payload, request_id=None):
                if operation == 'agent_status' and self.revoked:
                    raise self_error('auth_required')
                return {'quorum_lookup': {'quorum_id': 'q'}, 'device_register': {'device_id': 'd'},
                        'agent_join': {'agent_id': 'a'}, 'agent_status': {'registered': True}}[operation]
        self_error = self.api.QuorumError
        with tempfile.TemporaryDirectory() as root:
            ledger, api = self.state.LocalLedger(Path(root)), Api()
            data = dict(engine='claude', project_ref='scratch/repo', session_ref='main', cwd=root)
            module.register_agent(api, ledger, 'amplifier', 'backend', data)
            api.revoked = True
            with self.assertRaises(self_error):
                module.register_agent(api, ledger, 'amplifier', 'backend', data)


if __name__ == '__main__':
    unittest.main()
