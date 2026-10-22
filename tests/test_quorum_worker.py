import importlib
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'skills/quorum/scripts'))


class WorkerTests(unittest.TestCase):
    def setUp(self):
        self.acp = importlib.import_module('quorum_acp')
        self.state = importlib.import_module('quorum_state')
        self.worker = importlib.import_module('quorum_worker')

    def test_live_launcher_disabled_until_verified(self):
        with self.assertRaisesRegex(ValueError, 'live_agents_disabled'):
            self.acp.AcpRunner().start(None, 'question', None)

    def test_manifest_rejects_mutable_versions(self):
        for version in ('latest', '^1.0.0', '1.x', '', 'preview'):
            with self.assertRaises(ValueError):
                self.acp.validate_manifest({'acpx': version, 'adapters': {'claude': version, 'codex': version}})
        self.acp.validate_manifest({'acpx': '1.2.3', 'adapters': {'claude': '2.3.4', 'codex': '3.4.5'}})

    def test_no_launch_without_readonly_policy_budget_subscription(self):
        for overrides in ({'max_turns': 0}, {'auth_kind': 'api_key'}, {'read_only': False},
                          {'owner_approved': False}):
            values = dict(max_turns=2, timeout=10, auth_kind='subscription', read_only=True,
                          owner_approved=True)
            values.update(overrides)
            with self.assertRaises(ValueError): self.acp.ExecutionPolicy(**values).validate()

    def test_worker_reply_and_duplicate_do_not_spawn_twice(self):
        self.run_scenario(crash=False)

    def test_crash_after_spawn_stays_unknown(self):
        self.run_scenario(crash=True)

    def run_scenario(self, crash):
        class FakeApi:
            def __init__(self): self.calls = []
            def call(self, op, data, request_id=None):
                if op == 'session_requirements': return {'protocol_version':1, 'skill_version':'0.2.0'}
                self.calls.append((op, data, request_id))
                if op == 'inbox_claim':
                    return {'attempt_id': 'attempt', 'generation': 1,
                            'delivery': {'conversation_id': 'c', 'body': 'hello'}}
                if op == 'context_read': return []
                return {'state': 'running'}
        class FakeRunner:
            def __init__(self): self.starts = 0
            def start(self, binding, prompt, policy):
                self.starts += 1
                return object()
            def events(self, handle):
                if crash: raise OSError('dropped after spawn')
                yield {'type': 'started'}
                yield {'type': 'completed', 'text': 'answer', 'success': True}
        with tempfile.TemporaryDirectory() as root:
            ledger = self.state.LocalLedger(Path(root))
            ledger.bind_session(self.state.SessionBinding('q', 'p', 'd', 'a', 'c', 'claude', root, 'main'))
            api, runner = FakeApi(), FakeRunner()
            worker = self.worker.Worker(api, ledger, runner, 'd', self.acp.ExecutionPolicy(2, 10, 'subscription', True, True))
            outcome = worker.run_once()
            self.assertEqual(outcome, 'unknown' if crash else 'answered')
            worker.run_once()
            self.assertEqual(runner.starts, 1)
            self.assertEqual(any(x[0] == 'delivery_complete' for x in api.calls), not crash)
            self.assertEqual(ledger.attempt('attempt')['state'], 'unknown' if crash else 'answered')


if __name__ == '__main__': unittest.main()
