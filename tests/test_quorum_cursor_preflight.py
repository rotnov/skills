"""Local Cursor refusal: no provider process, credential or network access."""
import contextlib
import io
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

from quorum_test_support import isolated_profiles

SCRIPTS = Path(__file__).resolve().parents[1] / 'skills/quorum/scripts'
sys.path.insert(0, str(SCRIPTS))
SID = '11111111-1111-4111-8111-111111111111'
CURSOR = {'CURSOR_AGENT': '1', 'CURSOR_INVOKED_AS': 'cursor-agent',
          'CURSOR_CONVERSATION_ID': 'synthetic-private-cursor-context\nnot-a-session-export'}


class CursorPreflightTests(unittest.TestCase):
    @isolated_profiles
    def test_local_cursor_refusal_preserves_invite_before_authentication(self):
        # A missing diagnostic or moving refusal after api_for would fail this
        # CLI contract; api_for is the first credential/network boundary.
        import quorum
        environment = {**CURSOR, 'QUORUM_PROFILE_ROOT': os.environ['QUORUM_PROFILE_ROOT']}
        for flags in ([], ['--anonymous']):
            with self.subTest(flags=flags), tempfile.TemporaryDirectory() as root, \
                    patch.dict(os.environ, environment, clear=True), patch('sys.platform', 'darwin'), \
                    patch('quorum_live.api_for', side_effect=AssertionError('authentication boundary reached')), \
                    contextlib.redirect_stdout(io.StringIO()) as output:
                code = quorum.main(['--state', root, 'accept-id', SID, *flags,
                                    '--origin', 'https://example.com',
                                    '--publishable-key', 'sb_publishable_synthetic'])
                result = json.loads(output.getvalue())
                self.assertEqual((code, result['ok'], result['code']), (3, False, 'unsupported_client'))
                self.assertEqual((result['data']['reason'], result['data']['detected_client']),
                                 ('cursor_inbox_unavailable', 'cursor'))
                self.assertNotIn('synthetic-private-cursor-context', output.getvalue())
                self.assertNotIn(root, output.getvalue())
                self.assertFalse((Path(root) / 'sessions').exists())

    def test_other_contexts_keep_the_generic_missing_session_diagnostic(self):
        # Broad Cursor-variable detection would misclassify these contexts.
        from quorum_session import UnsupportedClient, client_preflight
        cases = [('linux', CURSOR), ('win32', CURSOR),
                 ('linux', {**CURSOR, 'CURSOR_AGENT_SOCKET': '/synthetic/cloud.sock'}),
                 ('darwin', {**CURSOR, 'CURSOR_AGENT_SOCKET': '/synthetic/socket'}),
                 ('darwin', {**CURSOR, 'CURSOR_AGENT': '0'}),
                 ('darwin', {**CURSOR, 'CURSOR_INVOKED_AS': 'other-client'})]
        cases.extend(('darwin', {key: value for key, value in CURSOR.items() if key != missing})
                     for missing in CURSOR)
        for platform, environment in cases:
            with self.subTest(platform=platform, keys=sorted(environment)), \
                    patch.dict(os.environ, environment, clear=True), patch('sys.platform', platform):
                with self.assertRaises(UnsupportedClient) as caught:
                    client_preflight()
                self.assertEqual((caught.exception.reason, caught.exception.detected),
                                 ('no_client_session', None))

    def test_supported_session_identity_survives_inherited_cursor_markers(self):
        # Detecting Cursor before the supported identity would reject a real
        # Codex, Claude or Grok session launched from a Cursor-hosted shell.
        from quorum_session import current_engine, current_session
        for engine, key in (('codex', 'CODEX_THREAD_ID'), ('claude', 'CLAUDE_CODE_SESSION_ID'),
                            ('grok', 'GROK_SESSION_ID')):
            with self.subTest(engine=engine), \
                    patch.dict(os.environ, {**CURSOR, key: SID}, clear=True), patch('sys.platform', 'darwin'):
                self.assertEqual(current_session(), SID)
                self.assertEqual(current_engine(), engine)

    def test_competing_supported_sessions_remain_ambiguous(self):
        from quorum_session import current_session
        with patch.dict(os.environ, {**CURSOR, 'CODEX_THREAD_ID': SID, 'CLAUDE_CODE_SESSION_ID': SID}, clear=True), \
                patch('sys.platform', 'darwin'):
            with self.assertRaisesRegex(ValueError, '^ambiguous_current_session$'):
                current_session()
