"""Invitation-file creation never returns its token to an agent tool output."""
import json
from pathlib import Path
import sys
import tempfile
import unittest
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'skills/quorum/scripts'))


class InvitationTests(unittest.TestCase):
    def test_owner_creates_reusable_private_file_without_stdout_token(self):
        from quorum_invitations import create_invitation
        from quorum_api import ServiceConfig
        from quorum_state import LocalLedger
        class Api:
            created = False
            def call(self, op, payload, request_id=None):
                if op == 'quorum_lookup': return {'quorum_id': 'q'}
                if op == 'quorum_check_owner': return True
                if op == 'invite_create':
                    if self.created: raise AssertionError('private saved invitation should be reused')
                    self.created = True
                    return {'invite_id': 'i', 'token': 'a' * 64, 'expires_at': '2026-10-22T12:00:00Z'}
                raise AssertionError(op)
        with tempfile.TemporaryDirectory() as root:
            ledger, api = LocalLedger(Path(root)), Api()
            config = ServiceConfig('https://example.supabase.co', 'public')
            result = create_invitation(api, config, ledger, 'team', '123', 'request-1')
            self.assertNotIn('a' * 64, json.dumps(result))
            private = json.loads(Path(result['invitation_file']).read_text())
            self.assertEqual(private['token'], 'a' * 64)
            self.assertEqual(private['origin'], 'https://example.supabase.co')
            self.assertEqual(private['target_github_id'], '123')
            self.assertEqual(create_invitation(api, config, ledger, 'team', '123', 'request-1'), result)

    def test_members_cannot_write_invitation_file(self):
        from quorum_invitations import create_invitation
        from quorum_api import ServiceConfig
        from quorum_state import LocalLedger
        class Api:
            def call(self, op, payload, request_id=None):
                if op == 'quorum_lookup': return {'quorum_id': 'q'}
                raise ValueError('owner_required')
        with tempfile.TemporaryDirectory() as root:
            ledger = LocalLedger(Path(root))
            with self.assertRaisesRegex(ValueError, 'owner_required'):
                create_invitation(Api(), ServiceConfig('https://example.com', 'pub'), ledger, 'team', '123', 'r')
            self.assertFalse((Path(root) / 'invitations').exists())

    def test_github_target_lookup_is_fixed_host_and_numeric(self):
        from quorum_invitations import github_id
        def get(url):
            self.assertEqual(url, 'https://api.github.com/users/jekmen')
            return {'login': 'jekmen', 'id': 17994057}
        self.assertEqual(github_id('jekmen', get=get), '17994057')
        for username in ('https://evil.invalid/u', '../user', 'bad user'):
            with self.assertRaises(ValueError): github_id(username, get=get)
        with self.assertRaises(ValueError): github_id('jekmen', get=lambda _: {'login': 'other', 'id': 123})
