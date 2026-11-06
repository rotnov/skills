"""Route-aware doctor (#129): a healthy route passes; the ACP worker gate is informational elsewhere."""
import contextlib
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

SKILL = Path(__file__).resolve().parents[1] / 'skills/quorum'
sys.path.insert(0, str(SKILL / 'scripts'))

import quorum  # noqa: E402
import quorum_doctor  # noqa: E402
import quorum_profiles  # noqa: E402
from quorum_api import QuorumError, ServiceConfig  # noqa: E402

CLIENT_VARIABLES = ('CODEX_THREAD_ID', 'CLAUDE_CODE_SESSION_ID', 'GROK_SESSION_ID')
TOKEN = 'access-token-that-must-not-appear'
CONFIG = ServiceConfig('https://quorum.example', 'publishable-key')


class Store:
    backend = None

    def __init__(self, session):
        self.session = session

    def load(self, origin):
        assert origin == CONFIG.origin
        return self.session


def run(argv, environ=None, profile=None):
    """Run the CLI with a fake resolved profile; no network or process may start."""
    environ = {} if environ is None else environ
    clean = {key: value for key, value in os.environ.items() if key not in CLIENT_VARIABLES}
    profile = profile or (lambda state: (CONFIG, Store({'access_token': TOKEN, 'refresh_token': TOKEN})))
    with tempfile.TemporaryDirectory() as state, \
            patch.dict(os.environ, {**clean, **environ}, clear=True), \
            patch.object(quorum_profiles, 'resolve_profile', side_effect=profile), \
            patch.object(subprocess, 'Popen', side_effect=AssertionError('no process may start')), \
            patch('urllib.request.urlopen', side_effect=AssertionError('no network call')), \
            contextlib.redirect_stdout(io.StringIO()) as output:
        code = quorum.main(['--state', state, 'doctor', *argv])
    return code, json.loads(output.getvalue()), output.getvalue()


class DoctorTests(unittest.TestCase):
    def test_healthy_codex_tool_inbox_route_passes_with_gate_informational(self):
        code, result, raw = run([], {'CODEX_THREAD_ID': 'thread'})
        self.assertEqual((code, result['ok'], result['code']), (0, True, 'ok'))
        data = result['data']
        self.assertEqual((data['route'], data['route_source']), ('codex-tool-inbox', 'client_env'))
        self.assertEqual(data['checks'], {'route': 'ok', 'service': 'configured', 'login': 'present'})
        self.assertEqual(data['pilot_gate'], {'state': quorum_doctor.gate_state(), 'applies_to': ['acp-worker'],
                                              'applies': False})
        self.assertEqual(data['pilot_gate']['state'], 'engine_acceptance_pending')
        self.assertNotIn(TOKEN, raw)

    def test_every_capability_route_is_checked_on_its_own_health(self):
        for route in quorum_doctor.ROUTES:
            code, result, _ = run(['--route', route])
            self.assertEqual((code, result['ok'], result['data']['route']), (0, True, route))
            self.assertFalse(result['data']['pilot_gate']['applies'])

    def test_explicit_acp_worker_route_still_reports_the_gate_as_failure(self):
        code, result, _ = run(['--route', 'acp-worker'], profile=lambda state: self.fail('no profile needed'))
        self.assertEqual((code, result['ok'], result['code']), (4, False, 'engine_acceptance_pending'))
        self.assertEqual(result['data']['pilot_gate']['state'], 'engine_acceptance_pending')
        self.assertTrue(result['data']['pilot_gate']['applies'])

    def test_missing_login_on_the_actual_route_fails(self):
        code, result, _ = run([], {'CODEX_THREAD_ID': 'thread'}, profile=lambda state: (CONFIG, Store(None)))
        self.assertEqual((code, result['ok'], result['code']), (3, False, 'auth_required'))
        self.assertEqual(result['data']['checks']['login'], 'missing')
        self.assertFalse(result['data']['pilot_gate']['applies'])

    def test_unconfigured_service_on_the_actual_route_fails(self):
        def unconfigured(state):
            raise QuorumError('service_not_configured')
        code, result, _ = run(['--route', 'claude-worker'], profile=unconfigured)
        self.assertEqual((code, result['ok'], result['code']), (4, False, 'service_not_configured'))
        self.assertEqual(result['data']['checks'], {'route': 'ok', 'service': 'failed'})

    def test_unknown_route_fails_without_reading_state(self):
        code, result, _ = run([], profile=lambda state: self.fail('no profile without a route'))
        self.assertEqual((code, result['ok'], result['code']), (4, False, 'route_unknown'))
        code, result, _ = run([], {'CODEX_THREAD_ID': 'a', 'CLAUDE_CODE_SESSION_ID': 'b'})
        self.assertEqual((result['code'], result['data']['route_source']), ('route_unknown', 'ambiguous_client_env'))

    def test_worker_gate_itself_is_unchanged(self):
        with tempfile.TemporaryDirectory() as state, contextlib.redirect_stdout(io.StringIO()) as output:
            code = quorum.main(['--state', state, 'worker'])
        self.assertEqual((code, json.loads(output.getvalue())['code']), (4, 'live_agents_disabled'))


if __name__ == '__main__':
    unittest.main()
