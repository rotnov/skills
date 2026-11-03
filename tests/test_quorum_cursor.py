"""Cursor Cloud identity and follow-up tests. They never start an agent or call api.cursor.com."""
import contextlib
import http.client
import http.server
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
import uuid
from types import SimpleNamespace
from unittest.mock import patch

from quorum_test_support import isolated_profiles

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

    def test_a_supported_export_wins_over_inherited_cursor_markers(self):
        # A Codex chat started from a Cursor Cloud shell inherits CURSOR_* and stays Codex.
        from quorum_session import current_engine, current_session
        with tempfile.TemporaryDirectory() as root:
            meta, env = cursor_env(root)
            try:
                with patch.dict(os.environ, {**env, 'CODEX_THREAD_ID': MID}, clear=True):
                    self.assertEqual((current_session(), current_engine()), (MID, 'codex'))
                with patch.dict(os.environ, {**env, 'CODEX_THREAD_ID': MID, 'GROK_SESSION_ID': MID}, clear=True):
                    with self.assertRaisesRegex(ValueError, '^ambiguous_current_session$'):
                        current_session()
                # A non-bc- conversation id or CURSOR_AGENT other than 1 is not this client.
                for change in ({'CURSOR_AGENT': '0'}, {'CURSOR_CONVERSATION_ID': MID}):
                    with patch.dict(os.environ, {**env, **change}, clear=True):
                        self.assertEqual(current_engine(), 'codex')
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

    def test_definite_refusals_are_a_fixed_failure_and_other_outcomes_stay_unknown(self):
        from quorum_cursor import DeliveryDeferred, IngressRefused, post_followup
        def status(code):
            def urlopen(request, timeout):
                raise urllib.error.HTTPError(request.full_url, code, 'refused', hdrs=None, fp=None)
            return urlopen
        for code in (301, 302, 303, 307, 308, 400, 401, 403, 404):
            with self.subTest(code=code), self.assertRaisesRegex(IngressRefused, '^cursor_ingress_unavailable$'):
                post_followup(BC, 'hello', KEY, urlopen=status(code))
        for code in (409, 429):
            with self.subTest(code=code), self.assertRaises(DeliveryDeferred):
                post_followup(BC, 'hello', KEY, urlopen=status(code))
        # A server error or lost response may follow a created run: never a fixed failure.
        for opener in (status(500), status(502), lambda request, timeout: Response(204)):
            with self.assertRaisesRegex(ValueError, '^session_delivery_unknown$') as caught:
                post_followup(BC, 'hello', KEY, urlopen=opener)
            self.assertNotIsInstance(caught.exception, IngressRefused)
        def lost(request, timeout): raise TimeoutError()
        with self.assertRaisesRegex(ValueError, '^session_delivery_unknown$'):
            post_followup(BC, 'hello', KEY, urlopen=lost)

    def test_a_real_redirect_is_refused_without_following_it_or_resending_the_key(self):
        from quorum_cursor import IngressRefused, post_followup
        seen = []
        class Handler(http.server.BaseHTTPRequestHandler):
            def log_message(self, *_): pass
            def do_POST(self):
                seen.append((self.path, self.headers.get('Authorization')))
                self.rfile.read(int(self.headers.get('Content-Length') or 0))
                self.send_response(307)
                self.send_header('Location', '/elsewhere')
                self.send_header('Content-Length', '0')
                self.end_headers()
            do_GET = do_POST
        server = http.server.HTTPServer(('127.0.0.1', 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            base = 'http://127.0.0.1:%d/v1/agents/' % server.server_address[1]
            with patch('quorum_cursor._API', base), \
                    self.assertRaisesRegex(IngressRefused, '^cursor_ingress_unavailable$'):
                post_followup(BC, 'hello', KEY)
        finally:
            server.shutdown()
            server.server_close()
        self.assertEqual(seen, [('/v1/agents/' + BC + '/runs', 'Bearer ' + KEY)])

    def test_refusal_clears_only_its_intent_and_stops_with_the_fixed_code(self):
        from quorum_cursor import IngressRefused, main, submit
        from quorum_session import Relay
        from quorum_state import LocalLedger
        with patch('quorum_cursor.subprocess.run', return_value=subprocess.CompletedProcess([], 3, '', '')):
            with self.assertRaises(IngressRefused):
                submit({'session_ref': BC}, MID, 'hello')
        with patch('quorum_cursor.post_followup', side_effect=IngressRefused()), \
                patch('quorum_cursor.binding', return_value={'session_ref': BC}), \
                patch.dict(os.environ, {'CURSOR_API_KEY': KEY}, clear=True), \
                contextlib.redirect_stdout(io.StringIO()) as out:
            self.assertEqual(main_with({'session_ref': BC, 'content': 'hello'}), 3)
        self.assertEqual(out.getvalue(), '')
        with tempfile.TemporaryDirectory() as root:
            ledger = LocalLedger(Path(root))
            try:
                relay = Relay(InboxApi([inbox_message(MID)]), ledger, {'agent_id': 'agent', 'session_ref': BC},
                              lambda item: (_ for _ in ()).throw(IngressRefused()))
                with self.assertRaisesRegex(ValueError, '^cursor_ingress_unavailable$'):
                    relay.run_once()
                self.assertIsNone(ledger.setting('delivery:' + MID))
                # No intent is left behind, so an owner-authorized restart is not wedged.
                relay.reconcile()
            finally:
                ledger.db.close()

    def test_pending_retry_after_also_holds_back_later_messages(self):
        from quorum_cursor import DeliveryDeferred
        from quorum_session import Relay
        from quorum_state import LocalLedger
        later = '33333333-3333-4333-8333-333333333333'
        api = InboxApi([inbox_message(MID)])
        with tempfile.TemporaryDirectory() as root:
            ledger = LocalLedger(Path(root))
            try:
                now, posts = [100.0], []
                def queue(item):
                    posts.append((item['id'], now[0]))
                    if len(posts) == 1: raise DeliveryDeferred(30)
                    return 'submitted'
                relay = Relay(api, ledger, {'agent_id': 'agent', 'session_ref': BC}, queue)
                relay.clock = lambda: now[0]
                self.assertEqual(relay.run_once(), 'idle')
                # The head changed (for example, it was withdrawn); the key is still rate-limited.
                api.inbox = [inbox_message(later)]
                now[0] = 129.0
                self.assertEqual(relay.run_once(), 'idle')
                self.assertEqual(posts, [(MID, 100.0)])
                self.assertIsNone(ledger.setting('delivery:' + later))
                now[0] = 130.0
                self.assertEqual(relay.run_once(), 'idle')
                self.assertEqual(posts, [(MID, 100.0), (later, 130.0)])
                self.assertEqual(ledger.setting('delivery:' + later)['state'], 'submitted')
            finally:
                ledger.db.close()

    def test_listener_end_to_end_retries_busy_then_counts_the_chats_receipt(self):
        from quorum_listener import Control, run_listener
        from quorum_session import Relay, enqueue
        from quorum_state import LocalLedger
        record = {'agent_id': 'agent', 'session_ref': BC, 'engine': 'cursor', 'quorum': 'team'}
        env = {'CURSOR_AGENT': '1', 'CURSOR_CONVERSATION_ID': BC}
        children = []
        def child(command, **kwargs):
            payload = json.loads(kwargs['input'])
            children.append(payload)
            self.assertEqual(payload['session_ref'], BC)
            self.assertNotIn(KEY, kwargs['input'])
            # The first follow-up finds the run busy (409, no Retry-After), the second is accepted.
            return subprocess.CompletedProcess(command, 2 if len(children) == 1 else 0,
                                               '0\n' if len(children) == 1 else '', '')
        with tempfile.TemporaryDirectory() as root:
            ledger = LocalLedger(Path(root))
            api = InboxApi([inbox_message(MID)])
            stop = threading.Event()
            def sleep(_):
                delivery = ledger.setting('delivery:' + MID)
                if delivery and delivery['state'] == 'submitted':
                    # This agent's next turn runs `session received` for the envelope.
                    ledger.setting('delivery:' + MID, {'state': 'acked'})
                    api.inbox = []
                elif Control(ledger).status()['handled']:
                    stop.set()
            try:
                with patch.dict(os.environ, env, clear=True), \
                        patch('quorum_cursor.subprocess.run', side_effect=child):
                    status = run_listener(ledger, lambda: Relay(api, ledger, record,
                                                                lambda item: enqueue(record, item, Path(root))),
                                          str(uuid.uuid4()), sleep=sleep, stop_event=stop, timeout=0)
                self.assertEqual(status['state'], 'stopped')
                self.assertEqual(Control(ledger).status()['handled'], 1)
                self.assertEqual(len(children), 2)
                self.assertIn('session', children[1]['content'])
                self.assertIn('received', children[1]['content'])
            finally:
                ledger.db.close()

    def test_listener_end_to_end_stops_on_a_definite_refusal_without_unknown_state(self):
        from quorum_listener import run_listener
        from quorum_session import Relay, enqueue
        from quorum_state import LocalLedger
        record = {'agent_id': 'agent', 'session_ref': BC, 'engine': 'cursor', 'quorum': 'team'}
        with tempfile.TemporaryDirectory() as root:
            ledger = LocalLedger(Path(root))
            api = InboxApi([inbox_message(MID)])
            try:
                with patch.dict(os.environ, {'CURSOR_AGENT': '1', 'CURSOR_CONVERSATION_ID': BC}, clear=True), \
                        patch('quorum_cursor.subprocess.run',
                              return_value=subprocess.CompletedProcess([], 3, '', '')):
                    status = run_listener(ledger, lambda: Relay(api, ledger, record,
                                                                lambda item: enqueue(record, item, Path(root))),
                                          str(uuid.uuid4()), sleep=lambda _: None, timeout=0)
                self.assertEqual((status['state'], status['code']), ('error', 'cursor_ingress_unavailable'))
                self.assertIsNone(ledger.setting('delivery:' + MID))
            finally:
                ledger.db.close()

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


class InboxApi:
    def __init__(self, inbox):
        self.inbox = inbox
    def call(self, operation, payload, request_id=None):
        if operation == 'session_requirements':
            return {'protocol_version': 2, 'skill_version': '0.3.0'}
        if operation == 'session_inbox':
            return list(self.inbox)
        raise AssertionError(operation)


def inbox_message(message_id):
    return {'id': message_id, 'recipient_id': 'agent', 'sender_role': 'peer', 'body': 'ping'}


class CursorRecoveryTests(unittest.TestCase):
    """Cursor has no resume hook or qualified same-VM resume, so it is not RECOVERABLE."""

    def test_cursor_is_not_a_recoverable_engine_or_resume_hook_client(self):
        import quorum_resume
        from quorum_session import RECOVERABLE
        self.assertNotIn('cursor', RECOVERABLE)
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as caught:
            quorum_resume.main(['--client', 'cursor'])
        self.assertEqual(caught.exception.code, 2)

    @isolated_profiles
    def test_session_recover_is_denied_while_explicit_start_still_works(self):
        from quorum_session import dispatch
        from quorum_state import LocalLedger
        from test_quorum_session import Generation, RequirementsApi
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            ledger = LocalLedger(root / 'sessions' / BC)
            children = []
            def spawn(command, ledger):
                child = Generation(ledger.root, command[command.index('--run-id') + 1])
                children.append(child)
                return child
            try:
                registration = {'engine': 'cursor', 'session_ref': BC, 'project_ref': 'project', 'cwd': str(root)}
                ledger.setting('session-agent', {**registration, 'registration': registration, 'agent_id': 'agent',
                                                 'quorum_id': 'quorum', 'role': 'worker', 'quorum': 'team',
                                                 'cursor_transport': {'session_ref': BC}})
                def run(action):
                    with patch.dict(os.environ, {'CURSOR_AGENT': '1', 'CURSOR_CONVERSATION_ID': BC}, clear=True), \
                            patch('pathlib.Path.cwd', return_value=root), \
                            patch('quorum_live.api_for', return_value=(RequirementsApi(), None, None)), \
                            patch('quorum_cursor.binding', return_value={'session_ref': BC}), \
                            patch('quorum_session.spawn_background', side_effect=spawn):
                        return dispatch(SimpleNamespace(action=action, session_id=BC), SimpleNamespace(root=root), None)
                started = run('start')
                self.assertEqual(len(children), 1)
                with self.assertRaisesRegex(ValueError, '^session_recovery_denied$'):
                    run('recover')
                self.assertEqual(len(children), 1)
                self.assertTrue(started['run_id'])
            finally:
                for child in children: child.close()
                ledger.db.close()


def main_with(payload):
    from quorum_cursor import main
    raw = json.dumps(payload).encode()
    with patch('sys.stdin', type('In', (), {'buffer': type('B', (), {'read': lambda self, n: raw})()})()):
        return main()


if __name__ == '__main__':
    unittest.main()
