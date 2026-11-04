"""Server error codes on send: only allow-listed names cross the client boundary. No live service."""
import contextlib
import io
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
from urllib.error import HTTPError
from quorum_test_support import isolated_profiles

SCRIPTS = Path(__file__).resolve().parents[1] / 'skills/quorum/scripts'
sys.path.insert(0, str(SCRIPTS))
SID = '11111111-1111-4111-8111-111111111111'
MID = '22222222-2222-4222-8222-222222222222'
SECRET = 'sb_secret_should_never_leak'
CLIENT_SESSION_VARIABLES = ('CODEX_THREAD_ID', 'GROK_SESSION_ID', 'GROK_LEADER_SOCKET',
                            'CLAUDE_CODE_SESSION_ID', 'CLAUDE_CODE_MESSAGING_SOCKET',
                            'CLAUDE_CODE_MESSAGING_TOKEN')


def http_error(status, body):
    return HTTPError('https://example.com/rest/v1/rpc/resolve_role?token=' + SECRET, status,
                     'error', {}, io.BytesIO(body))


def failing_opener(error):
    class Opener:
        def open(self, *_args, **_kwargs):
            raise error
    return lambda *_: Opener()


class TransportCodeTests(unittest.TestCase):
    def raised(self, status, body):
        import quorum_api
        with patch.object(quorum_api, 'build_opener', failing_opener(http_error(status, body))):
            with self.assertRaises(quorum_api.QuorumError) as caught:
                quorum_api.request_json('https://example.com/rest/v1/rpc/resolve_role',
                                        {'Authorization': 'Bearer ' + SECRET}, b'{}')
        error = caught.exception
        self.assertIsNone(error.__cause__)
        self.assertNotIn(SECRET, repr(error.args))
        return error

    def test_ambiguous_role_surfaces_its_allow_listed_code(self):
        body = json.dumps({'code': 'P0001', 'details': None, 'hint': None,
                           'message': 'recipient_ambiguous_or_missing'}).encode()
        error = self.raised(400, body)
        self.assertEqual(str(error), 'recipient_ambiguous_or_missing')
        self.assertFalse(error.transient)

    def test_other_bodies_stay_service_error_without_echo(self):
        for status, body in (
                (400, json.dumps({'code': 'P0001', 'message': 'leaked ' + SECRET}).encode()),
                (400, json.dumps({'message': 'invalid_priority'}).encode()),
                (400, b'not json ' + SECRET.encode()),
                (400, b'{"message":"recipient_ambiguous_or_missing","pad":"' + b'x' * 5000 + b'"}'),
                (400, json.dumps(['recipient_ambiguous_or_missing']).encode()),
                (500, json.dumps({'message': 'recipient_ambiguous_or_missing'}).encode())):
            with self.subTest(status=status, body=body[:40]):
                error = self.raised(status, body)
                self.assertEqual(str(error), 'service_error')
                self.assertEqual(error.transient, status == 500)

    def test_authentication_failures_are_unchanged(self):
        body = json.dumps({'message': 'recipient_ambiguous_or_missing'}).encode()
        for status in (401, 403):
            with self.subTest(status=status):
                self.assertEqual(str(self.raised(status, body)), 'auth_required')


class SendRoleTests(unittest.TestCase):
    def setUp(self):
        isolated = patch.dict(os.environ, {key: value for key, value in os.environ.items()
                                           if key not in CLIENT_SESSION_VARIABLES}, clear=True)
        isolated.start()
        self.addCleanup(isolated.stop)
        self.calls = []

    def api(self, failure):
        from quorum_api import QuorumError
        calls = self.calls

        class Api:
            def call(self, operation, payload, request_id=None):
                calls.append(operation)
                if operation == 'session_requirements': return {'protocol_version': 2, 'skill_version': '0.3.0'}
                if operation == 'resolve_role': raise QuorumError(failure)
                return {'message_id': MID}
        return Api()

    def send(self, failure):
        import quorum
        from quorum_api import ServiceConfig
        from quorum_state import LocalLedger
        with tempfile.TemporaryDirectory() as root:
            session = LocalLedger(Path(root) / 'sessions' / SID)
            session.setting('session-agent', {'engine': 'codex', 'session_ref': SID, 'agent_id': 'a',
                                              'quorum_id': 'q', 'project_ref': 'owner/repo', 'quorum': 'team'})
            session.db.close()
            config = ServiceConfig('https://example.com', 'sb_publishable_synthetic')
            with patch('quorum_live.api_for', return_value=(self.api(failure), config, None)), \
                    patch.dict(os.environ, {'CODEX_THREAD_ID': SID}), \
                    patch('sys.stdin', io.TextIOWrapper(io.BytesIO(b'hello'))), \
                    contextlib.redirect_stdout(io.StringIO()) as output:
                code = quorum.main(['--state', root, 'session', 'send', '--to-role', 'cloderust',
                                    '--request-id', 'r1', '--text-stdin'])
        return code, output.getvalue()

    @isolated_profiles
    def test_ambiguous_role_names_the_error_and_the_next_step(self):
        code, text = self.send('recipient_ambiguous_or_missing')
        result = json.loads(text)
        self.assertEqual(code, 3)
        self.assertEqual((result['ok'], result['code']), (False, 'recipient_ambiguous_or_missing'))
        self.assertEqual(result['data']['role'], 'cloderust')
        for hint in ('session peers', 'receipt_timeout', 'leave --request-id', 'same request ID'):
            self.assertIn(hint, result['data']['next_step'])
        self.assertEqual(set(result['data']), {'role', 'next_step'})
        self.assertIn('resolve_role', self.calls)
        self.assertNotIn('message_send', self.calls)
        for leaked in (SECRET, 'https://', 'Bearer', 'sb_publishable'):
            self.assertNotIn(leaked, text)

    @isolated_profiles
    def test_an_unlisted_service_failure_stays_service_error(self):
        code, text = self.send('service_error')
        self.assertEqual(code, 5)
        self.assertEqual(json.loads(text), {'ok': False, 'code': 'service_error', 'data': {}})
        self.assertIn('resolve_role', self.calls)
        self.assertNotIn('message_send', self.calls)


if __name__ == '__main__':
    unittest.main()
