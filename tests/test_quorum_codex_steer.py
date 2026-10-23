"""Urgent Codex steering tests. They never start a model, a chat or Codex itself.

A fixture app server listens on a private Unix socket at the documented control
path and speaks the real transport: an HTTP Upgrade to WebSocket, then one
JSON-RPC object per text frame without the `jsonrpc` member. Request order, the
expectedTurnId precondition, the bounds and the rejection/ambiguity rules are
exercised end to end through the pinned websockets client. A fixture is not
evidence that an owner's actual loaded chat was steered.
"""
import contextlib
import json
import os
from pathlib import Path
import socket
import stat
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch
import uuid

SCRIPTS = Path(__file__).resolve().parents[1] / 'skills/quorum/scripts'
sys.path.insert(0, str(SCRIPTS))
SID = '11111111-1111-4111-8111-111111111111'
OTHER_SID = '11111111-1111-4111-8111-111111111112'
MID = '22222222-2222-4222-8222-222222222222'
TURN = 'turn_0199a000-0000-7000-8000-000000000001'
OTHER_TURN = 'turn_0199a000-0000-7000-8000-000000000002'
FORBIDDEN = ('thread/resume', 'thread/start', 'thread/fork', 'turn/start', 'turn/interrupt')
UNIX = os.name != 'nt' and hasattr(socket, 'AF_UNIX')

#: The suite may run inside another client's chat; never inherit that binding.
CLIENT_SESSION_VARIABLES = ('CLAUDE_CODE_SESSION_ID', 'CLAUDE_CODE_MESSAGING_SOCKET',
                            'CLAUDE_CODE_MESSAGING_TOKEN', 'GROK_SESSION_ID', 'GROK_LEADER_SOCKET',
                            'CODEX_HOME', 'CURSOR_AGENT', 'CURSOR_CONVERSATION_ID',
                            'CURSOR_AGENT_SOCKET', 'CURSOR_API_KEY')


def short_root(stack):
    # AF_UNIX paths are limited to about 104 bytes; macOS $TMPDIR is too long.
    base = '/tmp' if os.path.isdir('/tmp') and os.access('/tmp', os.W_OK) else None
    return Path(stack.enter_context(tempfile.TemporaryDirectory(prefix='qs', dir=base))).resolve()


