"""Grok existing-session transport: synthetic identity and real local IPC only."""
from contextlib import contextmanager
import json
import contextlib
import io
import os
from pathlib import Path
import socket
import struct
import sys
import tempfile
import subprocess
import threading
import unittest
from quorum_test_support import isolated_profiles
from unittest.mock import patch

SCRIPTS = Path(__file__).resolve().parents[1] / 'skills/quorum/scripts'
sys.path.insert(0, str(SCRIPTS))
SID = '11111111-1111-4111-8111-111111111111'
MID = '22222222-2222-4222-8222-222222222222'



class Peer:
    def __init__(self, path, cwd):
        self.path, self.cwd, self.methods, self.prompts = path, str(cwd), [], []
        self.sid, self.resident, self.version = SID, True, '1.0.67'
        self.drop_prompt = False
        self.server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.server.bind(str(path))
        path.chmod(0o755)  # Observed native Grok socket; parent is private.
        self.server.listen()
        self.server.settimeout(.1)
        self.stop = threading.Event()
        self.thread = threading.Thread(target=self.run)
        self.thread.start()
    def run(self):
        while not self.stop.is_set():
            try: connection, _ = self.server.accept()
            except socket.timeout: continue
            try:
                connection.settimeout(2)
                def receive():
                    def exact(n):
                        raw = b''
                        while len(raw) < n:
                            part = connection.recv(n - len(raw))
                            if not part: raise EOFError
                            raw += part
                        return raw
                    return json.loads(exact(struct.unpack('>I', exact(4))[0]))
                def send(frame):
                    raw = json.dumps(frame).encode()
                    connection.sendall(struct.pack('>I', len(raw)) + raw)
                registration = receive()
                if registration.get('type') != 'register': raise AssertionError('not registered')
                if any(registration.get('capabilities', {}).values()): raise AssertionError('capability override')
                send({'type': 'registered', 'client_id': 1, 'ready': True,
                      'leader_protocol_version': 1, 'leader_binary_version': self.version})
                while not self.stop.is_set():
                    frame = receive()
                    if frame['type'] == 'disconnect': break
                    request = json.loads(frame['payload'])
                    self.methods.append(request['method'])
                    if request['method'] == '_x.ai/session/info':
                        result = {'sessionId': self.sid, 'cwd': self.cwd}
                    elif request['method'] == '_x.ai/sessions/list':
                        result = {'sessions': [{'sessionId': self.sid, 'cwd': self.cwd,
                                               'resident': self.resident}]}
                    elif request['method'] == 'session/prompt':
                        self.prompts.append(request)
                        if self.drop_prompt: break
                        prompt_id = request['params']['_meta']['promptId']
                        send({'type': 'acp', 'payload': json.dumps({'jsonrpc': '2.0',
                            'method': '_x.ai/queue/changed', 'params': {'sessionId': SID,
                            'entries': [{'id': prompt_id}], 'runningPromptId': None}})})
                        continue  # Full-turn response deliberately never arrives.
                    else: raise AssertionError('unexpected method ' + request['method'])
                    send({'type': 'acp', 'payload': json.dumps({'jsonrpc': '2.0',
                        'id': request['id'], 'result': {'result': result}})})
            except (OSError, EOFError): pass
            finally: connection.close()
    def close(self):
        self.stop.set()
        self.thread.join(timeout=3)
        self.server.close()


@contextmanager
def own_session():
    with tempfile.TemporaryDirectory(prefix='grok-q-', dir='/tmp') as temporary:
        root = Path(temporary)
        home = root / 'home'
        home.mkdir(mode=0o700)
        active = home / 'active_sessions.json'
        active.write_text(json.dumps([{'session_id': SID, 'pid': os.getpid(),
            'pid_start': 12345, 'cwd': str(root), 'opened_at': '2026-10-22T10:00:00Z'}]))
        peer = Peer(root / 'leader.sock', root)
        try:
            with patch.dict(os.environ, {'GROK_SESSION_ID': SID, 'GROK_HOME': str(home),
                            'GROK_LEADER_SOCKET': str(peer.path)}, clear=True), \
                 patch('quorum_grok_leader.process_identity', return_value={'pid_start':12345, 'uid':os.geteuid(), 'tty':True, 'executable':'/fixture/grok', 'argv':['/fixture/grok', '--leader', '--leader-socket', str(peer.path), '--session-id', SID]}), \
                 patch('quorum_grok_leader.shutil.which', return_value='/fixture/grok'), \
                 patch('quorum_grok_leader.sys.platform', 'darwin'):
                yield root, home, peer
        finally: peer.close()


