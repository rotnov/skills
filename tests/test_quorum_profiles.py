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


def session(access='access', refresh='refresh', expires=10000, identity='123'):
    return {'access_token': access, 'refresh_token': refresh, 'expires_at': expires,
            'user': {'identities': [{'provider': 'github', 'id': identity,
                                    'identity_data': {'provider_id': identity}}]}}


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
                contextlib.redirect_stdout(io.StringIO()) as output:
            self.assertEqual(quorum.main(['--state', str(self.root / 'new-chat'), 'accept-id',
                                         '11111111-1111-4111-8111-111111111111']), 0, output.getvalue())
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0][0], ORIGIN + '/rest/v1/rpc/invite_accept_id')
        self.assertEqual(calls[0][1]['Authorization'], 'Bearer access')
        self.assertEqual(calls[0][2]['p_invite_id'], '11111111-1111-4111-8111-111111111111')
        self.assertTrue(json.loads(output.getvalue())['data']['membership_only'])


if __name__ == '__main__': unittest.main()