class FakeAppServer:
    """A real Unix-socket WebSocket server answering like the Codex app server."""

    def __init__(self, path, scenario, cwd, home):
        from websockets.sync.server import unix_serve
        self.scenario, self.cwd, self.home = scenario, cwd, home
        self.frames, self.raw, self.connections, self.closed = [], [], 0, 0
        self.server = unix_serve(self.handle, str(path), compression=None, ping_interval=None)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def stop(self):
        self.server.shutdown()
        self.thread.join(timeout=5)

    def noise(self, connection, ref):
        """Server traffic a client must skip: notifications, requests, junk."""
        connection.send(json.dumps({'method': 'thread/status/changed', 'params': {'threadId': SID}}))
        connection.send(json.dumps({'id': ref, 'method': 'item/tool/requestUserInput', 'params': {}}))
        connection.send(json.dumps({'id': ref + 1000, 'result': {'turnId': TURN}}))
        connection.send(json.dumps({'id': str(ref), 'result': {'turnId': TURN}}))
        connection.send(json.dumps([{'id': ref, 'result': {}}]))
        connection.send('not json')
        connection.send(b'\x00binary')

    def handle(self, connection):
        self.connections += 1
        try:
            for message in connection:
                self.raw.append(message)
                frame = json.loads(message)
                self.frames.append(frame)
                ref, method, params = frame.get('id'), frame.get('method'), frame.get('params') or {}
                if ref is None: continue
                if self.scenario == 'noisy': self.noise(connection, ref)
                if method == 'initialize':
                    if self.scenario == 'silent_initialize': continue
                    if self.scenario == 'oversized':
                        connection.send(json.dumps({'id': ref, 'result': {'pad': 'x' * 300000}}))
                        continue
                    if self.scenario == 'flood':
                        for _ in range(600): connection.send(json.dumps({'method': 'noise'}))
                    home = os.path.join(self.home, 'other') if self.scenario == 'home_mismatch' else self.home
                    connection.send(json.dumps({'id': ref, 'result': {
                        'userAgent': 'fixture', 'codexHome': home, 'platformFamily': 'unix'}}))
                elif method == 'thread/loaded/list':
                    if self.scenario == 'not_loaded': listed = [OTHER_SID, '{' + SID + '}', SID.replace('-', ''), SID + ' ', 'urn:uuid:' + SID]
                    elif self.scenario == 'paged' and params.get('cursor') is None: listed = [OTHER_SID]
                    else: listed = [OTHER_SID, SID]
                    cursor = 'page-2' if self.scenario == 'paged' and params.get('cursor') is None else None
                    connection.send(json.dumps({'id': ref, 'result': {'data': listed, 'nextCursor': cursor}}))
                elif method == 'thread/read':
                    status = {'type': 'idle'} if self.scenario == 'idle' else {'type': 'active', 'activeFlags': []}
                    cwd = os.path.join(self.cwd, 'elsewhere') if self.scenario == 'cwd_mismatch' else self.cwd
                    thread = OTHER_SID if self.scenario == 'wrong_thread' else SID
                    connection.send(json.dumps({'id': ref, 'result': {
                        'thread': {'id': thread, 'cwd': cwd, 'status': status}}}))
                elif method == 'thread/turns/list':
                    if self.scenario == 'closed_before_steer':
                        connection.close()
                        return
                    status = 'completed' if self.scenario == 'turn_finished' else 'inProgress'
                    connection.send(json.dumps({'id': ref, 'result': {
                        'data': [{'id': TURN, 'status': status, 'items': []}]}}))
                elif method == 'turn/steer':
                    if self.scenario == 'ambiguous':
                        connection.close()
                        return
                    if self.scenario == 'silent_steer': continue
                    if self.scenario == 'flood_after_steer':
                        for _ in range(600): connection.send(json.dumps({'method': 'noise'}))
                    if self.scenario == 'not_steerable':
                        connection.send(json.dumps({'id': ref, 'error': {
                            'code': -32602, 'message': 'refused',
                            'data': {'activeTurnNotSteerable': {'turnKind': 'review'}}}}))
                    elif self.scenario == 'turn_changed':
                        connection.send(json.dumps({'id': ref, 'error': {
                            'code': -32602, 'message': 'expected turn mismatch'}}))
                    elif self.scenario == 'other_turn_result':
                        connection.send(json.dumps({'id': ref, 'result': {'turnId': OTHER_TURN}}))
                    elif self.scenario in ('opaque_replaced', 'opaque_completed', 'opaque_error'):
                        connection.send(json.dumps({'id': ref, 'error': {'code': -32602, 'message': 'refused'}}))
                    else:
                        connection.send(json.dumps({'id': ref, 'result': {'turnId': params['expectedTurnId']}}))
                else:
                    connection.send(json.dumps({'id': ref, 'error': {'code': -32601, 'message': 'unknown'}}))
        except Exception:
            pass
        finally:
            self.closed += 1


def steerable_answer(frame, cwd, home):
    """The reply a healthy app server gives to one read-only request."""
    ref, method = frame['id'], frame.get('method')
    if method == 'initialize': result = {'userAgent': 'fixture', 'codexHome': home}
    elif method == 'thread/loaded/list': result = {'data': [SID], 'nextCursor': None}
    elif method == 'thread/read': result = {'thread': {'id': SID, 'cwd': cwd, 'status': {'type': 'active'}}}
    elif method == 'thread/turns/list': result = {'data': [{'id': TURN, 'status': 'inProgress'}]}
    else: return {'id': ref, 'error': {'code': -32601, 'message': 'unknown'}}
    return {'id': ref, 'result': result}


