"""Grok Windows leader transport: synthetic identity everywhere, real named-pipe IPC on Windows."""
from contextlib import contextmanager
import ctypes
from ctypes import wintypes
import io
import json
import os
from pathlib import Path
import struct
import sys
import tempfile
import threading
import time
import unittest
import uuid
from quorum_test_support import isolated_profiles
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

SCRIPTS = Path(__file__).resolve().parents[1] / 'skills/quorum/scripts'
sys.path.insert(0, str(SCRIPTS))
SID = '11111111-1111-4111-8111-111111111111'
USER = 'S-1-5-21-1-2-3-1001'
EXE = os.path.normcase(str(Path('/fixture/grok.exe').resolve()))
OPENED = '2026-11-03T18:06:36.353956Z'
TUI_START = 1793729194722790  # Just before OPENED.
PIPE = 'grok-leader-ac4cff0b896f73b6'
MID = '22222222-2222-4222-8222-222222222222'


class FramedPipe:
    """A synthetic leader pipe that answers Register with the given binary version."""
    def __init__(self, version):
        raw = json.dumps({'type': 'registered', 'client_id': 1, 'ready': True,
                          'leader_protocol_version': 1, 'leader_binary_version': version}).encode()
        self.inbound, self.sent, self.closed = struct.pack('>I', len(raw)) + raw, [], False

    def __call__(self, pid, start): return self
    def settimeout(self, value): pass
    def connect(self, name): pass
    def sendall(self, data): self.sent.append(data)
    def close(self): self.closed = True

    def recv(self, count):
        chunk, self.inbound = self.inbound[:count], self.inbound[count:]
        return chunk


@contextmanager
def windows_session(argv=None, rows=None):
    """A Windows-shaped Grok home and process table: TUI 100 with leader child 200."""
    import quorum_grok_win
    with tempfile.TemporaryDirectory(prefix='grok-w-') as temporary:
        root = Path(temporary)
        home = root / 'home'
        home.mkdir()
        socket = str(root / 'leader.sock')
        default = [{'session_id': SID, 'pid': 100, 'cwd': str(root), 'opened_at': OPENED}]
        (home / 'active_sessions.json').write_text(json.dumps(default if rows is None else rows(root)))
        table = {
            100: {'pid_start': TUI_START, 'uid': USER, 'ppid': 1, 'executable': EXE,
                  'argv': argv or [EXE, '--leader', '--leader-socket', socket, '--session-id', SID]},
            200: {'pid_start': TUI_START + 700000, 'uid': USER, 'ppid': 100, 'executable': EXE,
                  'argv': [EXE, 'agent', 'leader', '--no-exit-on-disconnect', '--relay-on-demand']},
        }
        servers = {PIPE: 200}
        def identity(pid):
            if pid not in table: raise OSError('process_unavailable')
            return dict(table[pid])
        def opened(name):
            if name not in servers: raise OSError('pipe_unavailable')
            if servers[name] is None: raise quorum_grok_win.Busy('pipe_busy')  # Serving another client.
            kernel = SimpleNamespace(CloseHandle=lambda handle: None, SetNamedPipeHandleState=lambda *args: 1)
            return (ctypes, wintypes, kernel), name
        with patch.dict(os.environ, {'GROK_SESSION_ID': SID, 'GROK_HOME': str(home),
                                     'GROK_LEADER_SOCKET': socket}, clear=True), \
             patch('quorum_grok_leader.sys.platform', 'win32'), \
             patch('quorum_grok_leader.shutil.which', return_value='/fixture/grok.exe'), \
             patch('quorum_grok_win.process_identity', side_effect=identity), \
             patch('quorum_grok_win.own_sid', return_value=USER), \
             patch('quorum_grok_win.os.listdir', side_effect=lambda path: list(servers) + ['other-pipe']), \
             patch('quorum_grok_win._open', side_effect=opened), \
             patch('quorum_grok_win._server', side_effect=lambda api, handle: servers[handle]), \
             patch('quorum_winproof.verify'):
            yield root, table, servers


