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
import uuid
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
    def test_owner_relaunch_of_the_exact_session_with_resume_binds(self):
        from quorum_grok_leader import binding
        with own_session() as (root, _, peer):
            resumed = {'pid_start':12345, 'uid':os.geteuid(), 'tty':True, 'executable':'/fixture/grok',
                       'argv':['/fixture/grok', '--leader', '--leader-socket', str(peer.path),
                               '--cwd', str(root), '--resume', SID]}
            with patch('quorum_grok_leader.process_identity', return_value=resumed):
                result = binding(SID, str(root))
            self.assertEqual((result['leader_socket'], result['tui_pid']), (str(peer.path), os.getpid()))
            self.assertEqual(peer.methods, ['_x.ai/session/info', '_x.ai/sessions/list'])
            self.assertEqual(peer.prompts, [])

    @isolated_profiles
    def test_resume_of_another_session_title_fork_or_mixed_ids_is_denied(self):
        from quorum_grok_leader import binding
        with own_session() as (root, _, peer):
            base = ['/fixture/grok', '--leader', '--leader-socket', str(peer.path)]
            for tail in (['--resume', MID], ['--resume', 'my chat title'], ['--resume'],
                         ['--resume', SID, '--session-id', SID], ['--session-id', MID, '--resume', SID],
                         ['--resume', SID, '--resume', SID], ['-r', SID], ['--resume='+SID],
                         ['--resume', SID, '--fork-session'], ['--resume', SID, '--restore-code'],
                         ['--resume', SID, '--worktree'], ['--continue'], ['-c'], []):
                identity = {'pid_start':12345, 'uid':os.geteuid(), 'tty':True,
                            'executable':'/fixture/grok', 'argv':base+tail}
                with self.subTest(tail=tail), patch('quorum_grok_leader.process_identity', return_value=identity):
                    with self.assertRaisesRegex(ValueError, 'session_binding_denied'):
                        binding(SID, str(root))
            self.assertEqual(peer.methods, [])

    @isolated_profiles
    def test_missing_export_never_probes_a_stale_or_foreign_default_socket(self):
        from quorum_grok_leader import GrokBindingError, binding
        with own_session() as (root, home, _):
            default = Peer(home/'leader.sock', root)
            try:
                with patch.dict(os.environ):
                    del os.environ['GROK_LEADER_SOCKET']
                    with self.assertRaises(GrokBindingError) as refused:
                        binding(SID, str(root))
                self.assertEqual(refused.exception.diagnostic()['reason'], 'leader_socket_missing')
                self.assertEqual(default.methods, [])
            finally: default.close()

    @isolated_profiles
    def test_exported_socket_that_is_absent_reports_missing_before_any_probe(self):
        from quorum_grok_leader import GrokBindingError, binding
        with own_session() as (root, _, peer):
            private = root / 'private'
            private.mkdir(mode=0o700)
            with patch.dict(os.environ, {'GROK_LEADER_SOCKET': str(private / 'leader.sock')}):
                with self.assertRaises(GrokBindingError) as refused:
                    binding(SID, str(root))
            self.assertEqual(refused.exception.diagnostic()['reason'], 'leader_socket_missing')
            self.assertEqual(peer.methods, [])

    @isolated_profiles
    def test_plain_grok_inheriting_another_leaders_socket_is_not_attached(self):
        from quorum_grok_leader import binding
        with own_session() as (root, _, peer):
            # A plain TUI started from another leader's shell inherits its export.
            for argv in (['/fixture/grok'], ['/fixture/grok', '--resume', SID],
                         ['/fixture/grok', '--leader', '--resume', SID]):
                identity = {'pid_start':12345, 'uid':os.geteuid(), 'tty':True,
                            'executable':'/fixture/grok', 'argv':argv}
                with self.subTest(argv=argv), patch('quorum_grok_leader.process_identity', return_value=identity):
                    with self.assertRaisesRegex(ValueError, 'session_binding_denied'):
                        binding(SID, str(root))
            self.assertEqual(peer.methods, [])

    @isolated_profiles
    def test_resume_socket_under_a_shared_default_style_parent_is_refused(self):
        from quorum_grok_leader import binding
        with own_session() as (root, _, _):
            shared = root / 'shared'
            shared.mkdir()
            shared.chmod(0o755)  # Like Grok's default ~/.grok parent.
            exposed = Peer(shared / 'leader.sock', root)
            try:
                identity = {'pid_start':12345, 'uid':os.geteuid(), 'tty':True, 'executable':'/fixture/grok',
                            'argv':['/fixture/grok', '--leader', '--leader-socket', str(exposed.path),
                                    '--resume', SID]}
                with patch.dict(os.environ, {'GROK_LEADER_SOCKET': str(exposed.path)}), \
                     patch('quorum_grok_leader.process_identity', return_value=identity):
                    with self.assertRaisesRegex(ValueError, 'grok_transport_unavailable'):
                        binding(SID, str(root))
                self.assertEqual(exposed.methods, [])
            finally: exposed.close()

    @isolated_profiles
    def test_qualified_170_resume_keeps_exact_session_and_queue_contract(self):
        from quorum_grok_leader import binding, submit
        with own_session() as (root, _, peer):
            peer.version = '1.0.70'
            resumed = {'pid_start':12345, 'uid':os.geteuid(), 'tty':True, 'executable':'/fixture/grok',
                       'argv':['/fixture/grok', '--leader', '--leader-socket', str(peer.path),
                               '--cwd', str(root), '--resume', SID]}
            with patch('quorum_grok_leader.process_identity', return_value=resumed):
                transport = binding(SID, str(root))
                record = {'engine':'grok', 'session_ref':SID, 'cwd':str(root), 'grok_transport':transport}
                submit(record, MID, 'bounded native QA message')
            self.assertEqual(transport, {'leader_socket':str(peer.path), 'tui_pid':os.getpid(),
                                         'tui_start':12345, 'cwd':str(root.resolve())})
            self.assertEqual(peer.methods, ['_x.ai/session/info', '_x.ai/sessions/list',
                                           '_x.ai/session/info', '_x.ai/sessions/list', 'session/prompt'])
            self.assertEqual(len(peer.prompts), 1)
            self.assertEqual(peer.prompts[0]['params'], {'sessionId':SID,
                'prompt':[{'type':'text', 'text':'bounded native QA message'}],
                '_meta':{'promptId':'quorum:'+MID}})

    @isolated_profiles
    def test_binding_denial_names_the_failed_check_and_no_path(self):
        from quorum_grok_leader import GrokBindingDenied, binding, verify_process
        base = {'pid_start':12345, 'uid':os.geteuid(), 'tty':True, 'executable':'/fixture/grok'}
        with own_session() as (root, _, peer):
            flags = ['/fixture/grok', '--leader', '--leader-socket', str(peer.path), '--session-id', SID]
            cases = (({'executable':'/other/grok', 'argv':flags}, 'executable_mismatch'),
                     ({'tty':False, 'argv':flags}, 'no_controlling_terminal'),
                     ({'pid_start':1, 'argv':flags}, 'process_identity_mismatch'),
                     ({'argv':flags[:-2]}, 'flag_mismatch'),
                     ({'argv':[*flags, '--always-approve']}, 'flag_mismatch'))
            for change, reason in cases:
                with self.subTest(reason=reason), patch('quorum_grok_leader.process_identity',
                                                        return_value={**base, **change}):
                    with self.assertRaises(GrokBindingDenied) as caught: binding(SID, str(root))
                    data = caught.exception.diagnostic()
                    self.assertEqual((str(caught.exception), data['reason']), ('session_binding_denied', reason))
                    self.assertFalse(data['path_has_whitespace'])
                    self.assertNotIn(str(root), json.dumps(data))
            with patch.dict(os.environ, {'GROK_SESSION_ID':MID}), self.assertRaises(GrokBindingDenied) as caught:
                binding(SID, str(root))
            self.assertEqual(caught.exception.reason, 'session_export_mismatch')
            with patch('quorum_grok_leader.process_identity', side_effect=OSError), \
                    self.assertRaises(GrokBindingDenied) as caught:
                binding(SID, str(root))
            self.assertEqual(caught.exception.reason, 'process_unavailable')
            peer.resident = False
            with self.assertRaises(GrokBindingDenied) as caught: binding(SID, str(root))
            self.assertEqual(caught.exception.reason, 'session_metadata_mismatch')
            # `ps` splits a spaced path, which is the case the boolean explains.
            spaced = '/private/my dir/leader.sock'
            identity = {**base, 'argv':['/fixture/grok', '--leader', '--leader-socket', *spaced.split(), '--session-id', SID]}
            with patch('quorum_grok_leader.process_identity', return_value=identity), \
                    self.assertRaises(GrokBindingDenied) as caught:
                verify_process(1, 12345, spaced, SID, str(root))
            data = caught.exception.diagnostic()
            self.assertEqual((data['reason'], data['path_has_whitespace']), ('flag_mismatch', True))
            self.assertNotIn('my dir', json.dumps(data))

    @isolated_profiles
    def test_cli_and_preflight_carry_the_typed_reason(self):
        from quorum import main
        from quorum_grok_leader import GrokBindingDenied
        from quorum_session import UnsupportedClient, client_preflight
        output = io.StringIO()
        with tempfile.TemporaryDirectory() as state, patch.dict(os.environ, {'GROK_SESSION_ID':SID}, clear=True), \
                patch('quorum_session._dispatch', side_effect=GrokBindingDenied('executable_mismatch')), \
                contextlib.redirect_stdout(output):
            code = main(['--state', state, 'session', 'recover'])
        result = json.loads(output.getvalue())
        self.assertEqual((code, result['code'], result['data']['reason']),
                         (3, 'session_binding_denied', 'executable_mismatch'))
        with patch.dict(os.environ, {'GROK_SESSION_ID':SID}, clear=True), patch('sys.platform', 'darwin'), \
                patch('quorum_grok_leader.binding', side_effect=GrokBindingDenied('flag_mismatch')), \
                self.assertRaises(UnsupportedClient) as caught:
            client_preflight()
        self.assertEqual(caught.exception.diagnostic()['binding_reason'], 'flag_mismatch')

    def test_every_denial_reason_has_fixed_guidance(self):
        from quorum_grok_leader import GrokBindingDenied
        source = (SCRIPTS / 'quorum_grok_leader.py').read_text(encoding='utf-8')
        import re
        used = set(re.findall(r"GrokBindingDenied\('([a-z_]+)'", source))
        self.assertTrue(used)
        self.assertLessEqual(used, set(GrokBindingDenied.NEXT_STEPS))

    def test_the_accepted_build_statement_is_the_same_everywhere(self):
        """One sentence, derived from the accepted lists, in every page that states it."""
        from quorum_grok_leader import VERSIONS, WINDOWS_VERSIONS
        def spoken(versions): return ', '.join(versions[:-1]) + ' and ' + versions[-1]
        statement = ('Accepted Grok TUI builds: macOS ' + spoken(VERSIONS) + '; Windows '
                     + spoken(WINDOWS_VERSIONS) + '.')
        skill = SCRIPTS.parent
        for path in (skill / 'SKILL.md', skill / 'README.md', skill / 'references/sessions.md',
                     skill / 'references/grok.md'):
            with self.subTest(path=path.name):
                self.assertIn(statement, ' '.join(path.read_text(encoding='utf-8').split()))

    @isolated_profiles
    def test_macos_accepts_the_qualified_177_handshake_but_not_an_unlisted_build(self):
        """#139: the same handshake fields a live macOS 1.0.77 leader reported."""
        from quorum_grok_leader import binding, submit
        with own_session() as (root, _, peer):
            peer.version = '1.0.77'
            transport = binding(SID, str(root))
            submit({'engine':'grok', 'session_ref':SID, 'cwd':str(root), 'grok_transport':transport}, MID, 'hello')
            self.assertEqual(len(peer.prompts), 1)
            peer.version = '1.0.78'
            with self.assertRaisesRegex(ValueError, 'grok_transport_unavailable'): binding(SID, str(root))

    @isolated_profiles
    def test_running_170_stays_bound_after_path_grok_self_updates(self):
        """#135: the updater repoints `grok` at 1.0.77 while this TUI keeps running 1.0.70."""
        from quorum_grok_leader import binding, submit
        with own_session() as (root, _, peer):
            peer.version = '1.0.70'
            downloads = root / 'downloads'
            downloads.mkdir(mode=0o755)
            for name in ('grok-1.0.70-macos-aarch64', 'grok-1.0.77-macos-aarch64'):
                (downloads / name).write_bytes(b'')
                (downloads / name).chmod(0o755)
            running = str((downloads / 'grok-1.0.70-macos-aarch64').resolve())
            resumed = {'pid_start':12345, 'uid':os.geteuid(), 'tty':True, 'executable':running,
                       'argv':[running, '--leader', '--leader-socket', str(peer.path), '--resume', SID]}
            before = {'leader_socket':str(peer.path), 'tui_pid':os.getpid(), 'tui_start':12345,
                      'cwd':str(root.resolve())}
            with patch('quorum_grok_leader.process_identity', return_value=resumed), \
                 patch('quorum_grok_leader.shutil.which', return_value=str(downloads / 'grok-1.0.77-macos-aarch64')):
                # A transport stored before the update still compares equal, so a live listener keeps delivering.
                self.assertEqual(binding(SID, str(root)), before)
                submit({'engine':'grok', 'session_ref':SID, 'cwd':str(root), 'grok_transport':before}, MID, 'after update')
                self.assertEqual(len(peer.prompts), 1)
                # The leader must itself be the build the TUI image claims.
                peer.version = '1.0.67'
                with self.assertRaisesRegex(ValueError, 'session_binding_denied'): binding(SID, str(root))

    @isolated_profiles
    def test_qualified_170_still_refuses_wrong_session_or_nonresident_metadata(self):
        from quorum_grok_leader import binding
        with own_session() as (root, _, peer):
            peer.version = '1.0.70'
            for sid, resident in ((MID, True), (SID, False)):
                peer.sid, peer.resident = sid, resident
                with self.subTest(sid=sid, resident=resident):
                    with self.assertRaisesRegex(ValueError, 'session_binding_denied'):
                        binding(SID, str(root))
            self.assertEqual(peer.prompts, [])

    @isolated_profiles
    def test_untested_binary_version_is_refused(self):
        from quorum_grok_leader import binding
        with own_session() as (root, _, peer):
            for version in ('1.0.68', '1.0.69', '1.0.71', '1.0.70-preview', '2.0.0', None):
                peer.version = version
                with self.subTest(version=version):
                    with self.assertRaisesRegex(ValueError, 'grok_transport_unavailable'):
                        binding(SID, str(root))
            self.assertEqual(peer.methods, [])

    @isolated_profiles
    def test_accept_preflight_runs_the_read_only_leader_handshake_before_consuming_membership(self):
        import quorum
        invite = '33333333-3333-4333-8333-333333333333'
        class Store:
            backend = None
            def load(self, _): return None
        def accept(root, api):
            output = io.StringIO()
            with patch('quorum_session.Path.cwd', return_value=root), \
                 patch('quorum_live.api_for', return_value=(api, __import__('quorum_api').ServiceConfig(
                     'https://example.com', 'sb_publishable_synthetic'), Store())), \
                 patch('quorum_auth.login_pkce', return_value={'authenticated': True}) as login, \
                 patch('quorum_api.request_json', side_effect=AssertionError('no live call')), \
                 contextlib.redirect_stdout(output):
                code = quorum.main(['--state', str(root.resolve()/'state'), 'accept-id', invite, '--origin',
                                    'https://example.com', '--publishable-key', 'sb_publishable_synthetic'])
            return code, json.loads(output.getvalue()), login
        class Api:
            def __init__(self): self.calls = []
            def call(self, op, payload, request_id=None):
                self.calls.append(op)
                return {'quorum_id': 'q', 'membership_id': 'm'}
        with own_session() as (root, _, peer):
            api = Api()
            code, result, login = accept(root, api)
            self.assertEqual((code, result['data'].get('native_preflight')), (0, 'passed'), result)
            self.assertEqual(peer.methods, ['_x.ai/session/info', '_x.ai/sessions/list'])
            self.assertEqual((api.calls, login.call_count), (['invite_accept_id'], 1))
        def unsupported(peer, home): peer.version = '1.0.68'
        def foreign(peer, home): peer.sid = '44444444-4444-4444-8444-444444444444'
        def nonresident(peer, home): peer.resident = False
        def missing(peer, home): os.environ['GROK_LEADER_SOCKET'] = str(home/'missing.sock')
        for change in (unsupported, foreign, nonresident, missing):
            with self.subTest(change=change.__name__), own_session() as (root, home, peer):
                change(peer, home)
                api = Api()
                code, result, login = accept(root, api)
                self.assertEqual((code, result['code'], result['data']['reason']),
                                 (3, 'unsupported_client', 'grok_leader_unavailable'))
                self.assertEqual((api.calls, login.call_count, peer.prompts), ([], 0, []))
                self.assertNotIn('session/prompt', peer.methods)
                for secret in (str(root), SID, peer.sid):
                    self.assertNotIn(secret, json.dumps(result))

    @isolated_profiles
    def test_current_grok_identity_is_used_and_mixed_client_exports_are_denied(self):
        from quorum_session import current_engine, current_session
        with own_session():
            self.assertEqual((current_engine(), current_session()), ('grok', SID))
            with patch.dict(os.environ, {'CODEX_THREAD_ID': MID}):
                with self.assertRaisesRegex(ValueError, 'ambiguous_current_session'):
                    current_session()

    @isolated_profiles
    def test_inherited_claude_export_surfaces_ambiguity_with_names_only(self):
        # A Grok leader TUI launched from a Claude Code shell inherits its session export (#109).
        # Every export and the leader socket are inheritable, so nothing is guessed: the CLI names
        # the detected variables, never their values, and points to a clean-environment relaunch.
        import quorum
        claude_sid = '33333333-3333-4333-8333-333333333333'
        with own_session() as (root, _, peer), patch.dict(os.environ, {'CLAUDE_CODE_SESSION_ID': claude_sid}):
            for argv in (['session', 'status'],
                         ['session', 'connect', 'test-team', '--role', 'peer', '--project-ref', 'demo']):
                output = io.StringIO()
                with self.subTest(argv=argv[1]), contextlib.redirect_stdout(output):
                    code = quorum.main(['--state', str(root/'state'), *argv])
                result = json.loads(output.getvalue())
                self.assertEqual((code, result['ok'], result['code']), (3, False, 'ambiguous_current_session'))
                self.assertEqual(result['data']['detected_env'], ['CLAUDE_CODE_SESSION_ID', 'GROK_SESSION_ID'])
                self.assertIn('env -u CODEX_THREAD_ID -u CLAUDE_CODE_SESSION_ID -u GROK_SESSION_ID',
                              result['data']['next_step'])
                self.assertIn('references/grok.md', result['data']['next_step'])
                for secret in (SID, claude_sid, str(root), os.environ['GROK_LEADER_SOCKET']):
                    self.assertNotIn(secret, output.getvalue())
                self.assertEqual(peer.prompts, [])
            self.assertFalse((root/'state'/'sessions'/SID).exists())
            self.assertFalse((root/'state'/'sessions'/claude_sid).exists())

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
                intent = ledger.setting('delivery:'+MID)
                self.assertEqual(intent['state'], 'intent')
                self.assertEqual(str(uuid.UUID(intent['attempt_id'])), intent['attempt_id'])
                with self.assertRaisesRegex(ValueError, 'session_delivery_unknown'): relay.run_once()
                self.assertEqual(ledger.setting('delivery:'+MID), intent)
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
        from quorum_grok_leader import GrokPlatformUnsupported, local_binding
        with patch.dict(os.environ, {'GROK_SESSION_ID':SID}, clear=True), patch('sys.platform', 'linux'):
            with self.assertRaisesRegex(ValueError, '^grok_leader_platform_unsupported$') as caught:
                local_binding(SID, '/fixture')
            data = caught.exception.diagnostic()
        self.assertIsInstance(caught.exception, GrokPlatformUnsupported)
        self.assertEqual((data['platform'], data['supported_platforms']), ('linux', ['darwin', 'win32']))
        self.assertIn('Nothing was registered', data['next_step'])

    @isolated_profiles
    def test_connect_start_and_recover_report_the_platform_before_any_service_call(self):
        """#145: not the missing-socket code, and no login or service work first."""
        from quorum import main
        for action in (['connect', 'team', '--role', 'grok', '--project-ref', 'p'], ['start'], ['recover']):
            output = io.StringIO()
            with self.subTest(action=action[0]), tempfile.TemporaryDirectory() as state, \
                    patch.dict(os.environ, {'GROK_SESSION_ID':SID, 'GROK_LEADER_SOCKET':'/private/secret.sock'},
                               clear=True), \
                    patch('sys.platform', 'linux'), \
                    patch('quorum_live.api_for', side_effect=AssertionError('no service call')), \
                    contextlib.redirect_stdout(output):
                code = main(['--state', state, 'session', *action])
                result = json.loads(output.getvalue())
                self.assertEqual((code, result['code']), (3, 'grok_leader_platform_unsupported'))
                self.assertEqual(result['data']['supported_platforms'], ['darwin', 'win32'])
                self.assertNotIn('secret', output.getvalue())
                self.assertNotIn(SID, output.getvalue())


if __name__ == '__main__': unittest.main()