class StallingAppServer:
    """A WebSocket peer that answers until one point and then never reads again.

    It speaks the real Upgrade and framing through the websockets Sans-I/O
    protocol on a raw socket, so nothing drains the client's writes once it
    stalls: the client's socket buffer fills as it would behind a wedged daemon.
    """

    def __init__(self, path, cwd, home, stall_after):
        self.cwd, self.home, self.stall_after = cwd, home, stall_after
        self.methods, self.connections = [], 0
        self.release = threading.Event()
        self.listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.listener.bind(str(path))
        self.listener.listen(1)
        self.listener.settimeout(.05)
        self.thread = threading.Thread(target=self.serve, daemon=True)
        self.thread.start()

    def stop(self):
        self.release.set()
        self.thread.join(timeout=5)
        self.listener.close()

    def serve(self):
        while not self.release.is_set():
            try: connection, _ = self.listener.accept()
            except TimeoutError: continue
            self.connections += 1
            with connection: self.converse(connection)

    def converse(self, connection):
        from websockets.frames import Opcode
        from websockets.http11 import Request
        from websockets.server import ServerProtocol
        connection.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 4096)
        connection.settimeout(.05)
        protocol, stalled = ServerProtocol(max_size=None), False
        while not stalled and not self.release.is_set():
            try: data = connection.recv(65536)
            except TimeoutError: continue
            if not data: return
            protocol.receive_data(data)
            for event in protocol.events_received():
                if isinstance(event, Request):
                    protocol.send_response(protocol.accept(event))
                    stalled = self.stall_after is None  # None: stall right after the Upgrade
                elif event.opcode is Opcode.TEXT:
                    frame = json.loads(event.data)
                    self.methods.append(frame.get('method'))
                    if 'id' in frame:
                        protocol.send_text(json.dumps(steerable_answer(frame, self.cwd, self.home)).encode())
                    stalled = stalled or frame.get('method') == self.stall_after
            for data in protocol.data_to_send(): connection.sendall(data)
        # Hold the connection open without reading until the test releases it.
        self.release.wait()


@contextlib.contextmanager
def small_client_send_buffer():
    """Give new client sockets a small send buffer, as the review fixture did.

    Without it a Linux Unix socket would absorb a whole steering frame and a
    blocked write could not be observed. Adopted sockets (accept, socketpair)
    keep their defaults.
    """
    original = socket.socket

    class Small(original):
        def __init__(self, family=-1, type=-1, proto=-1, fileno=None):
            super().__init__(family, type, proto, fileno)
            if fileno is None and family == socket.AF_UNIX:
                self.setsockopt(socket.SOL_SOCKET, socket.SO_SNDBUF, 4096)

    with patch.object(socket, 'socket', Small): yield


def bounded(test, call, limit=6):
    """Run `call` in a thread; fail when it is still blocked after `limit` seconds."""
    outcome = {}
    def run():
        try: outcome['value'] = call()
        except BaseException as error: outcome['error'] = error
    worker = threading.Thread(target=run, daemon=True)
    started = time.monotonic()
    worker.start()
    worker.join(limit)
    test.assertFalse(worker.is_alive(), 'the call stayed blocked past its deadline')
    return outcome, time.monotonic() - started


