"""Route-aware doctor (#129): a healthy route passes; the ACP worker gate is informational elsewhere.

Every case uses real temporary state and credential stores. doctor must stay offline and read-only:
no network call, no process, and no file or directory created or changed.
"""
import contextlib
import io
import json
import os
from pathlib import Path
import shutil
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
import quorum_profiles  # noqa: E402
from quorum_api import QuorumError, ServiceConfig  # noqa: E402
from quorum_auth import CredentialStore  # noqa: E402
from quorum_profiles import resolve_profile  # noqa: E402

CLIENT_VARIABLES = ('CODEX_THREAD_ID', 'CLAUDE_CODE_SESSION_ID', 'GROK_SESSION_ID')
ORIGIN = 'https://quorum.example'
CONFIG = ServiceConfig(ORIGIN, 'sb_publishable_synthetic')
NO_NETWORK = AssertionError('doctor made a network call')


def session(access='machine-access-secret', refresh='machine-refresh-secret', identity='123', user_id='user-123',
            expires=None):
    return {'access_token': access, 'refresh_token': refresh,
            'expires_at': time.time() + 3600 if expires is None else expires,
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

    def test_signed_out_machine_login_with_unbound_legacy_login_still_fails(self):
        _, store = resolve_profile(self.configure('state-one'), CONFIG)
        store.delete(ORIGIN)
        legacy_state = self.root / 'state-two'
        legacy_state.mkdir(mode=0o700)
        CredentialStore(root=legacy_state / 'credentials').save(
            ORIGIN, session('legacy-access-secret', 'legacy-refresh-secret'))
        code, result = self.doctor(state='state-two', environ={'CODEX_THREAD_ID': 'thread'})
        self.assertEqual((code, result['ok'], result['code']), (3, False, 'auth_profile_conflict'))
        self.assertEqual(result['data']['checks']['login'], 'failed')

    def test_worker_gate_itself_is_unchanged(self):
        with contextlib.redirect_stdout(io.StringIO()) as output:
            code = quorum.main(['--state', str(self.root / 'state'), 'worker'])
        self.assertEqual((code, json.loads(output.getvalue())['code']), (4, 'live_agents_disabled'))


PENDING = 'pending_binding_verification'
MACHINE_USER = session()['user']
# Each case: (name, setup(test) -> state directory name, expected inspect outcome).
# resolve_profile must agree on pass/fail, code and reason, except the network-pending case.
PARITY_CASES = (
    ('no state, no machine profile', lambda t: 'state', ('fail', 'service_not_configured', None)),
    ('machine login missing', lambda t: t.machine(login=False), ('ok', 'missing', None)),
    ('machine login present', lambda t: t.machine(), ('ok', 'present', None)),
    ('new state uses the machine login', lambda t: t.machine() and 'state-new', ('ok', 'present', None)),
    ('two machine profiles, no state config', lambda t: t.two_profiles(), ('fail', 'service_profile_ambiguous', None)),
    ('state config key conflict', lambda t: t.state_config('sb_publishable_other'),
     ('fail', 'service_config_conflict', None)),
    ('state config alternate origin spelling', lambda t: t.state_config(origin=ORIGIN + ':443'),
     ('fail', 'service_config_conflict', None)),
    ('machine credential directory gone', lambda t: t.machine_directory_gone(),
     ('fail', 'machine_profile_unavailable', None)),
    ('legacy only, unconfigured', lambda t: t.legacy(), ('fail', 'service_not_configured', None)),
    ('legacy only, configured: adopted in place', lambda t: t.legacy_configured(), ('ok', 'present', None)),
    ('legacy unbound, machine login present', lambda t: t.machine() and t.legacy(), ('ok', PENDING, None)),
    ('legacy unbound, machine signed out', lambda t: t.machine_signed_out() and t.legacy(),
     ('fail', 'auth_profile_conflict', 'machine_login_missing')),
    ('legacy unbound, machine never logged in', lambda t: t.machine(login=False) and t.legacy(),
     ('fail', 'auth_profile_conflict', 'machine_login_missing')),
    ('legacy unbound, other GitHub principal', lambda t: t.machine() and t.legacy(identity='456'),
     ('fail', 'auth_profile_conflict', 'different_principal')),
    ('legacy unbound, expired legacy login', lambda t: t.machine() and t.legacy(expires=1),
     ('fail', 'auth_profile_conflict', 'legacy_login_unverified')),
    ('legacy bound', lambda t: t.bound(), ('ok', 'present', None)),
    ('legacy bound, legacy login gone', lambda t: t.bound(drop_legacy=True), ('ok', 'present', None)),
    ('legacy bound, machine login gone', lambda t: t.bound(drop_machine=True), ('ok', 'missing', None)),
    ('legacy stale binding, machine user changed', lambda t: t.bound(rotate_machine=True), ('ok', PENDING, None)),
    ('stale binding, legacy login gone', lambda t: t.bound(rotate_machine=True, drop_legacy=True),
     ('fail', 'auth_profile_conflict', 'bound_principal_unverifiable')),
    ('binding for another credential root', lambda t: t.foreign_binding(),
     ('fail', 'auth_profile_conflict', 'binding_mismatch')),
    ('anonymous state, unconfigured', lambda t: t.anonymous(configured=False), ('fail', 'service_not_configured', None)),
    ('anonymous state, no login', lambda t: t.anonymous(), ('ok', 'missing', None)),
)


class InspectResolveParityTests(unittest.TestCase):
    """inspect_profile mirrors resolve_profile on every offline branch, with real stores."""

    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.temporary = Path(temporary.name).resolve()

    # Setup helpers (they may write; doctor and inspect_profile may not).
    def machine(self, login=True):
        config, store = resolve_profile(self.root / 'state-one', CONFIG)
        if login: store.save(ORIGIN, session())
        self.machine_store = store
        return 'state-one'

    def machine_signed_out(self):
        self.machine()
        self.machine_store.delete(ORIGIN)
        return 'state-one'

    def machine_directory_gone(self):
        self.machine()
        shutil.rmtree(self.machine_store.backend.root)
        return 'state-one'

    def two_profiles(self):
        self.machine()
        resolve_profile(self.root / 'state-other', ServiceConfig('https://other.example', 'sb_publishable_other'))
        return 'state-new'

    def state_config(self, key=CONFIG.publishable_key, origin=ORIGIN):
        self.machine()
        state = self.root / 'state-two'
        state.mkdir(mode=0o700)
        quorum_profiles.write_config(state / 'config.json', ServiceConfig(origin, key))
        return 'state-two'

    def legacy(self, **values):
        state = self.root / 'state-two'
        state.mkdir(mode=0o700, exist_ok=True)
        CredentialStore(root=state / 'credentials').save(
            ORIGIN, session('legacy-access-secret', 'legacy-refresh-secret', **values))
        return 'state-two'

    def legacy_configured(self):
        self.legacy()
        quorum_profiles.write_config(self.root / 'state-two' / 'config.json', CONFIG)
        return 'state-two'

    def bound(self, drop_legacy=False, drop_machine=False, rotate_machine=False):
        self.machine()
        self.legacy()
        with self.network():
            resolve_profile(self.root / 'state-two')
        self.assertTrue((self.root / 'state-two' / 'auth-binding.json').exists())
        if rotate_machine:
            self.machine_store.save(ORIGIN, session('rotated-access-secret', 'rotated-refresh-secret',
                                                    user_id='user-999'))
        if drop_machine: self.machine_store.delete(ORIGIN)
        if drop_legacy: CredentialStore(root=self.root / 'state-two' / 'credentials').delete(ORIGIN)
        return 'state-two'

    def foreign_binding(self):
        self.machine()
        self.legacy()
        path = self.root / 'state-two' / 'auth-binding.json'
        path.write_text(json.dumps({'origin': ORIGIN, 'credential_root': str(self.root / 'elsewhere'),
                                    'github_id': '123', 'auth_user_id': 'user-123'}))
        path.chmod(0o600)
        return 'state-two'

    def anonymous(self, configured=True):
        if configured:
            resolve_profile(self.root / 'state-anon', CONFIG, anonymous=True)
        else:
            (self.root / 'state-anon' / 'anonymous-credentials').mkdir(mode=0o700, parents=True)
            (self.root / 'state-anon').chmod(0o700)
        return 'state-anon'

    @contextlib.contextmanager
    def network(self):
        """A stand-in Auth server that verifies every known token as the same user."""
        self.calls = []

        def get_json(url, headers):
            self.calls.append(url)
            user = {**MACHINE_USER, 'id': 'user-999'} if 'rotated' in headers['Authorization'] else MACHINE_USER
            return user
        with patch('quorum_api.get_json', side_effect=get_json), \
                patch('quorum_api.request_json', side_effect=AssertionError('token refresh')), \
                patch('quorum_auth.login_pkce', side_effect=AssertionError('browser login')):
            yield

    def tree(self):
        return {path: None if path.is_dir() else path.read_bytes() for path in self.root.rglob('*')}

    @staticmethod
    def outcome(call):
        try:
            return ('ok', call(), None)
        except (QuorumError, ValueError) as error:
            reason = error.diagnostic()['reason'] if hasattr(error, 'diagnostic') else None
            return ('fail', str(error), reason)

    def test_inspect_profile_matches_resolve_profile_on_every_branch(self):
        for name, setup, expected in PARITY_CASES:
            with self.subTest(name), tempfile.TemporaryDirectory(dir=self.temporary) as root:
                self.root = Path(root)
                with patch.dict(os.environ, {'QUORUM_PROFILE_ROOT': str(self.root / 'machine')}):
                    state = self.root / setup(self)
                    before = self.tree()
                    with patch('quorum_api.get_json', side_effect=NO_NETWORK), \
                            patch('quorum_api.request_json', side_effect=NO_NETWORK):
                        inspected = self.outcome(lambda: quorum_profiles.inspect_profile(state)['login'])
                    self.assertEqual(self.tree(), before, 'inspect_profile wrote state')
                    self.assertEqual(inspected, expected)

                    def resolved():
                        config, store = resolve_profile(state)
                        return 'present' if store.load(config.origin) is not None else 'missing'
                    with self.network():
                        actual = self.outcome(resolved)
                    if inspected[1] == PENDING:
                        # The only allowed difference: resolve_profile needed the Auth server to decide.
                        self.assertTrue(self.calls, 'pending must be genuinely network-dependent')
                    else:
                        self.assertEqual(actual, inspected)


if __name__ == '__main__':
    unittest.main()