class WindowsBindingTests(unittest.TestCase):
    def setUp(self):
        import quorum_grok_win
        self.addCleanup(patch.stopall)
        patch.object(quorum_grok_win, '_api', side_effect=AssertionError('no Win32 call')).start()

    def bind(self, root):
        from quorum_grok_leader import local_binding
        return local_binding(SID, str(root))

    @isolated_profiles
    def test_live_tui_binds_to_its_own_leader_pipe(self):
        with windows_session() as (root, _, _):
            self.assertEqual(self.bind(root), {
                'leader_socket': str(root / 'leader.sock'), 'leader_pipe': PIPE, 'leader_pid': 200,
                'leader_start': TUI_START + 700000, 'tui_pid': 100, 'tui_start': TUI_START,
                'cwd': str(root.resolve())})

    @isolated_profiles
    def test_resume_of_the_exact_session_binds(self):
        with windows_session() as (root, table, _):
            table[100]['argv'] = [EXE, '--leader', '--leader-socket', str(root / 'leader.sock'), '--resume', SID]
            self.assertEqual(self.bind(root)['tui_pid'], 100)

    @isolated_profiles
    def test_resume_beside_stale_rows_of_killed_tuis_binds(self):
        def rows(root):
            row = {'session_id': SID, 'cwd': str(root), 'opened_at': '2026-11-03T17:00:00Z'}
            return [{**row, 'pid': 300}, {**row, 'pid': 301},
                    {'session_id': SID, 'pid': 100, 'cwd': str(root), 'opened_at': OPENED}]
        with windows_session(rows=rows) as (root, table, _):
            table[301] = {**table[100], 'pid_start': TUI_START}  # PID reused after the row.
            table[100]['argv'] = [EXE, '--leader', '--leader-socket', str(root / 'leader.sock'), '--resume', SID]
            self.assertEqual(self.bind(root)['tui_pid'], 100)

    @isolated_profiles
    def test_tui_born_exactly_when_its_row_opened_binds(self):
        from quorum_grok_leader import opened_at
        with windows_session() as (root, table, _):
            table[100]['pid_start'] = opened_at({'opened_at': OPENED})
            table[200]['pid_start'] = table[100]['pid_start'] + 2000000  # Its leader starts after it.
            self.assertEqual(self.bind(root)['tui_start'], table[100]['pid_start'])
            table[100]['pid_start'] += 1
            with self.assertRaisesRegex(ValueError, 'session_binding_denied'): self.bind(root)

    @isolated_profiles
    def test_busy_leader_pipe_is_unavailable_not_missing(self):
        from quorum_grok_leader import GrokBindingError
        with windows_session() as (root, _, servers):
            servers[PIPE] = None
            with self.assertRaisesRegex(ValueError, 'grok_transport_unavailable') as busy: self.bind(root)
            self.assertNotIsInstance(busy.exception, GrokBindingError)

    @isolated_profiles
    def test_unexpected_errors_become_fixed_codes(self):
        with windows_session() as (root, _, _),              patch('quorum_grok_win.discover', side_effect=RuntimeError('raw detail')):
            with self.assertRaises(ValueError) as denied: self.bind(root)
            self.assertEqual(str(denied.exception), 'session_binding_denied')
        with windows_session() as (root, _, _), patch('quorum_grok_leader.shutil.which', return_value=None):
            with self.assertRaisesRegex(ValueError, 'session_binding_denied'): self.bind(root)

    @isolated_profiles
    def test_windows_leader_hint_names_the_leader_socket_flag(self):
        from quorum_grok_leader import GrokBindingError
        with patch('quorum_grok_leader.sys.platform', 'win32'):
            hint = GrokBindingError('leader_socket_missing').diagnostic()
        self.assertEqual(hint['reason'], 'leader_socket_missing')
        self.assertIn('--leader-socket <path>', hint['next_step'])

    @isolated_profiles
    def test_pipe_leader_accepts_only_qualified_versions(self):
        from quorum_grok_leader import Leader
        for version, accepted in (('1.0.70', True), ('1.0.77', True), ('1.0.67', False),
                                  ('1.0.71', False), ('1.0.78', False)):
            pipe = FramedPipe(version)
            with self.subTest(version=version), patch('quorum_grok_win.Pipe', pipe):
                if accepted: Leader(PIPE, (200, TUI_START)).close()
                else:
                    with self.assertRaisesRegex(ValueError, 'grok_transport_unavailable'):
                        Leader(PIPE, (200, TUI_START))
                self.assertTrue(pipe.closed)

    @isolated_profiles
    def test_main_prompts_through_the_bound_leader_pipe_only(self):
        import quorum_grok_leader
        with windows_session() as (root, _, _):
            transport = self.bind(root)
            def run(grok_transport):
                record = {'session_ref': SID, 'cwd': str(root), 'grok_transport': grok_transport}
                raw = json.dumps({'record': record, 'message_id': MID, 'content': 'hello'}).encode()
                with patch('quorum_grok_leader.Leader') as leader,                      patch('quorum_grok_leader.sys.stdin', SimpleNamespace(buffer=io.BytesIO(raw))):
                    return quorum_grok_leader.main(), leader
            code, leader = run(transport)
            self.assertEqual(code, 0)
            leader.assert_called_once_with(PIPE, (200, TUI_START + 700000))
            leader.return_value.prompt.assert_called_once_with(SID, MID, 'hello')
            for change in ({'leader_pid': 201}, {'leader_start': TUI_START}, {'leader_pipe': 'grok-leader-0000000000000001'}):
                with self.subTest(change=change):
                    code, leader = run({**transport, **change})
                    self.assertEqual(code, 2)  # Definite unsent: no leader was contacted.
                    leader.assert_not_called()

    @isolated_profiles
    def test_connect_routes_pipe_and_socket_records(self):
        import quorum_grok_leader
        with patch('quorum_grok_leader.Leader', MagicMock()) as leader:
            quorum_grok_leader.connect({'leader_socket': 'x', 'leader_pipe': PIPE, 'leader_pid': 200, 'leader_start': 7})
            quorum_grok_leader.connect({'leader_socket': '/private/leader.sock'})
        self.assertEqual([call.args for call in leader.call_args_list], [(PIPE, (200, 7)), ('/private/leader.sock',)])

    @isolated_profiles
    def test_missing_export_or_leader_pipe_reports_leader_socket_missing(self):
        from quorum_grok_leader import GrokBindingError
        with windows_session() as (root, _, servers):
            del os.environ['GROK_LEADER_SOCKET']
            with self.assertRaises(GrokBindingError) as missing: self.bind(root)
            self.assertEqual(missing.exception.reason, 'leader_socket_missing')
        with windows_session() as (root, _, servers):
            servers.clear()
            with self.assertRaises(GrokBindingError) as absent: self.bind(root)
            self.assertEqual(absent.exception.reason, 'leader_socket_missing')

    @isolated_profiles
    def test_closed_or_reused_tui_row_is_denied(self):
        with windows_session() as (root, table, _):
            del table[100]  # Grok 1.0.70 leaves the row of a killed TUI behind.
            with self.assertRaisesRegex(ValueError, 'session_binding_denied'): self.bind(root)
        with windows_session() as (root, table, _):
            table[100]['pid_start'] = TUI_START + 5000000  # A new process born after the row.
            with self.assertRaisesRegex(ValueError, 'session_binding_denied'): self.bind(root)

    @isolated_profiles
    def test_duplicate_foreign_or_malformed_rows_are_denied(self):
        cases = (
            lambda root: [{'session_id': SID, 'pid': 100, 'cwd': str(root), 'opened_at': OPENED}] * 2,
            lambda root: [{'session_id': SID, 'pid': 100, 'cwd': str(root / 'other'), 'opened_at': OPENED}],
            lambda root: [{'session_id': SID, 'pid': 100, 'cwd': str(root), 'opened_at': '2026-11-03T18:06:36'}],
            lambda root: [{'session_id': SID, 'pid': True, 'cwd': str(root), 'opened_at': OPENED}],
            lambda root: [{'session_id': str(uuid.uuid4()), 'pid': 100, 'cwd': str(root), 'opened_at': OPENED}],
        )
        for rows in cases:
            with self.subTest(rows=rows), windows_session(rows=rows) as (root, _, _):
                with self.assertRaisesRegex(ValueError, 'session_binding_denied'): self.bind(root)

    @isolated_profiles
    def test_foreign_user_image_or_flags_are_denied(self):
        changes = (
            lambda root, tui: tui.update(uid='S-1-5-21-9-9-9-1002'),
            lambda root, tui: tui.update(executable=os.path.normcase(str(Path('/other/grok.exe').resolve()))),
            lambda root, tui: tui.update(argv=[EXE, '--leader', '--leader-socket', str(root / 'x.sock'), '--session-id', SID]),
            lambda root, tui: tui.update(argv=[EXE, '--leader', '--leader-socket', str(root / 'leader.sock'),
                                               '--session-id', SID, '--resume', SID]),
            lambda root, tui: tui.update(argv=[EXE, '--leader', '--leader-socket', str(root / 'leader.sock'),
                                               '--session-id', SID, 'prompt']),
            lambda root, tui: tui.update(argv=[EXE, '--leader-socket', str(root / 'leader.sock'), '--session-id', SID]),
        )
        for change in changes:
            with self.subTest(change=change), windows_session() as (root, table, _):
                change(root, table[100])
                with self.assertRaisesRegex(ValueError, 'session_binding_denied'): self.bind(root)

    @isolated_profiles
    def test_only_this_tuis_own_leader_child_is_selected(self):
        from quorum_grok_leader import GrokBindingError
        changes = (
            lambda leader: leader.update(ppid=999),
            lambda leader: leader.update(uid='S-1-5-21-9-9-9-1002'),
            lambda leader: leader.update(pid_start=TUI_START - 1),
            lambda leader: leader.update(argv=[EXE, '--leader']),
            lambda leader: leader.update(executable=os.path.normcase(str(Path('/other/grok.exe').resolve()))),
        )
        for change in changes:
            with self.subTest(change=change), windows_session() as (root, table, _):
                change(table[200])
                with self.assertRaises(GrokBindingError): self.bind(root)

    @isolated_profiles
    def test_two_leader_pipes_for_one_tui_are_ambiguous(self):
        with windows_session() as (root, table, servers):
            table[201] = dict(table[200])
            servers['grok-leader-0000000000000001'] = 201
            with self.assertRaisesRegex(ValueError, 'session_binding_denied'): self.bind(root)

    @isolated_profiles
    def test_unsafe_registry_file_is_refused(self):
        from quorum_winproof import Unsafe
        with windows_session() as (root, _, _), patch('quorum_winproof.verify', side_effect=Unsafe()):
            with self.assertRaisesRegex(ValueError, 'grok_transport_unavailable'): self.bind(root)

    @isolated_profiles
    def test_pipe_refuses_a_replaced_server_on_the_opened_handle(self):
        from quorum_grok_win import Pipe
        with windows_session() as (_, table, servers):
            Pipe(200, TUI_START + 700000).connect(PIPE)
            for pid, start in ((201, TUI_START + 700000), (200, TUI_START + 1)):
                with self.subTest(pid=pid), self.assertRaises(OSError):
                    Pipe(pid, start).connect(PIPE)
            with self.assertRaises(OSError): Pipe(200, TUI_START + 700000).connect('..\\other')

    @isolated_profiles
    def test_session_preflight_accepts_windows_but_not_linux(self):
        import quorum_session
        with patch.dict(os.environ, {'GROK_SESSION_ID': SID}, clear=True), \
             patch('quorum_session.current_session', return_value=SID), \
             patch('quorum_session.current_engine', return_value='grok'), \
             patch('quorum_grok_leader.binding', return_value={}) as binding:
            with patch('quorum_session.sys.platform', 'win32'):
                self.assertEqual(quorum_session.client_preflight(), 'grok')
            binding.assert_called_once()
            with patch('quorum_session.sys.platform', 'linux'):
                with self.assertRaises(quorum_session.UnsupportedClient): quorum_session.client_preflight()


