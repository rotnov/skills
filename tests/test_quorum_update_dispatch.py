"""Real update CLI/QuorumApi guards with synthetic tokens and injected transport."""
import contextlib
import copy
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from quorum_test_support import isolated_profiles
from test_quorum_updates import SID, AID, QID, OPID


class UpdateDispatchAuthorityTests(unittest.TestCase):
    def record(self):
        cwd = str(Path.cwd().resolve())
        registration = dict(engine='codex', session_ref=SID, project_ref='project', cwd=cwd)
        return {**registration, 'registration': registration.copy(), 'agent_id': AID, 'quorum_id': QID,
                'quorum': 'team', 'device_id': 'synthetic-device', 'role': 'receiver',
                'transport': 'native', 'worker_binding': {'synthetic_parent': 'one'},
                'messaging_socket': 'synthetic-endpoint-one', 'grok_transport': {'endpoint': 'one'}}

    def cli(self, initial=None, *, token_change=None, response_change=None, worker_proof=None):
        from quorum import main
        from quorum_api import QuorumApi, ServiceConfig
        from quorum_state import LocalLedger
        requests = []
        with tempfile.TemporaryDirectory() as root:
            record = copy.deepcopy(initial or self.record())
            session = LocalLedger(Path(root)/'sessions'/SID)
            try:
                session.setting('session-agent', record)
                def change(values):
                    if values is not None:
                        latest = session.setting('session-agent')
                        latest.update(copy.deepcopy(values))
                        session.setting('session-agent', latest)
                def token():
                    change(token_change)
                    return 'synthetic-token'
                def request(url, headers, body):
                    requests.append(url)
                    change(response_change)
                    return {'private_probe': 'response-must-not-leak'}
                api = QuorumApi(ServiceConfig('https://synthetic.invalid', 'public'), token, request)
                with patch.dict(os.environ, {'CODEX_THREAD_ID': SID}, clear=True), \
                        patch('quorum_live.api_for', return_value=(api, None, None)), \
                        patch('quorum_native_inbox.require_worker', side_effect=worker_proof), \
                        contextlib.redirect_stdout(io.StringIO()) as output:
                    code = main(['--state', root, 'update', 'team', '--status', OPID])
                return code, json.loads(output.getvalue()), requests, output.getvalue()
            finally:
                session.db.close()

    @isolated_profiles
    def test_token_wait_cannot_redirect_transport_worker_registration_or_endpoints(self):
        changes = ({'transport': 'tool-inbox'}, {'worker_binding': {'synthetic_parent': 'two'}},
                   {'registration': {**self.record()['registration'], 'project_ref': 'other'}},
                   {'messaging_socket': 'synthetic-endpoint-two'}, {'grok_transport': {'endpoint': 'two'}})
        for change in changes:
            with self.subTest(change=change):
                code, result, requests, text = self.cli(token_change=change)
                self.assertEqual((code, result['code']), (3, 'session_binding_denied'), text)
                self.assertEqual(requests, [], 'guard must run after token resolution, before the HTTP boundary')

    @isolated_profiles
    def test_response_wait_does_not_return_data_after_endpoint_change(self):
        code, result, requests, text = self.cli(response_change={'messaging_socket': 'synthetic-endpoint-two'})
        self.assertEqual((code, result['code']), (3, 'session_binding_denied'), text)
        self.assertEqual(len(requests), 1)
        self.assertNotIn('response-must-not-leak', text)

    @isolated_profiles
    def test_actual_working_directory_must_match_the_binding_before_rpc(self):
        record = {**self.record(), 'cwd': '/synthetic-wrong-cwd'}
        code, result, requests, text = self.cli(record)
        self.assertEqual((code, result['code']), (3, 'session_binding_denied'), text)
        self.assertEqual(requests, [])

    @isolated_profiles
    def test_tool_worker_proof_rereads_latest_record_after_token_wait(self):
        seen = []
        record = {**self.record(), 'transport': 'tool-inbox'}
        code, result, requests, text = self.cli(record, token_change={'probe_marker': 'after-token'},
                                              worker_proof=lambda latest: seen.append(copy.deepcopy(latest)))
        self.assertEqual(code, 0, text)
        self.assertEqual(len(requests), 1)
        self.assertTrue(any(value.get('probe_marker') == 'after-token' for value in seen),
                        'native proof must check the latest record, not the captured pre-wait record')


if __name__ == '__main__': unittest.main()