class SteerFixture:
    """A fixture daemon plus the environment one existing Codex chat would have.

    With `managed`, the endpoint has the layout of an actual managed daemon:
    the control entry is one link owned by this user to a 0600 socket in a
    0700 directory of this user outside CODEX_HOME (on macOS this directory is
    /private/tmp/codex-daemon-<uid>).
    """

    def __init__(self, stack, scenario, *, serve=True, managed=False):
        root = short_root(stack)
        self.root = root
        self.home = root / 'home'
        self.control = self.home / 'app-server-control'
        self.control.mkdir(parents=True, mode=0o700)
        self.path = self.control / 'app-server-control.sock'
        self.daemon = root / 'daemon'
        self.target = self.daemon / ('4c8b11a8' * 8)
        if managed:
            self.daemon.mkdir(mode=0o700)
            self.path.symlink_to(self.target)
        listen = self.target if managed else self.path
        self.cwd = str(root)
        environment = {key: value for key, value in os.environ.items()
                       if key not in CLIENT_SESSION_VARIABLES}
        environment.update({'CODEX_HOME': str(self.home), 'CODEX_THREAD_ID': SID})
        stack.enter_context(patch.dict(os.environ, environment, clear=True))
        # Steering is a socket client only; it must never spawn a process.
        stack.enter_context(patch('subprocess.Popen', side_effect=AssertionError('spawned a process')))
        self.server = None
        if serve:
            self.server = FakeAppServer(listen, scenario, self.cwd, str(self.home))
            stack.callback(self.server.stop)
            if managed: os.chmod(listen, 0o600)
        self.record = {'engine': 'codex', 'session_ref': SID, 'cwd': self.cwd, 'quorum': 'team'}

    def frames(self):
        return list(self.server.frames) if self.server else []

    def methods(self):
        return [frame.get('method') for frame in self.frames()]

    def steer_params(self):
        return [frame['params'] for frame in self.frames() if frame.get('method') == 'turn/steer']

    def wait_closed(self):
        deadline = time.monotonic() + 5
        while self.server.closed < self.server.connections and time.monotonic() < deadline: time.sleep(.02)