@unittest.skipUnless(sys.platform == 'win32', 'Win32 named pipes')
class NativeWindowsTests(unittest.TestCase):
    @isolated_profiles
    def test_actual_process_identity_is_read_without_environment(self):
        from quorum_grok_win import own_sid, process_identity
        current = process_identity(os.getpid())
        self.assertEqual(current['uid'], own_sid())
        self.assertTrue(current['uid'].startswith('S-1-'))
        self.assertGreater(current['pid_start'], 1600000000000000)
        self.assertEqual(process_identity(os.getpid())['pid_start'], current['pid_start'])
        self.assertTrue(current['executable'].endswith('.exe'))
        self.assertGreater(current['ppid'], 0)
        self.assertTrue(current['argv'])
        self.assertNotIn('environment', current)
        with self.assertRaises(OSError): process_identity(4294967291)

    @isolated_profiles
    def test_real_pipe_handshake_checks_the_server_identity(self):
        import ctypes
        from ctypes import wintypes as w
        from quorum_grok_leader import Leader
        from quorum_grok_win import process_identity
        kernel = ctypes.WinDLL('kernel32', use_last_error=True)
        kernel.CreateNamedPipeW.restype = w.HANDLE
        kernel.CreateNamedPipeW.argtypes = (w.LPCWSTR, w.DWORD, w.DWORD, w.DWORD, w.DWORD, w.DWORD,
                                            w.DWORD, ctypes.c_void_p)
        for handle_call in (kernel.ConnectNamedPipe, kernel.DisconnectNamedPipe, kernel.CloseHandle,
                            kernel.FlushFileBuffers):
            handle_call.argtypes = (w.HANDLE,) + ((ctypes.c_void_p,) if handle_call is kernel.ConnectNamedPipe else ())
        kernel.ReadFile.argtypes = (w.HANDLE, ctypes.c_void_p, w.DWORD, ctypes.POINTER(w.DWORD), ctypes.c_void_p)
        kernel.WriteFile.argtypes = (w.HANDLE, ctypes.c_char_p, w.DWORD, ctypes.POINTER(w.DWORD), ctypes.c_void_p)
        name = 'grok-leader-' + uuid.uuid4().hex[:16]
        frames = []
        def serve(count):
            for _ in range(count):
                pipe = kernel.CreateNamedPipeW('\\\\.\\pipe\\' + name, 3, 0, 1, 65536, 65536, 0, None)
                kernel.ConnectNamedPipe(pipe, None)
                def exact(n):
                    raw = b''
                    while len(raw) < n:
                        buffer, got = ctypes.create_string_buffer(n - len(raw)), w.DWORD()
                        if not kernel.ReadFile(pipe, buffer, n - len(raw), ctypes.byref(got), None) or not got.value:
                            return None
                        raw += buffer.raw[:got.value]
                    return raw
                head = exact(4)
                if head:
                    frames.append(json.loads(exact(struct.unpack('>I', head)[0])))
                    raw = json.dumps({'type': 'registered', 'client_id': 1, 'ready': True,
                                      'leader_protocol_version': 1, 'leader_binary_version': '1.0.70'}).encode()
                    kernel.WriteFile(pipe, struct.pack('>I', len(raw)) + raw, len(raw) + 4, ctypes.byref(w.DWORD()), None)
                    kernel.FlushFileBuffers(pipe)
                    exact(4)  # Wait for the client's disconnect frame or close.
                kernel.DisconnectNamedPipe(pipe)
                kernel.CloseHandle(pipe)
        thread = threading.Thread(target=serve, args=(2,), daemon=True)
        thread.start()
        time.sleep(.2)
        start = process_identity(os.getpid())['pid_start']
        leader = Leader(name, (os.getpid(), start))
        leader.close()
        self.assertEqual(frames[0]['type'], 'register')
        time.sleep(.2)
        with self.assertRaises(OSError): Leader(name, (os.getpid(), start + 1))
        thread.join(timeout=5)

    @unittest.skipUnless(os.environ.get('GROK_SESSION_ID') and os.environ.get('GROK_LEADER_SOCKET'),
                         'run from the shell tool of a signed-in Grok leader TUI')
    @isolated_profiles
    def test_live_grok_leader_tui_binds(self):
        from quorum_grok_leader import binding
        result = binding(os.environ['GROK_SESSION_ID'], str(Path.cwd().resolve()))
        self.assertRegex(result['leader_pipe'], r'grok-leader-[0-9a-f]{16}')


if __name__ == '__main__': unittest.main()
