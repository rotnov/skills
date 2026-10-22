"""ID onboarding is membership-only, portable and independent of model providers."""
import contextlib
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from quorum_test_support import isolated_profiles
from unittest.mock import patch

SCRIPTS = Path(__file__).resolve().parents[1] / 'skills/quorum/scripts'
sys.path.insert(0, str(SCRIPTS))
INVITE = '11111111-1111-4111-8111-111111111111'



class IdInvitationTests(unittest.TestCase):
    @isolated_profiles
    def test_owner_gets_public_id_and_ready_session_prompt_without_file(self):
        import quorum_invitations as invitations
        from quorum_api import ServiceConfig
        calls = []
        class Api:
            def call(self, op, payload, request_id=None):
                calls.append((op, payload, request_id))
                if op == 'quorum_lookup': return {'quorum_id': 'q'}
                if op == 'quorum_check_owner': return True
                if op == 'invite_create_id': return {'invite_id': INVITE, 'expires_at': 'tomorrow'}
                raise AssertionError(op)
        with tempfile.TemporaryDirectory() as root:
            result = invitations.create_id_invitation(Api(), ServiceConfig('https://example.com', 'sb_publishable_synthetic'),
                                                      'team', '123', 'r')
            self.assertEqual(result['invite_id'], INVITE)
            self.assertIn(INVITE, result['session_prompt'])
            self.assertEqual(result['quorum'], 'team')
            self.assertIn('quorum slug is \"team\"', result['session_prompt'])
            self.assertIn('references/join.md', result['session_prompt'])
            self.assertIn('rotnov/skills', result['session_prompt'])
            self.assertIn('session connect', result['session_prompt'])
            self.assertIn('session start', result['session_prompt'])
            self.assertIn('session status', result['session_prompt'])
            self.assertIn('Hello', result['session_prompt'])
            self.assertIn('not a temporary scratch folder', result['session_prompt'])
            self.assertNotIn('Accept membership only', result['session_prompt'])
            self.assertEqual(result['service'], {'origin':'https://example.com','publishable_key':'sb_publishable_synthetic'})
            self.assertNotIn('token', result)
            self.assertEqual(list(Path(root).iterdir()), [])
        self.assertEqual(calls[-1], ('invite_create_id', {'quorum_id': 'q', 'target_github_id': '123'}, 'r'))

    @isolated_profiles
    def test_prompts_select_first_or_configured_machine_and_name_target_account(self):
        from quorum_invitations import create_id_invitation
        from quorum_api import ServiceConfig
        class Api:
            def call(self, operation, payload, request_id=None):
                if operation == 'quorum_lookup': return {'quorum_id': 'q'}
                if operation == 'quorum_check_owner': return True
                return {'invite_id': INVITE, 'expires_at': '2026-10-23T12:00:00Z'}
        initial = create_id_invitation(Api(), ServiceConfig('https://example.com', 'sb_publishable_synthetic'),
                                       'team', '123', 'r')
        self.assertIn('configured_machine_prompt', initial, 'configured-machine invitation is missing')
        result = create_id_invitation(Api(), ServiceConfig('https://example.com', 'sb_publishable_synthetic'),
                                      'team', '123', 'r', github_user='Octocat')
        for key in ('session_prompt', 'configured_machine_prompt'):
            prompt = result[key]
            for required in ('@Octocat', 'GitHub ID 123', '2026-10-23T12:00:00Z',
                             'via Supabase Auth', 'no Supabase dashboard registration',
                             'session connect', 'session start', 'session status', 'Hello',
                             '--accept-tasks-from coordinator', 'without asking again for each assignment',
                             'Pin the resolved coordinator agent UUID locally'):
                self.assertIn(required, prompt)
            self.assertIn('GitHub is required', prompt)
            for required in ('https://github.com/rotnov/skills/issues', 'Check existing issues',
                             'one report per distinct failure', 'safe error code', 'actual listener state',
                             'no tokens', 'no GitHub write access'):
                self.assertIn(required, prompt)
        full, short = result['session_prompt'], result['configured_machine_prompt']
        self.assertIn('--copy -g -y', full)
        self.assertIn('--origin', full)
        self.assertIn('sb_publishable_synthetic', full)
        self.assertIn(INVITE, short)
        self.assertNotIn('--origin', short)
        self.assertNotIn('--publishable-key', short)
        self.assertNotIn('https://example.com', short)
        self.assertNotIn('skills@', short)
        self.assertEqual(result['target_github_user'], 'Octocat')

    @isolated_profiles
    def test_accept_bootstraps_own_login_and_only_accepts_membership(self):
        import quorum
        from quorum_state import LocalLedger
        calls = []
        class Store:
            backend = None
            def load(self, _): return None
        class Api:
            def call(self, op, payload, request_id=None):
                calls.append((op, payload, request_id))
                return {'quorum_id': 'q', 'membership_id': 'm'}
        with tempfile.TemporaryDirectory() as root:
            with patch('quorum_live.api_for', return_value=(Api(), __import__('quorum_api').ServiceConfig('https://example.com','sb_publishable_synthetic'), Store())), \
                    patch('quorum_auth.login_pkce', return_value={'authenticated': True}) as login, \
                    patch('quorum.register_agent', side_effect=AssertionError('registration forbidden')), \
                    patch('quorum_session.client_preflight', return_value='codex'), \
                    contextlib.redirect_stdout(io.StringIO()) as output:
                code = quorum.main(['--state', root, 'accept-id', INVITE, '--origin','https://example.com','--publishable-key','sb_publishable_synthetic'])
            self.assertEqual(code, 0, output.getvalue())
            self.assertEqual(calls, [('invite_accept_id', {'invite_id': INVITE}, 'accept-id:' + INVITE)])
            login.assert_called_once()
            self.assertTrue(json.loads(output.getvalue())['data']['membership_only'])
            self.assertEqual(set(json.loads((Path(root)/'config.json').read_text())), {'origin', 'publishable_key'})
            ledger = LocalLedger(Path(root))
            self.assertIsNone(ledger.setting('pilot-agent'))
            ledger.db.close()

    @isolated_profiles
    def test_open_invitation_prompt_uses_anonymous_acceptance(self):
        from quorum_invitations import create_open_invitation
        from quorum_api import ServiceConfig
        calls = []
        class Api:
            def call(self, op, payload, request_id=None):
                calls.append((op, payload, request_id))
                if op == 'quorum_lookup': return {'quorum_id': 'q'}
                if op == 'quorum_check_owner': return True
                return {'invite_id': INVITE, 'expires_at': '2026-10-22T15:15:00Z', 'token': 'unexpected'}
        from quorum_release import installed_release
        release = installed_release()
        self.assertEqual(release, {'protocol_version': 2, 'skill_version': '0.3.0'})
        result = create_open_invitation(Api(), ServiceConfig('https://example.com', 'sb_publishable_synthetic'), 'team', 'r')
        self.assertEqual(calls[-1], ('invite_create_open', {'quorum_id': 'q'}, 'r'))
        prompt = result['session_prompt']
        for required in (f'accept-id {INVITE} --anonymous', 'sb_publishable_synthetic', '2026-10-22T15:15:00Z',
                         'single-use', 'No GitHub login', 'fresh private state', 'session connect', 'Hello',
                         'https://github.com/rotnov/skills/issues',
                         f'protocol {release["protocol_version"]} and skill {release["skill_version"]}',
                         '--accept-tasks-from coordinator --dispatch-mode native-subagent',
                         'paste this prompt as my own instruction'):
            self.assertIn(required, prompt)
        self.assertNotIn('GitHub is required', prompt)
        self.assertNotIn('token', result)
        self.assertNotIn('unexpected', json.dumps(result))
        self.assertEqual(result['access'], 'open')

    @isolated_profiles
    def test_anonymous_accept_keeps_its_login_inside_a_fresh_state(self):
        import quorum
        from quorum_api import QuorumApi
        requests = []
        def request(url, headers, body):
            requests.append((url, json.loads(body)))
            if url.endswith('/auth/v1/signup'):
                self.assertNotIn('Authorization', headers)
                return {'access_token': 'anon-access', 'refresh_token': 'anon-refresh', 'expires_in': 3600}
            self.assertEqual(headers['Authorization'], 'Bearer anon-access')
            return {'quorum_id': 'q', 'membership_id': 'm'}
        real_init = QuorumApi.__init__
        def init(api, config, token, request_=None):
            real_init(api, config, token, request)
        # Auth-only: an explicit membership-only accept never depends on this host's native session.
        arguments = ['accept-id', INVITE, '--anonymous', '--membership-only', '--origin', 'https://example.com',
                     '--publishable-key', 'sb_publishable_synthetic']
        with tempfile.TemporaryDirectory() as root, \
                patch('quorum_api.request_json', side_effect=request), \
                patch('quorum_api.QuorumApi.__init__', init), \
                patch('quorum_auth.login_pkce', side_effect=AssertionError('no browser login')):
            with contextlib.redirect_stdout(io.StringIO()) as output:
                self.assertEqual(quorum.main(['--state', root, *arguments]), 0, output.getvalue())
            self.assertEqual([url for url, _ in requests],
                             ['https://example.com/auth/v1/signup', 'https://example.com/rest/v1/rpc/invite_accept_id'])
            self.assertTrue((Path(root) / 'anonymous-credentials').is_dir())
            registry = Path(os.environ['QUORUM_PROFILE_ROOT'])
            self.assertFalse(registry.exists() and any(registry.iterdir()), 'anonymous login entered the machine profile')
            # Retries reuse the stored identity; a lost one needs a new open invitation.
            with contextlib.redirect_stdout(io.StringIO()) as output:
                self.assertEqual(quorum.main(['--state', root, *arguments]), 0, output.getvalue())
            self.assertEqual(json.loads(output.getvalue())['data']['native_preflight'], 'skipped')
            self.assertEqual(sum(url.endswith('/signup') for url, _ in requests), 1)
            # A GitHub accept on this anonymous state conflicts at auth, not at native preflight.
            with contextlib.redirect_stdout(io.StringIO()) as output:
                self.assertEqual(quorum.main(['--state', root, 'accept-id', INVITE, '--membership-only']), 3)
            self.assertEqual(json.loads(output.getvalue())['code'], 'anonymous_state_conflict')
            for path in (Path(root) / 'anonymous-credentials').glob('*.json'): path.unlink()
            with contextlib.redirect_stdout(io.StringIO()) as output:
                self.assertEqual(quorum.main(['--state', root, 'login']), 3)
            self.assertEqual(json.loads(output.getvalue())['code'], 'open_invite_required')

    @isolated_profiles
    def test_anonymous_login_never_reconciles_with_the_machine_login(self):
        import quorum
        import time
        from quorum_api import QuorumApi, ServiceConfig
        from quorum_profiles import resolve_profile
        config = ServiceConfig('https://example.com', 'sb_publishable_synthetic')
        requests = []
        def request(url, headers, body):
            requests.append((url, headers.get('Authorization')))
            if url.endswith('/auth/v1/signup'):
                return {'access_token': 'anon-access', 'refresh_token': 'anon-refresh', 'expires_in': 3600}
            return {'quorum_id': 'q', 'membership_id': 'm'}
        real_init = QuorumApi.__init__
        def init(api, config, token, request_=None):
            real_init(api, config, token, request)
        with tempfile.TemporaryDirectory() as machine_state, tempfile.TemporaryDirectory() as root:
            # An established GitHub machine login for the same service.
            _, machine = resolve_profile(Path(machine_state), config)
            machine.save(config.origin, {'access_token': 'machine-access', 'refresh_token': 'machine-refresh',
                                         'expires_at': time.time() + 3600,
                                         'user': {'id': 'user-123', 'identities': [
                                             {'provider': 'github', 'id': '123', 'identity_data': {'provider_id': '123'}}]}})
            registry = Path(os.environ['QUORUM_PROFILE_ROOT'])
            before = {path: path.read_bytes() for path in registry.rglob('*') if path.is_file()}
            with patch('quorum_api.request_json', side_effect=request), \
                    patch('quorum_api.QuorumApi.__init__', init), \
                    patch('quorum_api.get_json', side_effect=AssertionError('principal verification')), \
                    patch('quorum_auth.login_pkce', side_effect=AssertionError('no browser login')):
                with contextlib.redirect_stdout(io.StringIO()) as output:
                    self.assertEqual(quorum.main(['--state', root, 'accept-id', INVITE, '--anonymous',
                                                  '--membership-only', '--origin', config.origin,
                                                  '--publishable-key', config.publishable_key]), 0,
                                     output.getvalue())
                # Explicit login may refresh a legacy GitHub family; here it only reuses this state's own login.
                with contextlib.redirect_stdout(io.StringIO()) as output:
                    self.assertEqual(quorum.main(['--state', root, 'login']), 0, output.getvalue())
                _, store = resolve_profile(Path(root), refresh_legacy=True)
            self.assertEqual(type(store).__name__, 'AnonymousCredentials')
            self.assertFalse((Path(root) / 'auth-binding.json').exists())
            self.assertFalse((Path(root) / 'credentials').exists())
            self.assertEqual({path: path.read_bytes() for path in registry.rglob('*') if path.is_file()}, before)
            self.assertNotIn('Bearer machine-access', [auth for _, auth in requests])
            self.assertEqual(sum(url.endswith('/signup') for url, _ in requests), 1)

    @isolated_profiles
    def test_anonymous_state_checks_session_requirements_with_its_own_login(self):
        import quorum
        from quorum_api import QuorumApi
        from quorum_live import api_for
        from quorum_release import ReleaseGuard, installed_release
        from quorum_state import LocalLedger
        requests = []
        def request(url, headers, body):
            requests.append((url, headers.get('Authorization')))
            if url.endswith('/auth/v1/signup'):
                return {'access_token': 'anon-access', 'refresh_token': 'anon-refresh', 'expires_in': 3600}
            if url.endswith('/rpc/session_requirements'): return installed_release()
            return {'quorum_id': 'q', 'membership_id': 'm'}
        real_init = QuorumApi.__init__
        def init(api, config, token, request_=None):
            real_init(api, config, token, request)
        with tempfile.TemporaryDirectory() as root, \
                patch('quorum_api.request_json', side_effect=request), \
                patch('quorum_api.QuorumApi.__init__', init), \
                patch('quorum_auth.login_pkce', side_effect=AssertionError('no browser login')):
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(quorum.main(['--state', root, 'accept-id', INVITE, '--anonymous', '--membership-only',
                                              '--origin', 'https://example.com',
                                              '--publishable-key', 'sb_publishable_synthetic']), 0)
            # Session connect/start/run resolve the same state: never the machine login.
            auth, session = LocalLedger(Path(root)), LocalLedger(Path(root) / 'sessions' / 'synthetic')
            try:
                api, _, store = api_for(auth)
                self.assertEqual(type(store).__name__, 'AnonymousCredentials')
                self.assertEqual(ReleaseGuard(api, session).check(), installed_release())
            finally:
                auth.db.close(); session.db.close()
        self.assertEqual(requests[-1], ('https://example.com/rest/v1/rpc/session_requirements', 'Bearer anon-access'))
        self.assertEqual(sum(url.endswith('/signup') for url, _ in requests), 1)

    @isolated_profiles
    def test_unsupported_client_stops_before_login_or_consuming_invitation(self):
        import quorum
        clean = {key: value for key, value in __import__('os').environ.items()
                 if key not in ('CODEX_THREAD_ID', 'CLAUDE_CODE_SESSION_ID', 'GROK_SESSION_ID')}
        with tempfile.TemporaryDirectory() as root:
            with patch.dict('os.environ', clean, clear=True), \
                    patch('quorum_auth.login_pkce', side_effect=AssertionError('must not login')), \
                    patch('quorum_api.request_json', side_effect=AssertionError('must not accept')), \
                    contextlib.redirect_stdout(io.StringIO()) as output:
                code = quorum.main(['--state', root, 'accept-id', INVITE, '--origin','https://example.com',
                                    '--publishable-key','sb_publishable_synthetic'])
            result = json.loads(output.getvalue())
            self.assertEqual((code, result['ok'], result['code']), (3, False, 'unsupported_client'))
            self.assertEqual(result['data']['reason'], 'no_client_session')
            self.assertIn('grok', {row['client'] for row in result['data']['supported_transports']})
            self.assertNotIn(root, output.getvalue())
        with tempfile.TemporaryDirectory() as root, tempfile.TemporaryDirectory() as home:
            # A Grok chat without this session's private leader binding is not a transport.
            grok = {**clean, 'GROK_SESSION_ID': INVITE, 'GROK_HOME': home}
            with patch.dict('os.environ', grok, clear=True), patch('sys.platform', 'darwin'), \
                    patch('quorum_auth.login_pkce', side_effect=AssertionError('must not login')), \
                    patch('quorum_api.request_json', side_effect=AssertionError('must not accept')), \
                    contextlib.redirect_stdout(io.StringIO()) as output:
                code = quorum.main(['--state', root, 'accept-id', INVITE, '--origin','https://example.com',
                                    '--publishable-key','sb_publishable_synthetic'])
            result = json.loads(output.getvalue())
            self.assertEqual((code, result['code'], result['data']['reason']),
                             (3, 'unsupported_client', 'grok_leader_unavailable'))
            self.assertNotIn(home, output.getvalue())

    @isolated_profiles
    def test_explicit_membership_only_skips_native_preflight_but_not_auth(self):
        import quorum
        calls = []
        class Store:
            backend = None
            def load(self, _): return None
        class Api:
            def call(self, op, payload, request_id=None):
                calls.append((op, payload, request_id))
                return {'quorum_id': 'q', 'membership_id': 'm'}
        clean = {key: value for key, value in __import__('os').environ.items()
                 if key not in ('CODEX_THREAD_ID', 'CLAUDE_CODE_SESSION_ID', 'GROK_SESSION_ID')}
        with tempfile.TemporaryDirectory() as root:
            with patch.dict('os.environ', clean, clear=True), \
                    patch('quorum_live.api_for', return_value=(Api(), __import__('quorum_api').ServiceConfig('https://example.com','sb_publishable_synthetic'), Store())), \
                    patch('quorum_auth.login_pkce', return_value={'authenticated': True}) as login, \
                    patch('quorum.register_agent', side_effect=AssertionError('registration forbidden')), \
                    patch('quorum_session.client_preflight', side_effect=AssertionError('preflight skipped')), \
                    contextlib.redirect_stdout(io.StringIO()) as output:
                code = quorum.main(['--state', root, 'accept-id', INVITE, '--membership-only', '--origin',
                                    'https://example.com', '--publishable-key', 'sb_publishable_synthetic'])
            self.assertEqual(code, 0, output.getvalue())
            login.assert_called_once()
            self.assertEqual(calls, [('invite_accept_id', {'invite_id': INVITE}, 'accept-id:' + INVITE)])
            data = json.loads(output.getvalue())['data']
            self.assertEqual((data['membership_only'], data['native_preflight']), (True, 'skipped'))
        with tempfile.TemporaryDirectory() as root:
            # Membership-only still validates the invitation ID before login.
            with patch('quorum_auth.login_pkce', side_effect=AssertionError('must not login')), \
                    contextlib.redirect_stdout(io.StringIO()) as output:
                code = quorum.main(['--state', root, 'accept-id', 'not-a-uuid', '--membership-only', '--origin',
                                    'https://example.com', '--publishable-key', 'sb_publishable_synthetic'])
            self.assertNotEqual(code, 0)
            self.assertFalse(json.loads(output.getvalue())['ok'])

    @isolated_profiles
    def test_anonymous_accept_never_converts_a_github_state(self):
        import quorum
        with tempfile.TemporaryDirectory() as root:
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(quorum.main(['--state', root, 'configure', '--origin', 'https://example.com',
                                              '--publishable-key', 'sb_publishable_synthetic']), 0)
            with patch('quorum_api.request_json', side_effect=AssertionError('no network')), \
                    contextlib.redirect_stdout(io.StringIO()) as output:
                self.assertEqual(quorum.main(['--state', root, 'accept-id', INVITE, '--anonymous']), 3)
            self.assertEqual(json.loads(output.getvalue())['code'], 'anonymous_state_conflict')
            self.assertFalse((Path(root) / 'anonymous-credentials').exists())

    @isolated_profiles
    def test_disabled_anonymous_sign_in_and_missing_browser_have_safe_codes(self):
        import quorum
        from quorum_api import QuorumError
        with tempfile.TemporaryDirectory() as root:
            with patch('quorum_api.request_json', side_effect=QuorumError('service_error')), \
                    contextlib.redirect_stdout(io.StringIO()) as output:
                self.assertEqual(quorum.main(['--state', root, 'accept-id', INVITE, '--anonymous', '--membership-only',
                                              '--origin', 'https://example.com',
                                              '--publishable-key', 'sb_publishable_synthetic']), 4)
            self.assertEqual(json.loads(output.getvalue())['code'], 'anonymous_login_unavailable')
        with tempfile.TemporaryDirectory() as root:
            with patch('webbrowser.open', return_value=False), \
                    contextlib.redirect_stdout(io.StringIO()) as output:
                self.assertEqual(quorum.main(['--state', root, 'accept-id', INVITE, '--membership-only', '--origin',
                                              'https://example.com', '--publishable-key', 'sb_publishable_synthetic']), 3)
            self.assertEqual(json.loads(output.getvalue())['code'], 'browser_unavailable')

    @isolated_profiles
    def test_auth_only_accepts_are_independent_of_inherited_native_exports(self):
        import quorum
        from quorum_api import QuorumApi
        from quorum_session import current_session
        native = ('CODEX_THREAD_ID', 'CLAUDE_CODE_SESSION_ID', 'CLAUDE_CODE_HOST_SESSION_ID',
                  'CLAUDE_CODE_MESSAGING_SOCKET', 'CLAUDE_CODE_MESSAGING_TOKEN', 'GROK_SESSION_ID',
                  'GROK_LEADER_SOCKET')
        flags = ['--membership-only', '--origin', 'https://example.com', '--publishable-key', 'sb_publishable_synthetic']
        requests = []
        def request(url, headers, body):
            requests.append(url)
            if url.endswith('/auth/v1/signup'):
                return {'access_token': 'anon-access', 'refresh_token': 'anon-refresh', 'expires_in': 3600}
            return {'quorum_id': 'q', 'membership_id': 'm'}
        real_init = QuorumApi.__init__
        def init(api, config, token, request_=None):
            real_init(api, config, token, request)
        # CI exports no native session; a local chat may export a valid Codex SID.
        for codex_sid in (None, '22222222-2222-4222-8222-222222222222'):
            requests.clear()
            with self.subTest(codex_sid_set=codex_sid is not None), patch.dict(os.environ), \
                    patch('quorum_session.client_preflight', side_effect=AssertionError('native preflight')):
                for key in native: os.environ.pop(key, None)
                if codex_sid: os.environ['CODEX_THREAD_ID'] = codex_sid
                self.assertEqual(os.environ.get('CODEX_THREAD_ID'), codex_sid)
                if codex_sid: self.assertEqual(current_session(), codex_sid)
                with tempfile.TemporaryDirectory() as root, \
                        patch('quorum_api.request_json', side_effect=request), \
                        patch('quorum_api.QuorumApi.__init__', init), \
                        patch('quorum_auth.login_pkce', side_effect=AssertionError('no browser login')):
                    for _ in range(2):
                        with contextlib.redirect_stdout(io.StringIO()) as output:
                            self.assertEqual(quorum.main(['--state', root, 'accept-id', INVITE, '--anonymous', *flags]),
                                             0, output.getvalue())
                        data = json.loads(output.getvalue())['data']
                        self.assertEqual((data['membership_only'], data['native_preflight']), (True, 'skipped'))
                    self.assertEqual(sum(url.endswith('/signup') for url in requests), 1)
                    with contextlib.redirect_stdout(io.StringIO()) as output:
                        self.assertEqual(quorum.main(['--state', root, 'accept-id', INVITE, '--membership-only']), 3)
                    self.assertEqual(json.loads(output.getvalue())['code'], 'anonymous_state_conflict')
                with tempfile.TemporaryDirectory() as root, patch('webbrowser.open', return_value=False), \
                        contextlib.redirect_stdout(io.StringIO()) as output:
                    self.assertEqual(quorum.main(['--state', root, 'accept-id', INVITE, *flags]), 3)
                self.assertEqual(json.loads(output.getvalue())['code'], 'browser_unavailable')

    @isolated_profiles
    def test_headless_login_failures_report_fixed_codes_for_login_and_accept(self):
        import itertools
        import types
        import quorum
        class Loopback:
            # No real callback port: a headless host never reaches it.
            def __init__(self, *_): pass
            def __enter__(self): return self
            def __exit__(self, *_): return False
            def handle_request(self): pass
        flags = ['--origin', 'https://example.com', '--publishable-key', 'sb_publishable_synthetic']
        cases = (({'return_value': False}, 'browser_unavailable'), ({'return_value': True}, 'login_timeout'))
        for browser, expected in cases:
            for command in (['accept-id', INVITE, *flags], ['login']):
                clock = types.SimpleNamespace(monotonic=itertools.count(0, 200).__next__)
                with self.subTest(code=expected, command=command[0]), tempfile.TemporaryDirectory() as root, \
                        patch('http.server.HTTPServer', Loopback), patch('webbrowser.open', **browser), \
                        patch('quorum_auth.time', clock), \
                        patch('quorum_session.client_preflight', return_value='codex'), \
                        patch('quorum_api.request_json', side_effect=AssertionError('no network')):
                    if command == ['login']:
                        with contextlib.redirect_stdout(io.StringIO()):
                            self.assertEqual(quorum.main(['--state', root, 'configure', *flags]), 0)
                    with contextlib.redirect_stdout(io.StringIO()) as output:
                        self.assertEqual(quorum.main(['--state', root, *command]), 3)
                    self.assertEqual(json.loads(output.getvalue()), {'ok': False, 'code': expected, 'data': {}})

    @isolated_profiles
    def test_owner_access_commands_select_open_mode(self):
        import quorum
        calls = []
        class Api:
            def call(self, op, payload, request_id=None):
                calls.append((op, payload, request_id))
                if op == 'quorum_lookup': return {'quorum_id': 'q'}
                if op == 'quorum_create': return {'quorum_id': 'q', 'membership_id': 'm'}
                return {'quorum_id': 'q', 'access': payload['access']}
        with tempfile.TemporaryDirectory() as root, \
                patch('quorum_live.api_for', return_value=(Api(), None, None)), \
                patch('quorum_profiles.resolve_profile', return_value=(None, None)), \
                patch('quorum.QuorumApi', return_value=Api()), patch('quorum.token_provider'):
            with contextlib.redirect_stdout(io.StringIO()) as output:
                self.assertEqual(quorum.main(['--state', root, 'access', 'team', 'github']), 0)
            self.assertEqual(json.loads(output.getvalue())['data']['access'], 'github')
            with patch('sys.stdin', io.TextIOWrapper(io.BytesIO(b'{}'))), \
                    contextlib.redirect_stdout(io.StringIO()) as output:
                self.assertEqual(quorum.main(['--state', root, 'create', 'team', '--access', 'open',
                                              '--json-stdin', '--request-id', 'c']), 0)
            self.assertEqual(json.loads(output.getvalue())['data']['access'], 'open')
        self.assertEqual(calls, [('quorum_lookup', {'slug': 'team'}, None),
                                 ('quorum_set_access', {'quorum_id': 'q', 'access': 'github'}, 'access:q'),
                                 ('quorum_create', {'slug': 'team'}, 'c'),
                                 ('quorum_set_access', {'quorum_id': 'q', 'access': 'open'}, 'access:q')])

    @isolated_profiles
    def test_invalid_id_and_service_conflict_stop_before_login(self):
        import quorum
        with tempfile.TemporaryDirectory() as root:
            with patch('quorum_auth.login_pkce', side_effect=AssertionError('must not login')), \
                    contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(quorum.main(['--state', root, 'accept-id', 'not-an-id', '--origin','https://example.com','--publishable-key','sb_publishable_synthetic']), 3)
                path = Path(root)/'config.json'
                path.write_text(json.dumps({'origin':'https://different.invalid','publishable_key':'sb_publishable_synthetic'}))
                self.assertEqual(quorum.main(['--state', root, 'accept-id', INVITE, '--origin','https://example.com','--publishable-key','sb_publishable_synthetic']), 3)

    @isolated_profiles
    def test_no_bundled_service_or_implicit_backend(self):
        import quorum
        with tempfile.TemporaryDirectory() as root:
            with contextlib.redirect_stdout(io.StringIO()) as output:
                self.assertEqual(quorum.main(['--state',root,'accept-id',INVITE]),4)
            self.assertEqual(json.loads(output.getvalue())['code'],'service_not_configured')
            with contextlib.redirect_stdout(io.StringIO()) as output:
                self.assertEqual(quorum.main(['--state',root,'invite-id','team','--github-id','123','--request-id','r']),4)
            self.assertEqual(json.loads(output.getvalue())['code'],'service_not_configured')
        self.assertFalse((SCRIPTS.parent/'assets/service-profiles.json').exists())

    @isolated_profiles
    def test_invitation_never_publishes_secret_or_session_key(self):
        from quorum_invitations import create_id_invitation
        from quorum_api import ServiceConfig
        import base64
        claims = base64.urlsafe_b64encode(json.dumps({'role': 'service_role'}).encode()).decode().rstrip('=')
        service_key = '.'.join(('e30', claims, 'x'))  # Unsigned synthetic JWT.
        for key in ('sb_secret_x', service_key, 'provider-token'):
            with self.assertRaisesRegex(ValueError,'public_service_key_required'):
                create_id_invitation(None,ServiceConfig('https://example.com',key),'team','123','r')

    @isolated_profiles
    def test_api_allows_explicit_id_operations(self):
        from quorum_api import QuorumApi, ServiceConfig
        calls=[]
        api=QuorumApi(ServiceConfig('https://example.com','sb_publishable_synthetic'), lambda:'own-access',
                      request=lambda url, headers, body: calls.append((url,json.loads(body))) or {})
        api.call('invite_accept_id', {'invite_id':INVITE}, 'r')
        self.assertEqual(calls[0][1], {'p_invite_id':INVITE,'p_request_id':'r'})

    @isolated_profiles
    def test_clean_copy_cli_bootstraps_without_credentials_or_providers(self):
        # Runs unchanged on native Windows CI and on both CLI-installed clients.
        with tempfile.TemporaryDirectory() as root:
            root=Path(root)
            installed=root/'quorum'
            shutil.copytree(SCRIPTS.parent, installed, ignore=shutil.ignore_patterns('__pycache__','.venv'))
            state=root/'private state'
            result=subprocess.run([sys.executable,str(installed/'scripts/quorum.py'),'--state',str(state),
                                   'configure','--origin','https://example.com','--publishable-key','sb_publishable_synthetic'],cwd=root,capture_output=True,text=True)
            self.assertEqual(result.returncode,0,result.stdout+result.stderr)
            self.assertTrue(json.loads(result.stdout)['ok'])
            self.assertFalse((state/'credentials').exists())
            self.assertTrue((installed/'references/invite.md').is_file())
            self.assertTrue((installed/'references/join.md').is_file())
            help_result=subprocess.run([sys.executable,str(installed/'scripts/quorum.py'),'accept-id','--help'],
                                       cwd=root,capture_output=True,text=True)
            self.assertEqual(help_result.returncode,0)