@unittest.skipUnless(UNIX, 'the Codex control socket is a Unix-domain socket')
class CodexSteerTests(unittest.TestCase):
    def fixture(self, scenario, **options):
        stack = contextlib.ExitStack()
        self.addCleanup(stack.close)
        return SteerFixture(stack, scenario, **options)

    def submit(self, fixture, text='peer prompt', timeout=10):
        from quorum_codex_steer import submit
        return submit(fixture.record, MID, lambda: text, timeout=timeout)

    def unavailable(self, fixture, reason, **options):
        from quorum_codex_steer import SteerUnavailable
        with self.assertRaises(SteerUnavailable) as caught: self.submit(fixture, **options)
        self.assertEqual(caught.exception.reason, reason)
        self.assertNotIn('turn/steer', fixture.methods())

    def assertNoTakeover(self, fixture):
        for method in FORBIDDEN: self.assertNotIn(method, fixture.methods())

    def test_active_turn_is_steered_over_a_unix_websocket(self):
        fixture = self.fixture('steerable')
        self.assertEqual(self.submit(fixture), 'submitted')
        self.assertEqual(fixture.methods()[:5],
                         ['initialize', 'initialized', 'thread/loaded/list', 'thread/read', 'thread/turns/list'])
        params = fixture.steer_params()
        self.assertEqual(len(params), 1)
        self.assertEqual(params[0]['threadId'], SID)
        self.assertEqual(params[0]['expectedTurnId'], TURN)
        self.assertEqual(params[0]['input'], [{'type': 'text', 'text': 'peer prompt'}])
        self.assertEqual(params[0]['clientUserMessageId'], 'quorum:' + MID)
        self.assertNoTakeover(fixture)

    def test_every_frame_is_one_json_object_without_jsonrpc_or_newlines(self):
        fixture = self.fixture('steerable')
        self.submit(fixture)
        self.assertTrue(fixture.server.raw)
        for raw in fixture.server.raw:
            self.assertIsInstance(raw, str)
            self.assertNotIn('\n', raw)
            frame = json.loads(raw)
            self.assertIsInstance(frame, dict)
            self.assertNotIn('jsonrpc', frame)

    def test_notifications_requests_and_junk_frames_are_filtered(self):
        fixture = self.fixture('noisy')
        self.assertEqual(self.submit(fixture), 'submitted')
        self.assertEqual(len(fixture.steer_params()), 1)

    def test_a_paged_loaded_list_finds_the_exact_thread(self):
        fixture = self.fixture('paged')
        self.assertEqual(self.submit(fixture), 'submitted')
        listed = [frame['params'] for frame in fixture.frames() if frame.get('method') == 'thread/loaded/list']
        self.assertEqual([page.get('cursor') for page in listed], [None, 'page-2'])

    def test_an_idle_chat_is_never_steered(self):
        self.unavailable(self.fixture('idle'), 'no_active_turn')

    def test_a_finished_head_turn_is_never_steered(self):
        self.unavailable(self.fixture('turn_finished'), 'no_active_turn')

    def test_only_the_exact_session_id_counts_as_loaded(self):
        fixture = self.fixture('not_loaded')
        self.unavailable(fixture, 'thread_not_loaded')
        self.assertNotIn('thread/read', fixture.methods())
        self.assertNoTakeover(fixture)

    def test_a_read_of_another_thread_is_refused(self):
        self.unavailable(self.fixture('wrong_thread'), 'thread_not_loaded')

    def test_a_daemon_view_of_another_directory_refuses_to_steer(self):
        self.unavailable(self.fixture('cwd_mismatch'), 'thread_directory_mismatch')

    def test_a_daemon_for_another_codex_home_is_refused(self):
        fixture = self.fixture('home_mismatch')
        self.unavailable(fixture, 'daemon_home_mismatch')
        self.assertEqual(fixture.methods(), ['initialize'])

    def test_a_silent_daemon_times_out_before_any_steering_write(self):
        fixture = self.fixture('silent_initialize')
        started = time.monotonic()
        self.unavailable(fixture, 'daemon_unreachable', timeout=1)
        self.assertLess(time.monotonic() - started, 5)

    def test_a_socket_closed_before_steering_reports_nothing_submitted(self):
        self.unavailable(self.fixture('closed_before_steer'), 'turns_unreadable')

    def test_an_oversized_frame_is_never_accepted(self):
        self.unavailable(self.fixture('oversized'), 'daemon_unreachable')

    def test_a_frame_flood_is_bounded(self):
        self.unavailable(self.fixture('flood'), 'daemon_unreachable')

    def test_a_turn_that_cannot_accept_steering_is_a_rejection(self):
        fixture = self.fixture('not_steerable')
        self.assertEqual(self.submit(fixture), 'rejected')
        self.assertEqual(len(fixture.steer_params()), 1)

    def test_a_changed_busy_turn_is_ambiguous_not_a_rejection(self):
        for scenario in ('turn_changed', 'other_turn_result'):
            with self.subTest(scenario=scenario):
                fixture = self.fixture(scenario)
                with self.assertRaisesRegex(ValueError, 'session_delivery_unknown'): self.submit(fixture)
                self.assertEqual(fixture.methods().count('turn/steer'), 1)

    def test_an_opaque_error_stays_unknown(self):
        for scenario in ('opaque_replaced', 'opaque_completed', 'opaque_error'):
            with self.subTest(scenario=scenario):
                fixture = self.fixture(scenario)
                with self.assertRaisesRegex(ValueError, 'session_delivery_unknown'): self.submit(fixture)
                self.assertEqual(fixture.methods().count('turn/steer'), 1)
                self.assertEqual(fixture.methods()[-1], 'turn/steer')

    def test_an_opaque_error_never_queues_a_copy(self):
        from quorum_session import enqueue
        fixture = self.fixture('opaque_error')
        message = {'id': MID, 'body': 'peer text', 'sender_role': 'peer', 'reply_to': None,
                   'priority': 'urgent'}
        with patch('quorum_session.shutil.which', return_value=sys.executable), \
             patch('quorum_session.subprocess.run', side_effect=AssertionError('duplicate fallback')):
            with self.assertRaisesRegex(ValueError, 'session_delivery_unknown'):
                enqueue(fixture.record, message, Path(fixture.cwd))
        self.assertEqual(fixture.methods().count('turn/steer'), 1)

    def test_lost_or_late_steering_responses_stay_unknown_without_replaying(self):
        for scenario in ('ambiguous', 'silent_steer', 'flood_after_steer'):
            with self.subTest(scenario=scenario):
                fixture = self.fixture(scenario)
                with self.assertRaisesRegex(ValueError, 'session_delivery_unknown'):
                    self.submit(fixture, timeout=2)
                self.assertEqual(fixture.methods().count('turn/steer'), 1)

    def test_cleanup_closes_only_the_client_and_leaves_the_server_running(self):
        fixture = self.fixture('steerable')
        self.submit(fixture)
        fixture.wait_closed()
        self.assertEqual(fixture.server.closed, fixture.server.connections)
        self.assertTrue(fixture.server.thread.is_alive())
        self.assertTrue(stat.S_ISSOCK(os.lstat(fixture.path).st_mode))
        self.assertEqual(self.submit(fixture), 'submitted')
        self.assertEqual(fixture.server.connections, 2)

    def test_a_missing_endpoint_is_unavailable(self):
        self.unavailable(self.fixture('steerable', serve=False), 'endpoint_missing')

    def test_a_stale_socket_without_a_listener_is_unavailable(self):
        fixture = self.fixture('steerable', serve=False)
        stale = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        stale.bind(str(fixture.path))
        stale.close()
        self.unavailable(fixture, 'daemon_unreachable')

    def test_a_non_socket_endpoint_is_refused(self):
        fixture = self.fixture('steerable', serve=False)
        fixture.path.write_text('', encoding='utf-8')
        self.unavailable(fixture, 'endpoint_invalid')

    def test_a_link_to_a_socket_in_a_shared_directory_is_refused(self):
        fixture = self.fixture('steerable', serve=False)
        target = fixture.home / 'real.sock'  # CODEX_HOME itself is 0755 here
        server = FakeAppServer(target, 'steerable', fixture.cwd, str(fixture.home))
        self.addCleanup(server.stop)
        os.chmod(target, 0o600)
        fixture.path.symlink_to(target)
        self.unavailable(fixture, 'endpoint_not_private')
        self.assertEqual(server.connections, 0)

    def test_the_managed_daemon_link_to_its_private_socket_is_steered(self):
        fixture = self.fixture('steerable', managed=True)
        self.assertTrue(stat.S_ISLNK(os.lstat(fixture.path).st_mode))
        self.assertEqual(self.submit(fixture), 'submitted')
        self.assertEqual(len(fixture.steer_params()), 1)
        self.assertNoTakeover(fixture)
        # The link and the daemon stay as they were.
        self.assertEqual(os.readlink(fixture.path), str(fixture.target))
        self.assertTrue(fixture.server.thread.is_alive())

    def test_hostile_managed_endpoint_variants_are_refused_without_a_frame(self):
        def relink(fixture, target):
            fixture.path.unlink()
            os.symlink(target, fixture.path)

        def relative(fixture):
            relink(fixture, os.path.relpath(fixture.target, fixture.control))

        def chained(fixture):
            hop = fixture.daemon / 'hop'
            hop.symlink_to(fixture.target)
            relink(fixture, hop)

        def loop(fixture):
            relink(fixture, fixture.path)

        def regular_file(fixture):
            plain = fixture.daemon / 'plain'
            plain.write_text('', encoding='utf-8')
            relink(fixture, plain)

        def stale(fixture):
            relink(fixture, fixture.daemon / 'gone')

        def linked_ancestor(fixture):
            alias = fixture.root / 'alias'
            alias.symlink_to(fixture.daemon)
            relink(fixture, alias / fixture.target.name)

        def chmod(path, mode):
            def change(fixture):
                restore = stat.S_IMODE(os.lstat(path(fixture)).st_mode)
                os.chmod(path(fixture), mode)
                self.addCleanup(os.chmod, path(fixture), restore)
            return change

        def foreign_socket(fixture):
            real = os.lstat
            def lstat(path, *args, **options):
                info = real(path, *args, **options)
                if os.fspath(path) != str(fixture.target): return info
                fields = list(info)
                fields[stat.ST_UID] = os.geteuid() + 1
                return os.stat_result(fields)
            self.enterContext(patch('quorum_codex_steer.os.lstat', side_effect=lstat))

        variants = {
            'relative': (relative, 'endpoint_invalid'),
            'chained': (chained, 'endpoint_invalid'),
            'loop': (loop, 'endpoint_invalid'),
            'regular_file': (regular_file, 'endpoint_invalid'),
            'stale': (stale, 'endpoint_missing'),
            'linked_ancestor': (linked_ancestor, 'endpoint_invalid'),
            'readable_directory': (chmod(lambda f: f.daemon, 0o755), 'endpoint_not_private'),
            'shared_directory': (chmod(lambda f: f.daemon, 0o777), 'endpoint_not_private'),
            'shared_socket': (chmod(lambda f: f.target, 0o666), 'endpoint_not_private'),
            'unsafe_ancestor': (chmod(lambda f: f.root, 0o777), 'endpoint_not_private'),
            'foreign_socket': (foreign_socket, 'endpoint_not_private'),
        }
        for name, (prepare, reason) in variants.items():
            with self.subTest(variant=name):
                fixture = self.fixture('steerable', managed=True)
                prepare(fixture)
                self.unavailable(fixture, reason)
                self.assertEqual(fixture.server.connections, 0)
                self.assertEqual(fixture.frames(), [])

    def test_a_steering_write_to_a_peer_that_stopped_reading_is_bounded_and_unknown(self):
        fixture = self.fixture('steerable', serve=False)
        server = StallingAppServer(fixture.path, fixture.cwd, str(fixture.home), 'thread/turns/list')
        self.addCleanup(server.stop)
        with small_client_send_buffer():
            outcome, elapsed = bounded(self, lambda: self.submit(fixture, 'x' * 48000, timeout=1))
        self.assertIsInstance(outcome.get('error'), ValueError)
        self.assertEqual(str(outcome['error']), 'session_delivery_unknown')
        self.assertLess(elapsed, 1 + 2 + 1)  # deadline plus the close bound
        self.assertEqual(server.methods[-1], 'thread/turns/list')
        self.assertEqual(server.connections, 1)

    def test_a_blocked_write_before_steering_is_bounded_and_unavailable(self):
        import asyncio
        from quorum_codex_steer import Client, SteerUnavailable
        for ambiguous, expected in ((False, SteerUnavailable), (True, ValueError)):
            with self.subTest(ambiguous=ambiguous):
                fixture = self.fixture('steerable', serve=False)
                server = StallingAppServer(fixture.path, fixture.cwd, str(fixture.home), None)
                self.addCleanup(server.stop)

                async def write():
                    client = Client(1)
                    await client.open(fixture.path)
                    try: await client.write({'id': 1, 'method': 'initialize', 'params': {'pad': 'x' * 200000}},
                                            ambiguous=ambiguous)
                    finally: await client.close()

                with small_client_send_buffer():
                    outcome, elapsed = bounded(self, lambda: asyncio.run(write()))
                self.assertIs(type(outcome.get('error')), expected)
                if ambiguous: self.assertEqual(str(outcome['error']), 'session_delivery_unknown')
                else: self.assertEqual(outcome['error'].reason, 'daemon_write_timeout')
                self.assertLess(elapsed, 1 + 2 + 1)
                self.assertEqual(server.methods, [])

    def test_a_shared_control_directory_is_refused(self):
        fixture = self.fixture('steerable')
        os.chmod(fixture.control, 0o777)
        self.addCleanup(os.chmod, fixture.control, 0o700)
        self.unavailable(fixture, 'endpoint_not_private')
        self.assertEqual(fixture.server.connections, 0)

    def test_a_foreign_owned_endpoint_is_refused(self):
        fixture = self.fixture('steerable')
        with patch('quorum_codex_steer.os.geteuid', return_value=os.geteuid() + 1):
            self.unavailable(fixture, 'endpoint_not_private')
        self.assertEqual(fixture.server.connections, 0)

    def test_a_foreign_peer_is_refused_before_the_handshake(self):
        fixture = self.fixture('steerable')
        with patch('quorum_codex_steer.peer_uid', return_value=os.geteuid() + 1):
            self.unavailable(fixture, 'endpoint_not_private')
        self.assertEqual(fixture.frames(), [])

    def test_the_peer_credentials_of_this_server_are_this_user(self):
        import quorum_codex_steer
        if sys.platform not in ('darwin', 'linux'): self.skipTest('no peer credential API')
        fixture = self.fixture('steerable')
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
            client.connect(str(fixture.path))
            self.assertEqual(quorum_codex_steer.peer_uid(client), os.geteuid())

    def test_another_chats_caller_cannot_steer_this_binding(self):
        fixture = self.fixture('steerable')
        with patch.dict(os.environ, {'CODEX_THREAD_ID': str(uuid.uuid4())}):
            from quorum_session import require_caller
            with self.assertRaisesRegex(ValueError, 'session_binding_denied'):
                require_caller(fixture.record['session_ref'], 'codex')

    def test_oversized_steering_text_is_refused_before_any_write(self):
        fixture = self.fixture('steerable')
        with self.assertRaisesRegex(ValueError, 'invalid_message'):
            self.submit(fixture, 'x' * 48001)
        self.assertNotIn('turn/steer', fixture.methods())


