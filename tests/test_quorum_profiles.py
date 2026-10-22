"""Shared logins retain one rotating token family across exact state bindings."""
import concurrent.futures
import contextlib
import io
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'skills/quorum/scripts'))
from quorum_api import ServiceConfig, QuorumError
from quorum_auth import CredentialStore, token_provider

ORIGIN = 'https://one.example'
CONFIG = ServiceConfig(ORIGIN, 'sb_publishable_synthetic')


def session(access='access', refresh='refresh', expires=10000, identity='123', user_id='user-123'):
    return {'access_token': access, 'refresh_token': refresh, 'expires_at': expires,
            'user': {'id': user_id, 'identities': [{'provider': 'github', 'id': identity,
                                                    'identity_data': {'provider_id': identity}}]}}


def auth_server(users):
    """Stand-in Auth /user endpoint: only listed access tokens verify."""
    calls = []
    def get_json(url, headers):
        token = headers['Authorization'].removeprefix('Bearer ')
        calls.append((url, token))
        if token not in users: raise QuorumError('auth_required')
        return users[token]
    return get_json, calls


class MachineProfileTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name).resolve()
        self.env = patch.dict(os.environ, {'QUORUM_PROFILE_ROOT': str(self.root / 'machine')})
        self.env.start()
        self.addCleanup(self.env.stop)

    def resolve(self, state='state-one', config=CONFIG):
        self.assertIsNotNone(importlib.util.find_spec('quorum_profiles'), 'shared machine profile support is missing')
        from quorum_profiles import resolve_profile
        return resolve_profile(self.root / state, config)

    def test_new_state_reuses_selected_service_and_same_token_family_without_browser(self):
        # A state-local store here would lose auth and require another browser.
        config, first = self.resolve()
        first.save(config.origin, session())
        with patch('quorum_auth.login_pkce', side_effect=AssertionError('duplicate browser')):
            config, second = self.resolve('state-two', None)
            self.assertEqual(token_provider(config, second, clock=lambda: 100)(), 'access')
        self.assertEqual(first.backend.root, second.backend.root)
        self.assertFalse((self.root / 'state-two' / 'credentials').exists())
        self.assertEqual(json.loads((self.root / 'state-two/config.json').read_text())['origin'], ORIGIN)

    def test_legacy_adoption_keeps_original_path_and_observes_old_listener_rotations(self):
        (self.root / 'state-one').mkdir(mode=0o700)
        legacy = CredentialStore(root=self.root / 'state-one/credentials')
        legacy.save(ORIGIN, session())
        config, shared = self.resolve()
        self.assertEqual(shared.backend.root, legacy.backend.root)
        legacy.save(ORIGIN, session('rotated', 'rotated-refresh'))
        _, next_state = self.resolve('state-two', None)
        self.assertEqual(token_provider(config, next_state, clock=lambda: 100)(), 'rotated')
        self.assertEqual(list((self.root / 'machine').rglob('*.json')).__len__(), 1)

    def test_distinct_existing_login_is_not_overridden_or_copied(self):
        _, first = self.resolve()
        first.save(ORIGIN, session())
        (self.root / 'state-two').mkdir(mode=0o700)
        legacy = CredentialStore(root=self.root / 'state-two/credentials')
        legacy.save(ORIGIN, session('other', 'other-refresh', identity='456'))
        with self.assertRaisesRegex(ValueError, 'auth_profile_conflict'):
            self.resolve('state-two')
        self.assertEqual(legacy.load(ORIGIN)['access_token'], 'other')
        self.assertEqual(first.load(ORIGIN)['access_token'], 'access')

    def legacy_state(self, name='state-two', **values):
        state = self.root / name
        state.mkdir(mode=0o700)
        (state / 'ledger.sqlite3').write_bytes(b'per-session ledger, device and grants')
        legacy = CredentialStore(root=state / 'credentials')
        legacy.save(ORIGIN, session(**{'access': 'legacy-access', 'refresh': 'legacy-refresh', **values}))
        return state, legacy, self.snapshot(state)

    @staticmethod
    def snapshot(state):
        return {path: path.read_bytes() for path in state.rglob('*') if path.is_file()}

    def assertUnchanged(self, before):
        # New binding/config/ledger files may appear; existing bytes may not change.
        self.assertEqual({path: path.read_bytes() if path.exists() else None for path in before}, before)

    def test_same_principal_legacy_state_reuses_verified_machine_login_without_browser(self):
        config, canonical = self.resolve()
        canonical.save(ORIGIN, session(expires=time.time() + 3600))
        state, legacy, before = self.legacy_state(expires=time.time() + 3600)
        user = session()['user']
        get_json, calls = auth_server({'access': user, 'legacy-access': user})
        with patch('quorum_api.get_json', side_effect=get_json), \
                patch('quorum_auth.login_pkce', side_effect=AssertionError('duplicate browser')), \
                patch('quorum_api.request_json', side_effect=AssertionError('unexpected refresh')):
            _, bound = self.resolve('state-two', None)
            self.assertEqual(token_provider(config, bound, clock=lambda: 100)(), 'access')
        self.assertEqual(bound.backend.root, canonical.backend.root)
        self.assertEqual(sorted(token for _, token in calls), ['access', 'legacy-access'])
        self.assertTrue(all(url == ORIGIN + '/auth/v1/user' for url, _ in calls))
        # Legacy token family, ledger, device and grants stay exactly in place.
        self.assertUnchanged(before)
        self.assertEqual(legacy.load(ORIGIN)['refresh_token'], 'legacy-refresh')
        binding = state / 'auth-binding.json'
        self.assertNotIn('legacy', binding.read_text())
        if os.name != 'nt': self.assertEqual(binding.stat().st_mode & 0o777, 0o600)
        # The verified binding is durable: later helpers need no network proof.
        with patch('quorum_api.get_json', side_effect=AssertionError('reverified')):
            _, again = self.resolve('state-two', None)
        self.assertEqual(again.backend.root, canonical.backend.root)

    def test_verified_different_principal_stays_a_conflict_with_redacted_diagnostic(self):
        import quorum
        _, canonical = self.resolve()
        canonical.save(ORIGIN, session(expires=time.time() + 3600))
        # The stored claim matches, but the Auth server is authoritative.
        cases = {'other account': session(identity='456', user_id='user-456')['user'],
                 'same GitHub ID, other principal': session(user_id='user-999')['user']}
        for index, (name, legacy_user) in enumerate(cases.items()):
            with self.subTest(name):
                state, legacy, before = self.legacy_state('legacy-' + str(index), expires=time.time() + 3600)
                get_json, _ = auth_server({'access': session()['user'], 'legacy-access': legacy_user})
                with patch('quorum_api.get_json', side_effect=get_json), \
                        patch('quorum_auth.login_pkce', side_effect=AssertionError('browser')), \
                        contextlib.redirect_stdout(io.StringIO()) as output:
                    self.assertEqual(quorum.main(['--state', str(state), 'login']), 3)
                result = json.loads(output.getvalue())
                self.assertEqual(result['code'], 'auth_profile_conflict')
                self.assertEqual(result['data']['reason'], 'different_principal')
                self.assertEqual(result['data']['state_credentials'], str(state / 'credentials'))
                self.assertEqual(result['data']['machine_credentials'], str(canonical.backend.root))
                self.assertTrue(result['data']['action'])
                for secret in ('legacy-access', 'legacy-refresh', '"access"', '"refresh"'):
                    self.assertNotIn(secret, output.getvalue())
                self.assertUnchanged(before)
                self.assertFalse((state / 'auth-binding.json').exists())
        self.assertEqual(canonical.load(ORIGIN)['access_token'], 'access')

    def test_unverified_legacy_claim_can_only_block_and_never_links(self):
        from quorum_profiles import AuthProfileConflict
        _, canonical = self.resolve()
        canonical.save(ORIGIN, session(expires=time.time() + 3600))
        state, _, before = self.legacy_state(identity='456', expires=time.time() + 3600)
        with patch('quorum_api.get_json', side_effect=AssertionError('network for a denied claim')):
            with self.assertRaises(AuthProfileConflict) as error:
                self.resolve('state-two', None)
        self.assertEqual(error.exception.diagnostic()['reason'], 'different_principal')
        self.assertUnchanged(before)

    def test_expired_legacy_login_is_never_refreshed_implicitly_but_explicit_login_reconciles(self):
        from quorum_profiles import AuthProfileConflict, resolve_profile
        config, canonical = self.resolve()
        canonical.save(ORIGIN, session(expires=time.time() + 3600))
        state, legacy, before = self.legacy_state(expires=1)
        user = session()['user']
        get_json, _ = auth_server({'access': user, 'fresh-legacy': user})
        with patch('quorum_api.get_json', side_effect=get_json), \
                patch('quorum_api.request_json', side_effect=AssertionError('implicit legacy refresh')):
            with self.assertRaises(AuthProfileConflict) as error:
                self.resolve('state-two', None)
        diagnostic = error.exception.diagnostic()
        self.assertEqual(diagnostic['reason'], 'legacy_login_unverified')
        self.assertIn('login', diagnostic['action'])
        self.assertUnchanged(before)
        refreshes = []
        def refresh(url, headers, body):
            refreshes.append(json.loads(body))
            return {**session('fresh-legacy', 'rotated-legacy'), 'expires_in': 3600}
        with patch('quorum_api.get_json', side_effect=get_json), patch('quorum_api.request_json', side_effect=refresh), \
                patch('quorum_auth.login_pkce', side_effect=AssertionError('duplicate browser')):
            _, bound = resolve_profile(state, refresh_legacy=True)
        self.assertEqual(refreshes, [{'refresh_token': 'legacy-refresh'}])
        # The rotation is persisted to its own family, never copied elsewhere.
        self.assertEqual(legacy.load(ORIGIN)['refresh_token'], 'rotated-legacy')
        self.assertEqual(canonical.load(ORIGIN)['refresh_token'], 'refresh')
        self.assertEqual(bound.backend.root, canonical.backend.root)
        self.assertEqual(len(list((self.root / 'machine').rglob('*.json'))), 2)

    def test_reconciliation_and_concurrent_helper_refresh_the_machine_login_once(self):
        config, canonical = self.resolve()
        canonical.save(ORIGIN, session(expires=1))
        self.legacy_state(expires=time.time() + 3600)
        user = session()['user']
        get_json, _ = auth_server({'fresh': user, 'legacy-access': user})
        requests = []
        def refresh(url, headers, body):
            requests.append(json.loads(body))
            time.sleep(.1)
            return {**session('fresh', 'rotated'), 'expires_in': 3600}
        def reconcile(_):
            return self.resolve('state-two', None)[1].backend.root
        def helper(_):
            return token_provider(config, canonical)()
        with patch('quorum_api.get_json', side_effect=get_json), patch('quorum_api.request_json', side_effect=refresh), \
                concurrent.futures.ThreadPoolExecutor(2) as pool:
            bound, token = pool.submit(reconcile, None), pool.submit(helper, None)
            self.assertEqual(bound.result(), canonical.backend.root)
            self.assertEqual(token.result(), 'fresh')
        self.assertEqual(requests, [{'refresh_token': 'refresh'}])
        self.assertEqual(canonical.load(ORIGIN)['refresh_token'], 'rotated')

    def test_tampered_binding_or_missing_machine_login_fails_closed(self):
        from quorum_profiles import AuthProfileConflict
        _, canonical = self.resolve()
        canonical.save(ORIGIN, session(expires=time.time() + 3600))
        state, _, _ = self.legacy_state(expires=time.time() + 3600)
        user = session()['user']
        get_json, _ = auth_server({'access': user, 'legacy-access': user})
        with patch('quorum_api.get_json', side_effect=get_json):
            self.resolve('state-two', None)
        binding = state / 'auth-binding.json'
        record = json.loads(binding.read_text())
        binding.unlink()
        fd = os.open(binding, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, 'w') as output: json.dump({**record, 'credential_root': str(state / 'credentials')}, output)
        with self.assertRaises(AuthProfileConflict) as error:
            self.resolve('state-two', None)
        self.assertEqual(error.exception.diagnostic()['reason'], 'binding_mismatch')
        other, _, _ = self.legacy_state('state-three', expires=time.time() + 3600)
        canonical.delete(ORIGIN)
        with patch('quorum_api.get_json', side_effect=get_json), \
                patch('quorum_auth.login_pkce', side_effect=AssertionError('browser')):
            with self.assertRaises(AuthProfileConflict) as error:
                self.resolve('state-three', None)
        self.assertEqual(error.exception.diagnostic()['reason'], 'machine_login_missing')
        self.assertFalse((other / 'auth-binding.json').exists())

    def bind_state_two(self, canonical, user=None):
        state, legacy, before = self.legacy_state(expires=time.time() + 3600)
        user = user or session()['user']
        get_json, _ = auth_server({'access': user, 'legacy-access': user})
        with patch('quorum_api.get_json', side_effect=get_json):
            _, bound = self.resolve('state-two', None)
        return state, legacy, before, bound

    def test_binding_pins_the_server_verified_auth_principal(self):
        _, canonical = self.resolve()
        canonical.save(ORIGIN, session(expires=time.time() + 3600))
        state, _, _, _ = self.bind_state_two(canonical)
        binding = json.loads((state / 'auth-binding.json').read_text())
        self.assertEqual(binding['auth_user_id'], 'user-123')
        self.assertEqual(binding['github_id'], '123')
        # The released registry schema is unchanged, so older listeners keep reading it.
        self.assertEqual(set(canonical.profiles.read()[ORIGIN]), {'publishable_key', 'credential_root', 'github_id'})

    def test_same_github_new_auth_principal_after_logout_never_reuses_old_binding(self):
        from quorum_profiles import AuthProfileConflict, ensure_login
        config, canonical = self.resolve()
        canonical.save(ORIGIN, session(expires=time.time() + 3600))
        state, legacy, before, bound = self.bind_state_two(canonical)
        old_binding = (state / 'auth-binding.json').read_bytes()
        canonical.delete(ORIGIN)
        # The service account was recreated: same GitHub ID, different Auth user.
        rotated = session('new-access', 'new-refresh', expires=time.time() + 3600, user_id='user-999')
        with patch('quorum_auth.login_pkce', side_effect=lambda config, store: store.save(config.origin, rotated)):
            ensure_login(config, canonical, explicit=True)  # The human's new account is not locked out.
        self.assertEqual(canonical.load(ORIGIN)['access_token'], 'new-access')
        # A helper bound before the rotation must not route through the new principal.
        with self.assertRaisesRegex(ValueError, 'auth_profile_conflict'):
            bound.load(ORIGIN)
        with self.assertRaisesRegex(ValueError, 'auth_profile_conflict'):
            token_provider(config, bound)()
        get_json, calls = auth_server({'new-access': rotated['user'], 'legacy-access': session()['user']})
        with patch('quorum_api.get_json', side_effect=get_json), \
                patch('quorum_auth.login_pkce', side_effect=AssertionError('browser')):
            with self.assertRaises(AuthProfileConflict) as error:
                self.resolve('state-two', None)
        self.assertEqual(error.exception.diagnostic()['reason'], 'different_principal')
        self.assertEqual(sorted(token for _, token in calls), ['legacy-access', 'new-access'])
        self.assertEqual((state / 'auth-binding.json').read_bytes(), old_binding)
        self.assertUnchanged(before)
        self.assertEqual(legacy.load(ORIGIN)['refresh_token'], 'legacy-refresh')
        # A deleted old account cannot be verified either; it is never routed.
        get_json, _ = auth_server({'new-access': rotated['user']})
        with patch('quorum_api.get_json', side_effect=get_json):
            with self.assertRaises(AuthProfileConflict) as error:
                self.resolve('state-two', None)
        self.assertEqual(error.exception.diagnostic()['reason'], 'legacy_login_unverified')

    def test_bound_state_rejects_a_rotated_principal_while_the_machine_login_is_missing(self):
        config, canonical = self.resolve()
        canonical.save(ORIGIN, session(expires=time.time() + 3600))
        _, _, _, _ = self.bind_state_two(canonical)
        canonical.delete(ORIGIN)
        with patch('quorum_api.get_json', side_effect=AssertionError('network')):
            _, bound = self.resolve('state-two', None)
        with self.assertRaisesRegex(QuorumError, 'auth_required'):
            token_provider(config, bound)()
        canonical.save(ORIGIN, session('new-access', expires=time.time() + 3600, user_id='user-999'))
        with self.assertRaisesRegex(ValueError, 'auth_profile_conflict'):
            token_provider(config, bound)()

    def test_deleted_legacy_login_keeps_the_binding_pinned_against_a_new_principal(self):
        from quorum_profiles import AuthProfileConflict
        _, canonical = self.resolve()
        canonical.save(ORIGIN, session(expires=time.time() + 3600))
        state, legacy, _, _ = self.bind_state_two(canonical)
        binding = (state / 'auth-binding.json').read_bytes()
        legacy.delete(ORIGIN)  # An older helper's logout removes only its own token.
        canonical.delete(ORIGIN)
        canonical.save(ORIGIN, session('new-access', 'new-refresh', expires=time.time() + 3600, user_id='user-999'))
        with patch('quorum_api.get_json', side_effect=AssertionError('network')), \
                patch('quorum_auth.login_pkce', side_effect=AssertionError('browser')):
            with self.assertRaises(AuthProfileConflict) as error:
                self.resolve('state-two', None)
        self.assertEqual(error.exception.diagnostic()['reason'], 'bound_principal_unverifiable')
        self.assertNotIn('new-access', json.dumps(error.exception.diagnostic()))
        self.assertEqual((state / 'auth-binding.json').read_bytes(), binding)
        self.assertIsNone(legacy.load(ORIGIN))
        # Logged out, the state stays pinned: a new principal cannot be saved through it.
        canonical.delete(ORIGIN)
        _, bound = self.resolve('state-two', None)
        with self.assertRaisesRegex(ValueError, 'auth_profile_conflict'):
            bound.save(ORIGIN, session('other', expires=time.time() + 3600, user_id='user-999'))
        self.assertIsNone(canonical.load(ORIGIN))

    def test_deleted_legacy_login_continues_with_the_same_pinned_principal(self):
        config, canonical = self.resolve()
        canonical.save(ORIGIN, session(expires=time.time() + 3600))
        state, legacy, _, _ = self.bind_state_two(canonical)
        binding = (state / 'auth-binding.json').read_bytes()
        legacy.delete(ORIGIN)
        canonical.save(ORIGIN, session(expires=1))
        refreshed = {**session('fresh', 'rotated', expires=0), 'expires_in': 3600}
        with patch('quorum_api.get_json', side_effect=AssertionError('reverified')), \
                patch('quorum_api.request_json', return_value=refreshed):
            _, bound = self.resolve('state-two', None)
            self.assertEqual(bound.principal, 'user-123')
            self.assertEqual(token_provider(config, bound)(), 'fresh')
        self.assertEqual((state / 'auth-binding.json').read_bytes(), binding)

    def test_same_principal_refresh_and_concurrent_resolution_keep_the_binding(self):
        config, canonical = self.resolve()
        canonical.save(ORIGIN, session(expires=1))
        state, _, before = self.legacy_state(expires=time.time() + 3600)
        # Configured already: first-run config.json creation is outside this binding race.
        fd = os.open(state / 'config.json', os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, 'w') as output: json.dump({'origin': ORIGIN, 'publishable_key': CONFIG.publishable_key}, output)
        user = session()['user']
        get_json, _ = auth_server({'fresh': user, 'legacy-access': user})
        requests = []
        def refresh(url, headers, body):
            requests.append(json.loads(body))
            time.sleep(.1)
            return {**session('fresh', 'rotated', expires=0), 'expires_in': 3600}
        with patch('quorum_api.get_json', side_effect=get_json), patch('quorum_api.request_json', side_effect=refresh), \
                concurrent.futures.ThreadPoolExecutor(3) as pool:
            roots = list(pool.map(lambda _: self.resolve('state-two', None)[1].backend.root, range(3)))
        self.assertEqual(roots, [canonical.backend.root] * 3)
        self.assertEqual(requests, [{'refresh_token': 'refresh'}])
        self.assertEqual(list(state.glob('.auth-binding-*')), [])
        binding = (state / 'auth-binding.json').read_bytes()
        canonical.save(ORIGIN, session('later', 'later-refresh', expires=time.time() + 3600))
        with patch('quorum_api.get_json', side_effect=AssertionError('reverified')):
            _, bound = self.resolve('state-two', None)
            self.assertEqual(token_provider(config, bound)(), 'later')
        self.assertEqual((state / 'auth-binding.json').read_bytes(), binding)
        self.assertUnchanged(before)

    def test_binding_without_a_pinned_principal_is_reverified_not_trusted(self):
        _, canonical = self.resolve()
        canonical.save(ORIGIN, session(expires=time.time() + 3600))
        state, _, _, _ = self.bind_state_two(canonical)
        path = state / 'auth-binding.json'
        record = json.loads(path.read_text())
        del record['auth_user_id']
        path.write_text(json.dumps(record))
        get_json, calls = auth_server({'access': session()['user'], 'legacy-access': session()['user']})
        with patch('quorum_api.get_json', side_effect=get_json):
            self.resolve('state-two', None)
        self.assertEqual(len(calls), 2)
        self.assertEqual(json.loads(path.read_text())['auth_user_id'], 'user-123')
        if os.name != 'nt': self.assertEqual(path.stat().st_mode & 0o777, 0o600)
        # A claim that disagrees can only send it back to the server, never route it.
        record['auth_user_id'] = 'user-999'
        path.write_text(json.dumps(record))
        get_json, _ = auth_server({'access': session()['user']})
        from quorum_profiles import AuthProfileConflict
        with patch('quorum_api.get_json', side_effect=get_json):
            with self.assertRaises(AuthProfileConflict) as error:
                self.resolve('state-two', None)
        self.assertEqual(error.exception.diagnostic()['reason'], 'legacy_login_unverified')
        self.assertEqual(json.loads(path.read_text())['auth_user_id'], 'user-999')

    def test_interrupted_binding_write_leaves_no_partial_file_and_recovers(self):
        _, canonical = self.resolve()
        canonical.save(ORIGIN, session(expires=time.time() + 3600))
        state, _, before = self.legacy_state(expires=time.time() + 3600)
        user = session()['user']
        get_json, _ = auth_server({'access': user, 'legacy-access': user})
        fsync = os.fsync
        def failing_fsync(fd):
            # Fail only the binding write, after its bytes are in the file.
            if any(p.name.startswith('.auth-binding-') for p in state.iterdir()): raise OSError('disk full')
            return fsync(fd)
        with patch('quorum_api.get_json', side_effect=get_json), patch('os.fsync', side_effect=failing_fsync):
            with self.assertRaisesRegex(OSError, 'disk full'):
                self.resolve('state-two', None)
        self.assertFalse((state / 'auth-binding.json').exists())
        self.assertEqual(list(state.glob('.auth-binding-*')), [])
        # A crash after publishing but before temporary cleanup leaves a second link.
        unlink = os.unlink
        def killed_unlink(path, *args, **kwargs):
            if (state / 'auth-binding.json').exists(): raise OSError('killed')
            return unlink(path, *args, **kwargs)
        with patch('quorum_api.get_json', side_effect=get_json), patch('os.unlink', side_effect=killed_unlink):
            with self.assertRaisesRegex(OSError, 'killed'):
                self.resolve('state-two', None)
        self.assertEqual((state / 'auth-binding.json').stat().st_nlink, 2)
        with patch('quorum_api.get_json', side_effect=AssertionError('reverified')):
            _, bound = self.resolve('state-two', None)
        self.assertEqual(bound.backend.root, canonical.backend.root)
        self.assertEqual((state / 'auth-binding.json').stat().st_nlink, 1)
        self.assertEqual(list(state.glob('.auth-binding-*')), [])
        self.assertUnchanged(before)

    def test_binding_publication_never_overwrites_an_existing_binding(self):
        from quorum_profiles import publish_binding
        state = self.root / 'state-x'
        state.mkdir(mode=0o700)
        path = state / 'auth-binding.json'
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, 'w') as output: json.dump({'existing': True}, output)
        publish_binding(path, {'new': True})
        self.assertEqual(json.loads(path.read_text()), {'existing': True})
        # Even a race that slips past the re-read cannot replace it.
        with patch('quorum_profiles.read_private_json', return_value=None):
            publish_binding(path, {'new': True})
        self.assertEqual(json.loads(path.read_text()), {'existing': True})
        self.assertEqual(path.stat().st_nlink, 1)
        self.assertEqual(list(state.glob('.auth-binding-*')), [])

    def test_concurrent_first_runs_of_one_state_publish_one_complete_config(self):
        state = self.root / 'state-new'
        other = ServiceConfig('https://two.example', 'sb_publishable_other')
        fsync = os.fsync
        def slow_fsync(fd):
            time.sleep(.05)  # Widen the window between creating and completing config.json.
            return fsync(fd)
        with patch('os.fsync', side_effect=slow_fsync), concurrent.futures.ThreadPoolExecutor(4) as pool:
            results = list(pool.map(lambda _: self.resolve('state-new')[1].backend.root, range(4)))
        self.assertEqual(len(set(results)), 1)
        self.assertEqual(json.loads((state / 'config.json').read_text()),
                         {'origin': ORIGIN, 'publishable_key': CONFIG.publishable_key})
        self.assertEqual((state / 'config.json').stat().st_nlink, 1)
        if os.name != 'nt': self.assertEqual((state / 'config.json').stat().st_mode & 0o777, 0o600)
        self.assertEqual(list(state.glob('.config-*')), [])
        # Racing first runs for different services: one wins, the other never replaces it.
        def attempt(config):
            try:
                return self.resolve('state-race', config)[0]
            except ValueError as error:
                return str(error)
        with patch('os.fsync', side_effect=slow_fsync), concurrent.futures.ThreadPoolExecutor(2) as pool:
            outcomes = list(pool.map(attempt, (CONFIG, other)))
        winner = next(outcome for outcome in outcomes if isinstance(outcome, ServiceConfig))
        self.assertIn('service_config_conflict', outcomes)
        self.assertEqual(json.loads((self.root / 'state-race/config.json').read_text())['origin'], winner.origin)

    def test_interrupted_config_write_leaves_no_partial_config_and_recovers(self):
        state = self.root / 'state-new'
        fsync = os.fsync
        def failing_fsync(fd):
            if state.exists() and any(p.name.startswith('.config-') for p in state.iterdir()):
                raise OSError('disk full')
            return fsync(fd)
        with patch('os.fsync', side_effect=failing_fsync):
            with self.assertRaisesRegex(OSError, 'disk full'):
                self.resolve('state-new')
        self.assertFalse((state / 'config.json').exists())
        self.assertEqual(list(state.glob('.config-*')), [])
        # A crash after publishing but before temporary cleanup leaves a second link.
        unlink = os.unlink
        def killed_unlink(path, *args, **kwargs):
            if (state / 'config.json').exists(): raise OSError('killed')
            return unlink(path, *args, **kwargs)
        with patch('os.unlink', side_effect=killed_unlink):
            with self.assertRaisesRegex(OSError, 'killed'):
                self.resolve('state-new')
        self.assertEqual((state / 'config.json').stat().st_nlink, 2)
        config, _ = self.resolve('state-new', None)
        self.assertEqual(config, CONFIG)
        self.assertEqual((state / 'config.json').stat().st_nlink, 1)
        self.assertEqual(list(state.glob('.config-*')), [])

    def test_legacy_origin_spelling_retains_the_original_hashed_credential_key(self):
        (self.root / 'state-one').mkdir(mode=0o700)
        legacy = CredentialStore(root=self.root / 'state-one/credentials')
        legacy.save(ORIGIN + '/', session())
        config, shared = self.resolve(config=ServiceConfig(ORIGIN + '/', CONFIG.publishable_key))
        self.assertEqual(config.origin, ORIGIN + '/')
        self.assertEqual(shared.backend.root, legacy.backend.root)
        self.assertEqual(token_provider(config, shared, clock=lambda: 100)(), 'access')
        with self.assertRaisesRegex(ValueError, 'service_config_conflict'):
            self.resolve('state-two', CONFIG)

    def test_default_https_port_alias_cannot_create_another_profile_or_implicit_login(self):
        from quorum_profiles import ensure_login
        config, first = self.resolve()
        first.save(config.origin, session(expires=time.time() + 3600))
        browser_logins = []
        def login(config, store):
            browser_logins.append(config.origin)
            store.save(config.origin, session(expires=time.time() + 3600))
        aliases = ('https://one.example:443', 'https://ONE.EXAMPLE:443/',
                   'HTTPS://ONE.EXAMPLE/', 'https://one.example/')
        with patch('quorum_auth.login_pkce', side_effect=login):
            for index, origin in enumerate(aliases):
                state = 'alias-' + str(index)
                with self.subTest(origin=origin):
                    with self.assertRaisesRegex(ValueError, 'service_config_conflict'):
                        alias_config, alias_store = self.resolve(state, ServiceConfig(origin, CONFIG.publishable_key))
                        ensure_login(alias_config, alias_store)
                    self.assertFalse((self.root / state / 'config.json').exists())
        self.assertEqual(browser_logins, [])
        self.assertEqual(list(first.profiles.read()), [ORIGIN])
        self.assertEqual([path for path in (self.root / 'machine').iterdir() if path.is_dir()],
                         [first.backend.root])

    def test_default_port_comparison_preserves_legacy_origin_and_nondefault_port_isolation(self):
        legacy_origin = 'HTTPS://ONE.EXAMPLE:443/'
        (self.root / 'state-one').mkdir(mode=0o700)
        legacy = CredentialStore(root=self.root / 'state-one/credentials')
        legacy.save(legacy_origin, session())
        config, shared = self.resolve(config=ServiceConfig(legacy_origin, CONFIG.publishable_key))
        self.assertEqual(config.origin, legacy_origin)
        self.assertEqual(shared.backend.root, legacy.backend.root)
        self.assertEqual(shared.load(legacy_origin)['refresh_token'], 'refresh')
        with self.assertRaisesRegex(ValueError, 'service_config_conflict'):
            self.resolve('state-two', CONFIG)
        other_origin = 'https://one.example:8443'
        _, independent = self.resolve('state-three', ServiceConfig(other_origin, CONFIG.publishable_key))
        self.assertNotEqual(independent.backend.root, legacy.backend.root)
        self.assertIsNone(independent.load(other_origin))

    def test_separate_origins_and_os_roots_are_isolated_and_uuid_never_guesses(self):
        _, first = self.resolve()
        first.save(ORIGIN, session())
        other = ServiceConfig('https://two.example', 'sb_publishable_two')
        _, second = self.resolve('state-two', other)
        self.assertIsNone(second.load(other.origin))
        with self.assertRaisesRegex(ValueError, 'service_profile_ambiguous'):
            self.resolve('state-three', None)
        with patch.dict(os.environ, QUORUM_PROFILE_ROOT=str(self.root / 'other-user')):
            with self.assertRaisesRegex(QuorumError, 'service_not_configured'):
                self.resolve('state-four', None)

    def test_service_key_and_authoritative_state_conflicts_stop(self):
        self.resolve()
        for state in ('state-one', 'state-two'):
            with self.subTest(state=state), self.assertRaisesRegex(ValueError, 'service_config_conflict'):
                self.resolve(state, ServiceConfig(ORIGIN, 'sb_publishable_other'))
        with self.assertRaisesRegex(ValueError, 'service_config_conflict'):
            self.resolve('state-one', ServiceConfig('https://other.example', 'sb_publishable_other'))

    def test_deleted_legacy_path_is_blocker_and_is_not_recreated(self):
        (self.root / 'state-one').mkdir(mode=0o700)
        legacy = CredentialStore(root=self.root / 'state-one/credentials')
        legacy.save(ORIGIN, session())
        self.resolve()
        legacy.delete(ORIGIN)
        legacy.backend.root.rmdir()
        with self.assertRaisesRegex(ValueError, 'machine_profile_unavailable'):
            self.resolve('state-two', None)
        self.assertFalse(legacy.backend.root.exists())

    def test_shared_identity_change_fails_closed_even_if_old_listener_writes_it(self):
        _, shared = self.resolve()
        shared.save(ORIGIN, session())
        with self.assertRaisesRegex(ValueError, 'auth_profile_conflict'):
            shared.save(ORIGIN, session(identity='456'))
        self.assertEqual(shared.load(ORIGIN)['access_token'], 'access')
        shared.backend.write(ORIGIN, json.dumps(session(identity='456')).encode())
        with self.assertRaisesRegex(ValueError, 'auth_profile_conflict'):
            shared.load(ORIGIN)

    def test_concurrent_refresh_reloads_after_lock_and_rotates_only_once(self):
        config, store = self.resolve()
        store.save(ORIGIN, session(expires=1))
        requests = []
        def refresh(url, headers, body):
            requests.append(json.loads(body))
            time.sleep(.1)
            return {**session('fresh', 'rotated'), 'expires_in': 3600}
        _, other = self.resolve('state-two')
        with patch('quorum_api.request_json', side_effect=refresh), concurrent.futures.ThreadPoolExecutor(2) as pool:
            tokens = list(pool.map(lambda s: token_provider(config, s, clock=lambda: 100)(), (store, other)))
        self.assertEqual(tokens, ['fresh', 'fresh'])
        self.assertEqual(requests, [{'refresh_token': 'refresh'}])
        self.assertEqual(store.load(ORIGIN)['refresh_token'], 'rotated')

    def test_simultaneous_first_login_opens_one_browser_flow(self):
        config, first = self.resolve()
        _, second = self.resolve('state-two')
        from quorum_profiles import ensure_login
        logins = []
        def login(config, store):
            logins.append('browser')
            time.sleep(.1)
            store.save(config.origin, session(expires=time.time() + 3600))
        with patch('quorum_auth.login_pkce', side_effect=login), concurrent.futures.ThreadPoolExecutor(2) as pool:
            list(pool.map(lambda s: ensure_login(config, s), (first, second)))
        self.assertEqual(logins, ['browser'])

    def test_simultaneous_initial_helpers_can_create_one_machine_registry(self):
        # Force the first-run mkdir race while retaining real filesystem effects.
        root = self.root / 'machine'
        barrier = threading.Barrier(2)
        mkdir = Path.mkdir
        def concurrent_mkdir(path, *args, **kwargs):
            if path == root and not kwargs.get('exist_ok'): barrier.wait(timeout=5)
            return mkdir(path, *args, **kwargs)
        with patch.object(Path, 'mkdir', concurrent_mkdir), concurrent.futures.ThreadPoolExecutor(2) as pool:
            results = list(pool.map(lambda name: self.resolve(name), ('state-one', 'state-two')))
        self.assertEqual(results[0][1].backend.root, results[1][1].backend.root)

    def test_revocation_and_missing_established_auth_do_not_open_browser(self):
        config, store = self.resolve()
        store.save(ORIGIN, session(expires=1))
        from quorum_profiles import ensure_login
        with patch('quorum_auth.login_pkce', side_effect=AssertionError('silent re-login')), \
                patch('quorum_api.request_json', side_effect=QuorumError('auth_required')):
            with self.assertRaisesRegex(QuorumError, 'auth_required'):
                ensure_login(config, store)
            store.delete(ORIGIN)
            with self.assertRaisesRegex(QuorumError, 'auth_required'):
                ensure_login(config, store)

    def test_malformed_or_non_github_identity_is_a_safe_blocker(self):
        _, store = self.resolve()
        for user in (None, {}, {'identities': None}, {'identities': ['private-data']},
                     {'identities': [{'provider': 'github', 'identity_data': None}]},
                     {'identities': [{'provider': 'google', 'id': '123'}]}):
            with self.subTest(user=user), self.assertRaisesRegex(ValueError, 'github_identity_required'):
                store.save(ORIGIN, {**session(), 'user': user})
        self.assertIsNone(store.load(ORIGIN))

    def test_private_profile_and_ancestor_link_guards(self):
        # A permissive registry or indirect path must never be silently trusted.
        self.resolve()
        registry = next((self.root / 'machine').glob('*.json'))
        if os.name != 'nt':
            registry.chmod(0o644)
            with self.assertRaises(ValueError): self.resolve('state-two', None)
            registry.chmod(0o600)
        link = self.root / 'alias'
        link.symlink_to(self.root / 'machine', target_is_directory=True)
        with patch.dict(os.environ, QUORUM_PROFILE_ROOT=str(link / 'nested')):
            with self.assertRaises(ValueError): self.resolve('state-three')
        self.assertFalse((self.root / 'machine/nested').exists())

    def test_registry_cannot_share_its_lock_with_an_adopted_credential_directory(self):
        (self.root / 'state-one').mkdir(mode=0o700)
        legacy_root = self.root / 'state-one/credentials'
        CredentialStore(root=legacy_root).save(ORIGIN, session())
        with patch.dict(os.environ, QUORUM_PROFILE_ROOT=str(legacy_root)):
            with self.assertRaisesRegex(ValueError, 'unsafe_profile_directory'):
                self.resolve()

    def test_configured_machine_accepts_uuid_only_from_new_state_without_login(self):
        import quorum
        import quorum_live
        _, store = self.resolve()
        store.save(ORIGIN, session(expires=time.time() + 3600))
        calls = []
        def request(url, headers, body):
            calls.append((url, headers, json.loads(body)))
            return {'quorum_id': 'q', 'membership_id': 'm'}
        # Keep real CLI/selection/auth/API serialization; replace only outbound I/O.
        original = quorum_live.api_for
        def api_for(ledger):
            api, config, store = original(ledger)
            api.request = request
            return api, config, store
        with patch('quorum_live.api_for', side_effect=api_for), \
                patch('quorum_auth.login_pkce', side_effect=AssertionError('duplicate browser')), \
                patch('quorum_session.client_preflight', return_value='codex'), \
                contextlib.redirect_stdout(io.StringIO()) as output:
            self.assertEqual(quorum.main(['--state', str(self.root / 'new-chat'), 'accept-id',
                                         '11111111-1111-4111-8111-111111111111']), 0, output.getvalue())
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0][0], ORIGIN + '/rest/v1/rpc/invite_accept_id')
        self.assertEqual(calls[0][1]['Authorization'], 'Bearer access')
        self.assertEqual(calls[0][2]['p_invite_id'], '11111111-1111-4111-8111-111111111111')
        self.assertTrue(json.loads(output.getvalue())['data']['membership_only'])


if __name__ == '__main__': unittest.main()
