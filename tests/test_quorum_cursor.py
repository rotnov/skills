"""Cursor Cloud identity and follow-up tests. They never start an agent or call api.cursor.com."""
import contextlib
import http.client
import io
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import threading
import unittest
import urllib.error
from unittest.mock import patch

SCRIPTS = Path(__file__).resolve().parents[1] / 'skills/quorum/scripts'
sys.path.insert(0, str(SCRIPTS))
BC = 'bc-11111111-1111-4111-8111-111111111111'
OTHER = 'bc-aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa'
MID = '22222222-2222-4222-8222-222222222222'
KEY = 'cursor-test-key-value'


class Response:
    def __init__(self, status, body=b'{}'):
        self.status, self.body = status, body
    def read(self, n): return self.body[:n]
    def __enter__(self): return self
    def __exit__(self, *_): return False


class Metadata:
    def __init__(self, directory, body):
        self.path = Path(directory) / 'api.sock'
        self.socket = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.socket.bind(str(self.path))
        os.chmod(self.path, 0o666)
        self.socket.listen(4)
        self.stop = threading.Event()
        payload = body.encode()
        response = (b'HTTP/1.1 200 OK\r\ncontent-length: ' + str(len(payload)).encode()
                    + b'\r\nconnection: close\r\n\r\n' + payload)
        def serve():
            while not self.stop.is_set():
                self.socket.settimeout(0.2)
                try: connection, _ = self.socket.accept()
                except socket.timeout: continue
                except OSError:
                    return
                try:
                    connection.recv(1024)
                    connection.sendall(response)
                finally:
                    connection.close()
        self.thread = threading.Thread(target=serve, daemon=True)
        self.thread.start()

    def close(self):
        self.stop.set()
        self.socket.close()


def cursor_env(directory, body=BC, key=KEY):
    meta = Metadata(directory, body)
    env = {'CURSOR_AGENT': '1', 'CURSOR_CONVERSATION_ID': BC,
           'CURSOR_AGENT_SOCKET': str(meta.path), 'CURSOR_API_KEY': key}
    return meta, env