class CliLedgerLifetimeTests(unittest.TestCase):
    @isolated_profiles
    def test_main_closes_its_ledger_on_success_and_handled_errors(self):
        import quorum
        import sqlite3
        from quorum_state import LocalLedger
        cases = (
            (['status'], 0),
            (['accept-id', INVITE], 4),
            (['configure', '--origin', 'http://example.invalid', '--publishable-key', 'synthetic'], 3),
        )
        for arguments, expected in cases:
            with self.subTest(arguments=arguments), tempfile.TemporaryDirectory() as root:
                ledger = LocalLedger(Path(root))
                try:
                    # Retain the real connection: GC must not determine its lifetime.
                    with patch('quorum.LocalLedger', return_value=ledger), \
                            contextlib.redirect_stdout(io.StringIO()):
                        self.assertEqual(quorum.main(['--state', root, *arguments]), expected)
                    with self.assertRaisesRegex(sqlite3.ProgrammingError, 'closed'):
                        ledger.db.execute('select 1')
                finally:
                    ledger.db.close()

    @isolated_profiles
    def test_live_dispatch_keeps_ledger_open_until_return_or_interruption(self):
        import quorum
        import sqlite3
        from quorum_state import LocalLedger
        for command, failure in (('accept-id', None), ('mcp', None),
                                 ('accept-id', RuntimeError('synthetic')),
                                 ('accept-id', KeyboardInterrupt())):
            with self.subTest(command=command, failure=type(failure)), tempfile.TemporaryDirectory() as root:
                ledger = LocalLedger(Path(root))
                def dispatch(args, active_ledger, register_agent):
                    self.assertIs(active_ledger, ledger)
                    active_ledger.setting('dispatch-active', {'open': True})
                    if failure is not None: raise failure
                    return {'membership_only': True}
                arguments = ['accept-id', INVITE] if command == 'accept-id' else ['mcp', '--agent-id', 'synthetic']
                try:
                    with patch('quorum.LocalLedger', return_value=ledger), \
                            patch('quorum_live.dispatch', side_effect=dispatch), \
                            contextlib.redirect_stdout(io.StringIO()):
                        if failure is None:
                            self.assertEqual(quorum.main(['--state', root, *arguments]), 0)
                        else:
                            with self.assertRaises(type(failure)):
                                quorum.main(['--state', root, *arguments])
                    with self.assertRaisesRegex(sqlite3.ProgrammingError, 'closed'):
                        ledger.db.execute('select 1')
                finally:
                    ledger.db.close()


if __name__ == '__main__': unittest.main()