class UnixTransportAvailabilityTests(unittest.TestCase):
    def test_a_platform_without_unix_sockets_falls_back_explicitly(self):
        import quorum_codex_steer
        from quorum_codex_steer import SteerUnavailable, submit
        record = {'engine': 'codex', 'session_ref': SID, 'cwd': os.getcwd()}
        with patch.object(quorum_codex_steer, 'AF_UNIX', None), \
             patch('subprocess.Popen', side_effect=AssertionError('spawned a process')), \
             patch('socket.socket', side_effect=AssertionError('opened a socket')):
            with self.assertRaises(SteerUnavailable) as caught: submit(record, MID, lambda: 'x')
        self.assertEqual(caught.exception.reason, 'unix_transport_unsupported')

    @unittest.skipUnless(os.name == 'nt', 'Windows only')
    def test_windows_never_invents_a_unix_transport(self):
        from quorum_codex_steer import SteerUnavailable, submit
        record = {'engine': 'codex', 'session_ref': SID, 'cwd': os.getcwd()}
        with patch('subprocess.Popen', side_effect=AssertionError('spawned a console process')):
            with self.assertRaises(SteerUnavailable) as caught: submit(record, MID, lambda: 'x')
        self.assertEqual(caught.exception.reason, 'unix_transport_unsupported')

    def test_an_unavailable_route_queues_once_with_a_private_diagnostic(self):
        from quorum_codex_steer import SteerUnavailable
        from quorum_session import enqueue
        message = {'id': MID, 'body': 'peer text', 'sender_role': 'peer', 'reply_to': None,
                   'priority': 'urgent'}
        record = {'engine': 'codex', 'session_ref': SID, 'cwd': os.getcwd(), 'quorum': 'team'}
        calls = []
        def queued(command, **options):
            calls.append(command)
            return type('Result', (), {'returncode': 0})()
        with patch.dict(os.environ, {'CODEX_THREAD_ID': SID}), \
             patch('quorum_codex_steer.submit', side_effect=SteerUnavailable('unix_transport_unsupported')), \
             patch('quorum_session.shutil.which', return_value=sys.executable), \
             patch('quorum_session.subprocess.run', side_effect=queued), \
             patch('sys.stderr') as stderr:
            for key in CLIENT_SESSION_VARIABLES: os.environ.pop(key, None)
            enqueue(record, message, Path(os.getcwd()))
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0][1:4], ['queue', '--thread', SID])
        written = ''.join(call.args[0] for call in stderr.write.call_args_list)
        self.assertIn('unix_transport_unsupported', written)
        self.assertNotIn('peer text', written)


if __name__ == '__main__':
    unittest.main()
