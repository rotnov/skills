import importlib
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'skills/quorum/scripts'))


class AuthTests(unittest.TestCase):
    def setUp(self):
        self.module = importlib.import_module('quorum_auth')

    def test_pkce_unique_verifier_and_state(self):
        one, two = self.module.PkceFlow(), self.module.PkceFlow()
        self.assertNotEqual(one.verifier, two.verifier)
        self.assertNotEqual(one.state, two.state)
        self.assertNotIn('=', one.challenge)
        self.assertIn('code_challenge=', one.authorization_url('https://test.supabase.co'))

    def test_pkce_rejects_bad_state_and_callback_path(self):
        flow = self.module.PkceFlow()
        for path in ('/callback?code=x&quorum_state=wrong',
                     '/other?code=x&quorum_state=' + flow.state,
                     '/callback?code=x&code=y&quorum_state=' + flow.state):
            with self.subTest(path=path), self.assertRaises(ValueError):
                flow.callback_code(path)
        self.assertEqual(flow.callback_code('/callback?code=good&quorum_state=' + flow.state), 'good')

    def test_store_failure_does_not_succeed_or_leak_secrets(self):
        class Broken:
            def write(self, *_):
                raise RuntimeError('private-token must not escape')
        store = self.module.CredentialStore(Broken())
        with self.assertRaisesRegex(ValueError, 'credential_store_unavailable'):
            store.save('profile', {'access_token': 'secret', 'refresh_token': 'secret2'})

    def test_tokens_not_in_login_summary(self):
        class Memory:
            def write(self, name, data): self.data = data
            def read(self, name): return self.data
            def delete(self, name): self.data = None
        backend = Memory()
        store = self.module.CredentialStore(backend)
        store.save('x', {'access_token': 'secret', 'refresh_token': 'refresh', 'expires_at': 500})
        self.assertEqual(store.load('x')['access_token'], 'secret')
        store.delete('x')
        self.assertIsNone(store.load('x'))

    def test_file_store_survives_restart_and_keeps_profiles_separate(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / 'credentials'
            store = self.module.CredentialStore(root=root)
            session = {'access_token': 'access-one', 'refresh_token': 'refresh-one', 'expires_at': 500}
            store.save('https://one.example', session)
            store.save('../../two', {**session, 'access_token': 'access-two'})
            restarted = self.module.CredentialStore(root=root)
            self.assertEqual(restarted.load('https://one.example'), session)
            self.assertEqual(restarted.load('../../two')['access_token'], 'access-two')
            self.assertEqual(len(list(root.iterdir())), 2)
            self.assertIsNone(restarted.load('missing'))
            restarted.delete('https://one.example')
            restarted.delete('https://one.example')
            self.assertIsNone(store.load('https://one.example'))
            self.assertIsNotNone(store.load('../../two'))

    def test_file_store_permissions_and_explicit_plaintext_pilot_format(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / 'credentials'
            store = self.module.CredentialStore(root=root)
            store.save('profile', {'access_token': 'access', 'refresh_token': 'refresh', 'expires_at': 500})
            files = list(root.iterdir())
            self.assertEqual(len(files), 1)
            self.assertEqual(root.stat().st_mode & 0o777, 0o700)
            self.assertEqual(files[0].stat().st_mode & 0o777, 0o600)
            self.assertEqual(json.loads(files[0].read_text())['refresh_token'], 'refresh')

    def test_file_store_rejects_symlink_directory_and_session(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            target = root / 'target'
            target.mkdir()
            link = root / 'linked'
            link.symlink_to(target, target_is_directory=True)
            with self.assertRaises(ValueError):
                self.module.CredentialStore(root=link)
            store = self.module.CredentialStore(root=target)
            session = {'access_token': 'access', 'refresh_token': 'refresh'}
            store.save('profile', session)
            path = next(target.iterdir())
            path.unlink()
            outside = root / 'outside'
            outside.write_text('private unrelated content')
            path.symlink_to(outside)
            for operation in (lambda: store.load('profile'), lambda: store.save('profile', session),
                              lambda: store.delete('profile')):
                with self.assertRaisesRegex(ValueError, 'credential_store_unavailable'):
                    operation()
            self.assertEqual(outside.read_text(), 'private unrelated content')

    def test_file_store_fails_closed_on_corruption_or_open_permissions(self):
        with tempfile.TemporaryDirectory() as directory:
            store = self.module.CredentialStore(root=Path(directory))
            store.save('profile', {'access_token': 'access', 'refresh_token': 'refresh'})
            path = next(Path(directory).iterdir())
            path.write_text('{broken')
            with self.assertRaisesRegex(ValueError, 'credential_store_unavailable'):
                store.load('profile')
            path.write_text('{}')
            with self.assertRaisesRegex(ValueError, 'credential_store_unavailable'):
                store.load('profile')
            path.chmod(0o644)
            with self.assertRaisesRegex(ValueError, 'credential_store_unavailable'):
                store.load('profile')

    def test_failed_atomic_replace_preserves_previous_session_and_removes_tempfile(self):
        with tempfile.TemporaryDirectory() as directory:
            store = self.module.CredentialStore(root=Path(directory))
            old = {'access_token': 'old', 'refresh_token': 'old-refresh'}
            store.save('profile', old)
            with patch('os.replace', side_effect=OSError('disk error')):
                with self.assertRaisesRegex(ValueError, 'credential_store_unavailable'):
                    store.save('profile', {'access_token': 'new', 'refresh_token': 'new-refresh'})
            self.assertEqual(store.load('profile'), old)
            self.assertEqual(len(list(Path(directory).iterdir())), 1)

    def test_refresh_persists_rotated_tokens_before_use(self):
        from quorum_api import ServiceConfig
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config = ServiceConfig('https://test.example', 'public')
            store = self.module.CredentialStore(root=root)
            store.save(config.origin, {'access_token': 'expired', 'refresh_token': 'old-refresh', 'expires_at': 1})
            fresh = {'access_token': 'fresh', 'refresh_token': 'rotated-refresh', 'expires_in': 3600}
            with patch('quorum_api.request_json', return_value=fresh) as request:
                self.assertEqual(self.module.token_provider(config, store, clock=lambda: 100)(), 'fresh')
                self.assertEqual(json.loads(request.call_args.args[2])['refresh_token'], 'old-refresh')
            persisted = self.module.CredentialStore(root=root).load(config.origin)
            self.assertEqual(persisted['refresh_token'], 'rotated-refresh')
            with patch('quorum_api.request_json', side_effect=AssertionError('unexpected refresh')):
                self.assertEqual(self.module.token_provider(config, store, clock=lambda: 200)(), 'fresh')

    def test_configured_cli_uses_selected_state_and_logout_does_not_recreate_login(self):
        import contextlib
        import io
        import quorum
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            args = ['--state', str(root)]
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(quorum.main(args + ['configure', '--origin', 'https://test.example',
                                                    '--publishable-key', 'public']), 0)
            store = self.module.CredentialStore(root=root / 'credentials')
            store.save('https://test.example', {'access_token': 'secret-access', 'refresh_token': 'secret-refresh'})
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                self.assertEqual(quorum.main(args + ['logout']), 0)
            self.assertTrue(json.loads(output.getvalue())['data']['logged_out'])
            self.assertNotIn('secret-', output.getvalue())
            self.assertIsNone(store.load('https://test.example'))


if __name__ == '__main__': unittest.main()