@unittest.skipUnless(os.name == 'posix' and hasattr(socket, 'AF_UNIX'), 'Unix transport')
class GrokTests(unittest.TestCase):
    def setUp(self):
        self.run_patch = patch('quorum_grok_leader.subprocess.run', side_effect=self.helper)
        self.run_patch.start()
        self.addCleanup(self.run_patch.stop)

    def helper(self, argv, *, input, **kwargs):
        import quorum_grok_leader
        with patch('sys.stdin', io.TextIOWrapper(io.BytesIO(input.encode()))):
            code = quorum_grok_leader.main()
        return subprocess.CompletedProcess(argv, code, '', '')

    @isolated_profiles
    def test_closed_reused_foreign_or_headless_process_is_denied(self):
        from quorum_grok_leader import binding
        with own_session() as (root, _, peer):
            original = {'pid_start':12345, 'uid':os.geteuid(), 'tty':True,
                        'executable':'/fixture/grok', 'argv':['/fixture/grok', '--leader',
                         '--leader-socket', str(peer.path), '--session-id', SID]}
            for change in ({'pid_start':54321}, {'uid':os.geteuid()+1}, {'tty':False},
                           {'executable':'/fixture/python'}, {'argv':['grok', '-p', 'headless']},
                           {'argv':['grok', 'agent']},
                           {'argv':original['argv']+['agent', 'stdio']},
                           {'argv':original['argv']+['a prompt', '--single', 'headless']},
                           {'argv':['grok', '--leader', '--session-id', MID]}):
                with self.subTest(change=change), patch('quorum_grok_leader.process_identity', return_value=original|change):
                    with self.assertRaisesRegex(ValueError, 'session_binding_denied'):
                        binding(SID, str(root))
            with patch('quorum_grok_leader.process_identity', side_effect=OSError):
                with self.assertRaisesRegex(ValueError, 'session_binding_denied'):
                    binding(SID, str(root))
            self.assertEqual(peer.prompts, [])

    @isolated_profiles
    def test_untested_binary_version_is_refused(self):
        from quorum_grok_leader import binding
        with own_session() as (root, _, peer):
            peer.version = '1.0.68'
            with self.assertRaisesRegex(ValueError, 'grok_transport_unavailable'):
                binding(SID, str(root))
            self.assertEqual(peer.methods, [])
    @isolated_profiles
    def test_current_grok_identity_is_used_and_mixed_client_exports_are_denied(self):
        from quorum_session import current_engine, current_session
        with own_session():
            self.assertEqual((current_engine(), current_session()), ('grok', SID))
            with patch.dict(os.environ, {'CODEX_THREAD_ID': MID}):
                with self.assertRaisesRegex(ValueError, 'ambiguous_current_session'):
                    current_session()

    @isolated_profiles
    def test_normal_envelope_uses_grok_and_requires_same_chat_receipt(self):
        from quorum_grok_leader import binding
        from quorum_session import enqueue
        with own_session() as (root, _, peer):
            record = {'engine': 'grok', 'session_ref': SID, 'cwd': str(root),
                'grok_transport': binding(SID, str(root)), 'quorum': 'test-team'}
            result = enqueue(record, {'id': MID, 'sender_role': 'peer', 'body': 'hello'}, root)
            self.assertEqual(result, 'submitted')
            text = peer.prompts[-1]['params']['prompt'][0]['text']
            self.assertIn('[Quorum · test-team · peer]', text)
            self.assertIn('"received", "'+MID+'"', text)
            self.assertIn('Preserve the owner task and permissions.', text)

    @isolated_profiles
    def test_connect_registers_the_actual_grok_chat_without_starting_a_listener(self):
        import quorum
        from quorum_listener import Control
        from quorum_state import LocalLedger
        with own_session() as (root, _, peer):
            requests = []
            class Api:
                def call(self, method, payload, request_id=None):
                    if method == 'session_requirements': return {'protocol_version':2, 'skill_version':'0.3.0'}
                    requests.append((method, payload))
                    return {'quorum_id': 'q', 'device_id': 'd', 'agent_id': 'a'}
            output = io.StringIO()
            with patch('quorum_live.api_for', return_value=(Api(), None, None)), \
                 patch('quorum_session.Path.cwd', return_value=root), contextlib.redirect_stdout(output):
                code = quorum.main(['--state', str(root/'state'), 'session', 'connect', 'test-team',
                                    '--role', 'grok-test', '--project-ref', 'shared-project'])
            self.assertEqual(code, 0, output.getvalue())
            self.assertEqual(json.loads(output.getvalue())['data']['session_ref'], SID)
            registration = next(data for method, data in requests if method == 'agent_join')
            self.assertEqual((registration['engine'], registration['session_ref']), ('grok', SID))
            state = LocalLedger(root/'state/sessions'/SID)
            try:
                self.assertEqual(Control(state).status()['listener'], 'offline')
                self.assertEqual(state.setting('session-agent')['grok_transport']['leader_socket'], str(peer.path))
            finally: state.db.close()

    @isolated_profiles
    def test_plain_tui_without_leader_reports_actionable_diagnostic_before_registration(self):
        import quorum
        from quorum_state import LocalLedger
        with own_session() as (root, _, peer):
            class Api:
                def call(self, *args, **kwargs):
                    if args[0] == 'session_requirements': return {'protocol_version':2, 'skill_version':'0.3.0'}
                    raise AssertionError('missing transport must not register an agent')
            output = io.StringIO()
            with patch.dict(os.environ):
                del os.environ['GROK_LEADER_SOCKET']
                with patch('quorum_live.api_for', return_value=(Api(), None, None)), \
                     patch('quorum_session.Path.cwd', return_value=root), \
                     contextlib.redirect_stdout(output):
                    code = quorum.main(['--state', str(root/'state'), 'session', 'connect', 'test-team',
                                        '--role', 'grok-test', '--project-ref', 'shared-project'])
            result = json.loads(output.getvalue())
            self.assertEqual(code, 3)
            self.assertEqual(result['code'], 'grok_transport_unavailable')
            self.assertEqual(result['data']['reason'], 'leader_socket_missing')
            self.assertIn('already-running standalone', result['data']['next_step'])
            self.assertIn('references/grok.md', result['data']['next_step'])
            self.assertNotIn(str(root), output.getvalue())
            self.assertNotIn(SID, output.getvalue())
            state = LocalLedger(root/'state/sessions'/SID)
            try:
                self.assertIsNone(state.setting('session-agent'))
            finally: state.db.close()
            self.assertEqual(peer.methods, [])

    @isolated_profiles
    def test_safe_binding_error_codes_do_not_expose_untrusted_exception_text(self):
        import quorum
        from quorum_grok_leader import GrokBindingError
        with own_session() as (root, _, _):
            for error, expected in ((ValueError('grok_transport_unavailable'), 'grok_transport_unavailable'),
                                    (ValueError('session_binding_denied'), 'session_binding_denied'),
                                    (GrokBindingError('secret provider body'), 'grok_transport_unavailable'),
                                    (ValueError('secret provider body'), 'operation_failed')):
                output = io.StringIO()
                with self.subTest(expected=expected), \
                     patch('quorum_live.dispatch', side_effect=error), contextlib.redirect_stdout(output):
                    code = quorum.main(['--state', str(root/'state'), 'session', 'status'])
                self.assertEqual(code, 3)
                self.assertEqual(json.loads(output.getvalue()),
                                 {'ok': False, 'code': expected, 'data': {}})

    @isolated_profiles
    def test_bind_verifies_current_export_active_tui_and_exact_resident_metadata(self):
        from quorum_grok_leader import binding
        with own_session() as (root, _, peer):
            result = binding(SID, str(root))
            self.assertEqual(result['leader_socket'], str(peer.path))
            self.assertEqual(result['tui_pid'], os.getpid())
            self.assertEqual(result['tui_start'], 12345)
            self.assertEqual(peer.prompts, [])
            self.assertEqual(peer.methods, ['_x.ai/session/info', '_x.ai/sessions/list'])

    @isolated_profiles
    def test_submit_uses_existing_session_and_stable_prompt_id_without_waiting_for_model(self):
        from quorum_grok_leader import binding, submit
        with own_session() as (root, _, peer):
            transport = binding(SID, str(root))
            record = {'session_ref': SID, 'cwd': str(root), 'grok_transport': transport}
            submit(record, MID, 'Quorum · тест: preserve owner permissions')
            self.assertEqual(len(peer.prompts), 1)
            params = peer.prompts[0]['params']
            self.assertEqual(params, {'sessionId': SID,
                'prompt': [{'type': 'text', 'text': 'Quorum · тест: preserve owner permissions'}],
                '_meta': {'promptId': 'quorum:' + MID}})
            self.assertNotIn('session/new', peer.methods)
            self.assertNotIn('session/load', peer.methods)

    @isolated_profiles
    def test_wrong_export_foreign_live_session_or_cwd_is_denied_before_a_prompt(self):
        from quorum_grok_leader import binding
        with own_session() as (root, _, peer):
            with self.assertRaisesRegex(ValueError, 'session_binding_denied'):
                binding(MID, str(root))
            peer.sid = MID
            with self.assertRaisesRegex(ValueError, 'session_binding_denied'):
                binding(SID, str(root))
            peer.sid, peer.cwd = SID, '/somewhere-else'
            with self.assertRaisesRegex(ValueError, 'session_binding_denied'):
                binding(SID, str(root))
            self.assertEqual(peer.prompts, [])

    @isolated_profiles
    def test_missing_active_tui_or_hidden_headless_row_is_not_resumed(self):
        from quorum_grok_leader import binding
        with own_session() as (root, home, peer):
            peer.resident = False
            with self.assertRaisesRegex(ValueError, 'session_binding_denied'):
                binding(SID, str(root))
            peer.resident = True
            (home / 'active_sessions.json').write_text('[]')
            with self.assertRaisesRegex(ValueError, 'session_binding_denied'):
                binding(SID, str(root))
            self.assertEqual(peer.prompts, [])

    @isolated_profiles
    def test_unsafe_socket_file_symlink_or_writable_mode_is_refused(self):
        from quorum_grok_leader import binding
        with own_session() as (root, _, peer):
            path = root / 'not-a-socket'
            path.write_text('unchanged')
            for endpoint in (str(path), str(root / '../elsewhere.sock')):
                with patch.dict(os.environ, {'GROK_LEADER_SOCKET': endpoint}):
                    with self.assertRaisesRegex(ValueError, 'grok_transport_unavailable'):
                        binding(SID, str(root))
            alias = root / 'alias.sock'
            alias.symlink_to(peer.path)
            with patch.dict(os.environ, {'GROK_LEADER_SOCKET': str(alias)}):
                with self.assertRaisesRegex(ValueError, 'grok_transport_unavailable'):
                    binding(SID, str(root))
            peer.path.chmod(0o777)
            with self.assertRaisesRegex(ValueError, 'grok_transport_unavailable'):
                binding(SID, str(root))
            self.assertEqual(path.read_text(), 'unchanged')

    @isolated_profiles
    def test_ambiguous_closed_submission_is_not_replayed(self):
        from quorum_grok_leader import binding, submit
        from quorum_session import Relay
        from quorum_state import LocalLedger
        with own_session() as (root, _, peer):
            record = {'session_ref': SID, 'cwd': str(root), 'grok_transport': binding(SID, str(root)),
                      'agent_id':'a'}
            peer.drop_prompt = True
            message = {'id':MID, 'recipient_id':'a'}
            class Api:
                def call(self, method, data):
                    if method == 'session_requirements': return {'protocol_version':2, 'skill_version':'0.3.0'}
                    if method == 'session_inbox': return [message]
                    raise AssertionError('unconfirmed message acknowledged')
            ledger = LocalLedger(root/'state')
            try:
                relay = Relay(Api(), ledger, record, lambda m: submit(record, m['id'], 'hello'))
                with self.assertRaisesRegex(ValueError, 'session_delivery_unknown'): relay.run_once()
                with self.assertRaisesRegex(ValueError, 'session_delivery_unknown'): relay.run_once()
                self.assertEqual(ledger.setting('delivery:'+MID), {'state':'intent'})
                self.assertEqual(len(peer.prompts), 1)
            finally: ledger.db.close()

    @isolated_profiles
    def test_changed_tui_binding_is_not_submitted_by_the_detached_helper(self):
        from quorum_grok_leader import binding, submit
        with own_session() as (root, home, peer):
            record = {'session_ref': SID, 'cwd': str(root), 'grok_transport': binding(SID, str(root))}
            current = json.loads((home / 'active_sessions.json').read_text())
            current[0]['pid_start'] = 54321
            (home / 'active_sessions.json').write_text(json.dumps(current))
            with self.assertRaisesRegex(ValueError, 'session_delivery_unknown'):
                submit(record, MID, 'must not reach a replacement UI')
            self.assertEqual(peer.prompts, [])


