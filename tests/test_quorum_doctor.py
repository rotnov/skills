"""Route-aware doctor (#129): a healthy route passes; the ACP worker gate is informational elsewhere.

Every case uses real temporary state and credential stores. doctor must stay offline and read-only:
no network call, no process, and no file or directory created or changed.
"""
import contextlib
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

SKILL = Path(__file__).resolve().parents[1] / 'skills/quorum'
sys.path.insert(0, str(SKILL / 'scripts'))

import quorum  # noqa: E402
import quorum_doctor  # noqa: E402
from quorum_api import ServiceConfig  # noqa: E402
from quorum_auth import CredentialStore  # noqa: E402
from quorum_profiles import resolve_profile  # noqa: E402

CLIENT_VARIABLES = ('CODEX_THREAD_ID', 'CLAUDE_CODE_SESSION_ID', 'GROK_SESSION_ID')
ORIGIN = 'https://quorum.example'
CONFIG = ServiceConfig(ORIGIN, 'sb_publishable_synthetic')
NO_NETWORK = AssertionError('doctor made a network call')


def session(access='machine-access-secret', refresh='machine-refresh-secret', identity='123', user_id='user-123'):
    return {'access_token': access, 'refresh_token': refresh, 'expires_at': time.time() + 3600,
            'user': {'id': user_id, 'identities': [{'provider': 'github', 'id': identity,
                                                    'identity_data': {'provider_id': identity}}]}}


class DoctorTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        clean = {key: value for key, value in os.environ.items() if key not in CLIENT_VARIABLES}
        environment = patch.dict(os.environ, {**clean, 'QUORUM_PROFILE_ROOT': str(self.root / 'machine')}, clear=True)
        environment.start()
        self.addCleanup(environment.stop)

    def configure(self, state='state', login=True):
        config, store = resolve_profile(self.root / state, CONFIG)
        if login: store.save(config.origin, session())
        return self.root / state

    def tree(self):
        return {path: (path.is_dir(), None if path.is_dir() else path.read_bytes(), path.stat().st_mode)
                for path in self.root.rglob('*')}

    def doctor(self, *argv, state='state', environ=None):
        before = self.tree()
        with patch.dict(os.environ, environ or {}), \
                patch('quorum_api.get_json', side_effect=NO_NETWORK), \
                patch('quorum_api.request_json', side_effect=NO_NETWORK), \
                patch('urllib.request.urlopen', side_effect=NO_NETWORK), \
                patch.object(subprocess, 'Popen', side_effect=AssertionError('no process may start')), \
                contextlib.redirect_stdout(io.StringIO()) as output:
            code = quorum.main(['--state', str(self.root / state), 'doctor', *argv])
        self.assertEqual(self.tree(), before, 'doctor wrote, created or changed state')
        raw = output.getvalue()
        self.assertNotIn('secret', raw)
        return code, json.loads(raw)

    def test_healthy_codex_tool_inbox_route_passes_with_gate_informational(self):
        self.configure()
        code, result = self.doctor(environ={'CODEX_THREAD_ID': 'thread'})
        self.assertEqual((code, result['ok'], result['code']), (0, True, 'ok'))
        data = result['data']
        self.assertEqual((data['route'], data['route_source']), ('codex-tool-inbox', 'client_env'))
        self.assertEqual(data['checks'], {'route': 'ok', 'service': 'configured', 'login': 'present'})
        self.assertEqual(data['pilot_gate'], {'state': 'engine_acceptance_pending', 'applies_to': ['acp-worker'],
                                              'applies': False})
        self.assertEqual(quorum_doctor.gate_state(), 'engine_acceptance_pending')

    def test_every_capability_route_is_checked_on_its_own_health(self):
        self.configure()
        for route in quorum_doctor.ROUTES:
            code, result = self.doctor('--route', route)
            self.assertEqual((code, result['ok'], result['data']['route']), (0, True, route))
            self.assertFalse(result['data']['pilot_gate']['applies'])

    def test_explicit_acp_worker_route_still_reports_the_gate_as_failure(self):
        self.configure()
        code, result = self.doctor('--route', 'acp-worker')
        self.assertEqual((code, result['ok'], result['code']), (4, False, 'engine_acceptance_pending'))
        self.assertEqual(result['data']['pilot_gate']['state'], 'engine_acceptance_pending')
        self.assertTrue(result['data']['pilot_gate']['applies'])

    def test_missing_login_on_the_actual_route_fails(self):
        self.configure(login=False)
        code, result = self.doctor(environ={'CODEX_THREAD_ID': 'thread'})
        self.assertEqual((code, result['ok'], result['code']), (3, False, 'auth_required'))
        self.assertEqual(result['data']['checks']['login'], 'missing')
        self.assertFalse(result['data']['pilot_gate']['applies'])

    def test_unconfigured_service_fails_without_creating_state_or_profiles(self):
        code, result = self.doctor('--route', 'claude-worker')
        self.assertEqual((code, result['ok'], result['code']), (4, False, 'service_not_configured'))
        self.assertEqual(result['data']['checks'], {'route': 'ok', 'service': 'failed'})
        self.assertFalse((self.root / 'state').exists())
        self.assertFalse((self.root / 'machine').exists())

    def test_unknown_route_fails(self):
        self.configure()
        code, result = self.doctor()
        self.assertEqual((code, result['ok'], result['code']), (4, False, 'route_unknown'))
        code, result = self.doctor(environ={'CODEX_THREAD_ID': 'a', 'CLAUDE_CODE_SESSION_ID': 'b'})
        self.assertEqual((result['code'], result['data']['route_source']), ('route_unknown', 'ambiguous_client_env'))

    def test_unbound_legacy_login_is_pending_without_auth_request_refresh_or_binding(self):
        # resolve_profile would verify this legacy login at /auth/v1/user and write auth-binding.json.
        self.configure('state-one')
        legacy_state = self.root / 'state-two'
        legacy_state.mkdir(mode=0o700)
        CredentialStore(root=legacy_state / 'credentials').save(
            ORIGIN, session('legacy-access-secret', 'legacy-refresh-secret'))
        code, result = self.doctor(state='state-two', environ={'CODEX_THREAD_ID': 'thread'})
        self.assertEqual((code, result['ok']), (0, True))
        self.assertEqual(result['data']['checks']['login'], 'pending_binding_verification')
        self.assertFalse((legacy_state / 'auth-binding.json').exists())
        self.assertFalse((legacy_state / 'config.json').exists())

    def test_worker_gate_itself_is_unchanged(self):
        with contextlib.redirect_stdout(io.StringIO()) as output:
            code = quorum.main(['--state', str(self.root / 'state'), 'worker'])
        self.assertEqual((code, json.loads(output.getvalue())['code']), (4, 'live_agents_disabled'))


if __name__ == '__main__':
    unittest.main()