class CursorTests(unittest.TestCase):
    def test_matching_metadata_and_key_identify_this_agent(self):
        from quorum_cursor import binding
        from quorum_session import UnsupportedClient, client_preflight, current_session
        with tempfile.TemporaryDirectory() as root:
            meta, env = cursor_env(root)
            try:
                with patch.dict(os.environ, env, clear=True):
                    self.assertEqual(current_session(), BC)
                    self.assertEqual(binding(BC), {'session_ref': BC})
                    self.assertEqual(client_preflight(), 'cursor')
                    with self.assertRaises(UnsupportedClient) as caught:
                        os.environ.pop('CURSOR_API_KEY')
                        client_preflight()
                    data = caught.exception.diagnostic()
                    self.assertEqual((data['reason'], data['detected_client']),
                                     ('cursor_ingress_unavailable', 'cursor'))
                    self.assertNotIn(KEY, json.dumps(data))
                    self.assertNotIn(BC, json.dumps(data))
            finally:
                meta.close()

    def test_mismatched_or_exposed_identity_is_refused_without_the_key_or_id(self):
        from quorum_cursor import binding
        from quorum_session import UnsupportedClient, client_preflight
        with tempfile.TemporaryDirectory() as root:
            meta, env = cursor_env(root, body=OTHER)
            try:
                with patch.dict(os.environ, env, clear=True):
                    with self.assertRaisesRegex(ValueError, '^cursor_identity_unavailable$'):
                        binding(BC)
                    with self.assertRaises(UnsupportedClient) as caught:
                        client_preflight()
                    self.assertEqual(caught.exception.diagnostic()['reason'], 'cursor_identity_unavailable')
                    self.assertNotIn(KEY, json.dumps(caught.exception.diagnostic()))
                    self.assertNotIn(OTHER, json.dumps(caught.exception.diagnostic()))
                exposed_dir = Path(root) / 'open'
                exposed_dir.mkdir()
                os.chmod(exposed_dir, 0o777)
                meta_open, exposed = cursor_env(exposed_dir)
                try:
                    with patch.dict(os.environ, exposed, clear=True):
                        with self.assertRaisesRegex(ValueError, '^cursor_identity_unavailable$'):
                            binding(BC)
                finally:
                    meta_open.close()
            finally:
                meta.close()

    def test_unset_socket_uses_cursors_documented_default_with_the_same_checks(self):
        from quorum_cursor import binding
        with tempfile.TemporaryDirectory() as root:
            meta, env = cursor_env(root)
            try:
                for socket_value in (None, ''):
                    vm = {key: value for key, value in env.items() if key != 'CURSOR_AGENT_SOCKET'}
                    if socket_value is not None: vm['CURSOR_AGENT_SOCKET'] = socket_value
                    with patch.dict(os.environ, vm, clear=True):
                        with patch('quorum_cursor.DEFAULT_SOCKET', str(meta.path)):
                            self.assertEqual(binding(BC), {'session_ref': BC})
                        with patch('quorum_cursor.DEFAULT_SOCKET', str(Path(root) / 'missing.sock')):
                            with self.assertRaisesRegex(ValueError, '^cursor_identity_unavailable$'):
                                binding(BC)
            finally:
                meta.close()

    def test_another_client_export_is_ambiguous(self):
        from quorum_session import current_session
        with tempfile.TemporaryDirectory() as root:
            meta, env = cursor_env(root)
            try:
                env['CODEX_THREAD_ID'] = MID
                with patch.dict(os.environ, env, clear=True):
                    with self.assertRaisesRegex(ValueError, '^ambiguous_current_session$'):
                        current_session()
            finally:
                meta.close()

    def test_followup_targets_only_this_agent_and_defers_when_busy(self):
        from quorum_cursor import DeliveryDeferred, post_followup
        seen = {}
        def urlopen(request, timeout):
            seen['url'] = request.full_url
            seen['method'] = request.method
            seen['auth'] = request.get_header('Authorization')
            seen['body'] = json.loads(request.data.decode())
            seen['timeout'] = timeout
            return Response(202, b'{"run":{"id":"run-1"}}')
        self.assertEqual(post_followup(BC, 'hello from quorum', KEY, urlopen=urlopen), 'submitted')
        self.assertEqual(seen['url'], 'https://api.cursor.com/v1/agents/' + BC + '/runs')
        self.assertEqual(seen['method'], 'POST')
        self.assertEqual(seen['auth'], 'Bearer ' + KEY)
        self.assertEqual(seen['body'], {'prompt': {'text': 'hello from quorum'}})
        self.assertNotIn('mcpServers', seen['body'])
        def busy(request, timeout):
            raise urllib.error.HTTPError(request.full_url, 409, 'busy', hdrs=None, fp=None)
        with self.assertRaises(DeliveryDeferred):
            post_followup(BC, 'later', KEY, urlopen=busy)
        with self.assertRaisesRegex(ValueError, '^session_delivery_unknown$'):
            post_followup(BC, 'hello', KEY, urlopen=lambda request, timeout: None)

    def test_submit_retries_a_busy_agent_without_freezing_the_ledger(self):
        from quorum_cursor import DeliveryDeferred, submit
        from quorum_session import Relay
        from quorum_state import LocalLedger
        calls = []
        def fake_run(command, **kwargs):
            calls.append(kwargs['input'])
            code = 2 if len(calls) == 1 else 0
            return subprocess.CompletedProcess(command, code, '', '')
        record = {'session_ref': BC}
        with patch('quorum_cursor.subprocess.run', fake_run):
            with self.assertRaises(DeliveryDeferred):
                submit(record, MID, 'first')
            submit(record, MID, 'second')
        self.assertNotIn(KEY, calls[0])
        message = {'id': MID, 'recipient_id': 'agent', 'sender_role': 'peer', 'body': 'ping'}
        class Api:
            def call(self, operation, payload, request_id=None):
                if operation == 'session_requirements':
                    return {'protocol_version': 2, 'skill_version': '0.3.0'}
                if operation == 'session_inbox':
                    return [message]
                raise AssertionError(operation)
        with tempfile.TemporaryDirectory() as root:
            ledger = LocalLedger(Path(root))
            try:
                outcomes = []
                def queue(item):
                    if not outcomes:
                        raise DeliveryDeferred()
                    return 'submitted'
                relay = Relay(Api(), ledger, {'agent_id': 'agent', 'session_ref': BC}, queue)
                outcomes.append(relay.run_once())
                outcomes.append(relay.run_once())
                self.assertEqual(outcomes, ['idle', 'idle'])
                self.assertEqual(ledger.setting('delivery:' + MID)['state'], 'submitted')
            finally:
                ledger.db.close()

    def test_rate_limit_defers_for_retry_after_instead_of_freezing_the_ledger(self):
        from quorum_cursor import DeliveryDeferred, post_followup, submit
        from quorum_session import Relay
        from quorum_state import LocalLedger
        def limited(retry_after):
            def urlopen(request, timeout):
                headers = http.client.HTTPMessage()
                if retry_after is not None: headers['Retry-After'] = retry_after
                raise urllib.error.HTTPError(request.full_url, 429, 'limited', hdrs=headers, fp=None)
            return urlopen
        # A 429 accepted no run, so it is a refusal to retry, never an uncertain write.
        for header, wait in (('7', 7), (None, 30), ('Wed, 21 Oct 2026 07:28:00 GMT', 30), ('99999', 3600)):
            with self.assertRaises(DeliveryDeferred) as caught:
                post_followup(BC, 'hello', KEY, urlopen=limited(header))
            self.assertEqual(caught.exception.retry_after, wait)
        out = io.StringIO()
        with patch('quorum_cursor.post_followup', side_effect=DeliveryDeferred(7)), \
                patch('quorum_cursor.binding', return_value={'session_ref': BC}), \
                patch.dict(os.environ, {'CURSOR_API_KEY': KEY}, clear=True), \
                contextlib.redirect_stdout(out):
            self.assertEqual(main_with({'session_ref': BC, 'content': 'hello'}), 2)
        self.assertEqual(out.getvalue(), '7\n')
        with patch('quorum_cursor.subprocess.run',
                   return_value=subprocess.CompletedProcess([], 2, '7\n', '')):
            with self.assertRaises(DeliveryDeferred) as caught:
                submit({'session_ref': BC}, MID, 'hello')
        self.assertEqual(caught.exception.retry_after, 7)
        message = {'id': MID, 'recipient_id': 'agent', 'sender_role': 'peer', 'body': 'ping'}
        class Api:
            def call(self, operation, payload, request_id=None):
                if operation == 'session_requirements':
                    return {'protocol_version': 2, 'skill_version': '0.3.0'}
                if operation == 'session_inbox':
                    return [message]
                raise AssertionError(operation)
        with tempfile.TemporaryDirectory() as root:
            ledger = LocalLedger(Path(root))
            try:
                now, posts = [100.0], []
                def queue(item):
                    posts.append(now[0])
                    if len(posts) == 1: raise DeliveryDeferred(30)
                    return 'submitted'
                relay = Relay(Api(), ledger, {'agent_id': 'agent', 'session_ref': BC}, queue)
                relay.clock = lambda: now[0]
                self.assertEqual(relay.run_once(), 'idle')
                self.assertIsNone(ledger.setting('delivery:' + MID))
                relay.reconcile()
                now[0] = 129.0
                self.assertEqual(relay.run_once(), 'idle')
                self.assertEqual(posts, [100.0])
                now[0] = 130.0
                self.assertEqual(relay.run_once(), 'idle')
                self.assertEqual(posts, [100.0, 130.0])
                self.assertEqual(ledger.setting('delivery:' + MID)['state'], 'submitted')
            finally:
                ledger.db.close()

    def test_child_refuses_a_different_session_without_posting(self):
        from quorum_cursor import main
        posted = []
        with tempfile.TemporaryDirectory() as root:
            meta, env = cursor_env(root)
            try:
                with patch.dict(os.environ, env, clear=True), \
                        patch('quorum_cursor.post_followup', side_effect=lambda *args, **kwargs: posted.append(args)):
                    self.assertEqual(main_with({'session_ref': OTHER, 'content': 'nope'}), 1)
                self.assertEqual(posted, [])
                with patch.dict(os.environ, env, clear=True), \
                        patch('quorum_cursor.post_followup', return_value='submitted') as follow:
                    self.assertEqual(main_with({'session_ref': BC, 'content': 'hello'}), 0)
                follow.assert_called_once()
                self.assertEqual(follow.call_args.args[0], BC)
                self.assertNotIn(KEY, json.dumps(follow.call_args.args[:2]))
            finally:
                meta.close()


def main_with(payload):
    from quorum_cursor import main
    raw = json.dumps(payload).encode()
    with patch('sys.stdin', type('In', (), {'buffer': type('B', (), {'read': lambda self, n: raw})()})()):
        return main()


if __name__ == '__main__':
    unittest.main()