class NativeProcessTests(unittest.TestCase):
    @unittest.skipUnless(sys.platform == 'darwin', 'macOS proc_info ABI')
    @isolated_profiles
    def test_actual_process_birth_uid_and_executable_are_read_without_environment(self):
        from quorum_grok_leader import process_identity
        current = process_identity(os.getpid())
        self.assertEqual(current['uid'], os.geteuid())
        # Framework Python may exec Python.app while sys.executable still names
        # its launcher. Compare the OS image with independent dyld metadata.
        import ctypes
        image = ctypes.create_string_buffer(4096)
        size = ctypes.c_uint32(len(image))
        self.assertEqual(ctypes.CDLL(None)._NSGetExecutablePath(image, ctypes.byref(size)), 0)
        self.assertEqual(current['executable'], str(Path(os.fsdecode(image.value)).resolve()))
        self.assertGreater(current['pid_start'], 0)
        self.assertEqual(process_identity(os.getpid())['pid_start'], current['pid_start'])
        self.assertNotIn('environment', current)
        with self.assertRaises(OSError): process_identity(2147483647)

    @isolated_profiles
    def test_invalid_or_oversized_framing_is_refused(self):
        from quorum_grok_leader import Leader
        import time
        class Wire:
            def __init__(self, data): self.data = data
            def settimeout(self, value): pass
            def recv(self, count):
                out, self.data = self.data[:count], self.data[count:]
                return out
        for raw in (struct.pack('>I',262145), struct.pack('>I',2)+b'[]', struct.pack('>I',10)+b'{}'):
            leader = Leader.__new__(Leader)
            leader.socket = Wire(raw)
            with self.assertRaises((ValueError, EOFError)):
                leader.receive(time.monotonic()+1)

    @isolated_profiles
    def test_unsupported_platform_is_explicit(self):
        from quorum_grok_leader import local_binding
        with patch.dict(os.environ, {'GROK_SESSION_ID':SID}, clear=True), patch('sys.platform', 'win32'):
            with self.assertRaisesRegex(ValueError, 'grok_transport_unavailable'):
                local_binding(SID, '/fixture')


if __name__ == '__main__': unittest.main()
