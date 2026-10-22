"""Native wakeups use synthetic JWTs, disposable state and loopback WebSockets."""
from collections import deque
from contextlib import contextmanager
import json
from pathlib import Path
import sys
import tempfile
import threading
import time
import unittest
from types import SimpleNamespace

SCRIPTS = Path(__file__).resolve().parents[1] / 'skills/quorum/scripts'
sys.path.insert(0, str(SCRIPTS))
AGENT = '11111111-1111-4111-8111-111111111111'
TOPIC = 'realtime:quorum:agent:' + AGENT


@contextmanager
def ledger():
    from quorum_state import LocalLedger
    with tempfile.TemporaryDirectory() as temporary:
        state = LocalLedger(Path(temporary))
        try: yield state
        finally: state.db.close()


@contextmanager
def websocket_server(handler):
    from quorum_realtime import connect_socket
    from websockets.sync.server import serve
    errors = []
    def checked(connection):
        try: handler(connection)
        except Exception as error: errors.append(error)
    with serve(checked, '127.0.0.1', 0, ping_interval=None, close_timeout=.1) as server:
        thread = threading.Thread(target=server.serve_forever)
        thread.start()
        uri = 'ws://127.0.0.1:' + str(server.socket.getsockname()[1])
        def local_connect(_uri, **kwargs): return connect_socket(uri, **kwargs)
        try: yield local_connect
        finally:
            server.shutdown()
            thread.join(timeout=3)
    if errors: raise errors[0]


def reply(frame):
    return {'topic': frame['topic'], 'event': 'phx_reply', 'ref': frame['ref'],
            'join_ref': frame.get('join_ref'), 'payload': {'status': 'ok', 'response': {}}}


class Clock:
    def __init__(self): self.now = 0
    def __call__(self): return self.now
    def sleep(self, seconds): self.now += seconds


class Socket:
    """External network boundary; production parsing and timer logic remain real."""
    def __init__(self, clock, *, heartbeat=True, join=True):
        self.clock, self.heartbeat, self.join = clock, heartbeat, join
        self.frames, self.incoming, self.closed = [], deque(), False
    def send(self, raw):
        frame = json.loads(raw)
        self.frames.append(frame)
        if ((frame['event'] == 'phx_join' and self.join)
                or (frame['event'] == 'heartbeat' and self.heartbeat)):
            self.incoming.append(json.dumps(reply(frame)))
    def recv(self, timeout):
        if self.incoming: return self.incoming.popleft()
        self.clock.sleep(timeout)
        raise TimeoutError
    def close(self): self.closed = True


def api(token=lambda: 'synthetic-owner-jwt'):
    return SimpleNamespace(config=SimpleNamespace(origin='https://example.invalid',
                           publishable_key='synthetic-publishable-key'), token=token)


class RealtimeTests(unittest.TestCase):
    def test_redirect_is_rejected_before_the_jwt_can_be_sent_to_another_origin(self):
        from quorum_realtime import connect_socket
        from websockets.exceptions import InvalidStatus
        from websockets.sync.server import serve
        contacted = []
        def destination(connection): contacted.append(True)
        with serve(destination, '127.0.0.1', 0) as target:
            target_thread = threading.Thread(target=target.serve_forever)
            target_thread.start()
            def redirect(connection, request):
                response = connection.respond(307, 'redirect')
                response.headers['Location'] = 'ws://127.0.0.1:' + str(target.socket.getsockname()[1])
                return response
            try:
                with serve(lambda connection: None, '127.0.0.1', 0, process_request=redirect) as source:
                    source_thread = threading.Thread(target=source.serve_forever)
                    source_thread.start()
                    try:
                        with self.assertRaises(InvalidStatus):
                            connect_socket('ws://127.0.0.1:' + str(source.socket.getsockname()[1]), open_timeout=2)
                        self.assertEqual(contacted, [])
                    finally:
                        source.shutdown()
                        source_thread.join(timeout=3)
            finally:
                target.shutdown()
                target_thread.join(timeout=3)

    def test_offline_status_does_not_claim_a_live_notification_connection(self):
        from quorum_listener import Control
        with ledger() as state:
            state.setting('listener-wait', {'mode': 'realtime', 'poll_seconds': 60, 'signals': 2})
            status = Control(state).status()
            self.assertEqual((status['listener'], status['wait_mode']), ('offline', 'stopped'))
            self.assertEqual(status['notification_signals'], 2)

    def test_real_socket_joins_private_agent_channel_then_receives_only_wake_signal(self):
        from quorum_realtime import RealtimeWait
        observed = []
        def server(connection):
            frame = json.loads(connection.recv(timeout=3))
            observed.append(frame)
            connection.send(json.dumps(reply(frame)))
            connection.send(json.dumps({'topic': TOPIC, 'event': 'broadcast', 'ref': None,
                'payload': {'event': 'inbox_changed', 'type': 'broadcast', 'payload': {}}}))
            try: connection.recv(timeout=3)
            except Exception: pass
        with ledger() as state, websocket_server(server) as connect:
            wait = RealtimeWait(api(), {'agent_id': AGENT}, state, connect=connect)
            try:
                wait.prepare(lambda: False)
                self.assertEqual(state.setting('listener-wait')['mode'], 'realtime')
                before = time.monotonic()
                wait.wait(lambda: False)
                self.assertLess(time.monotonic() - before, 2)
            finally: wait.close()
        self.assertEqual(observed[0]['topic'], TOPIC)
        self.assertEqual(observed[0]['payload']['access_token'], 'synthetic-owner-jwt')
        self.assertEqual(observed[0]['payload']['config'], {
            'private': True, 'broadcast': {'ack': False, 'self': False},
            'presence': {'enabled': False}, 'postgres_changes': []})

    def test_healthy_wait_checks_queue_after_sixty_seconds_and_refreshes_jwt(self):
        from quorum_realtime import RealtimeWait
        clock, connected = Clock(), []
        socket = Socket(clock)
        def connect(url, **kwargs):
            connected.append((url, kwargs))
            return socket
        with ledger() as state:
            wait = RealtimeWait(api(lambda: 'new-jwt' if clock.now >= 20 else 'old-jwt'),
                                {'agent_id': AGENT}, state, connect=connect,
                                clock=clock, sleep=clock.sleep)
            try:
                wait.prepare(lambda: False)
                wait.wait(lambda: False)
                self.assertEqual(clock.now, 60)
                self.assertEqual(state.setting('listener-wait')['poll_seconds'], 60)
            finally: wait.close()
        self.assertEqual(connected[0][0], 'wss://example.invalid/realtime/v1/websocket?apikey=synthetic-publishable-key&vsn=1.0.0')
        self.assertNotIn('old-jwt', connected[0][0])
        refreshes = [x for x in socket.frames if x['event'] == 'access_token']
        self.assertEqual([x['payload'] for x in refreshes], [{'access_token': 'new-jwt'}])
        self.assertGreaterEqual(len([x for x in socket.frames if x['event'] == 'heartbeat']), 2)

    def test_missing_heartbeat_acknowledgement_enters_bounded_polling_fallback(self):
        from quorum_realtime import RealtimeWait
        clock = Clock()
        socket = Socket(clock, heartbeat=False)
        with ledger() as state:
            wait = RealtimeWait(api(), {'agent_id': AGENT}, state, connect=lambda *a, **k: socket,
                                clock=clock, sleep=clock.sleep)
            wait.prepare(lambda: False)
            wait.wait(lambda: False)
            self.assertTrue(socket.closed)
            self.assertEqual(state.setting('listener-wait')['mode'], 'polling_fallback')
            self.assertLessEqual(clock.now, 40)
            wait.close()

    def test_reconnect_returns_immediately_for_catchup_instead_of_waiting_for_new_signal(self):
        from quorum_realtime import RealtimeWait
        clock, calls = Clock(), []
        def connect(*args, **kwargs):
            calls.append(clock.now)
            if len(calls) == 1: raise OSError('synthetic-secret-must-not-be-reported')
            return Socket(clock)
        with ledger() as state:
            wait = RealtimeWait(api(), {'agent_id': AGENT}, state, connect=connect,
                                clock=clock, sleep=clock.sleep)
            try:
                wait.prepare(lambda: False)
                wait.wait(lambda: False)
                self.assertEqual(clock.now, 10)
                wait.wait(lambda: False)
                self.assertEqual(calls, [0, 10])
                self.assertEqual(clock.now, 10)
                self.assertEqual(state.setting('listener-wait')['mode'], 'realtime')
                self.assertNotIn('synthetic-secret', json.dumps(state.setting('listener-wait')))
            finally: wait.close()

    def test_a_real_closed_socket_falls_back_then_rejoins_and_receives_a_new_signal(self):
        from quorum_realtime import RealtimeWait
        clock, joins = Clock(), []
        def server(connection):
            frame = json.loads(connection.recv(timeout=3))
            joins.append(frame)
            connection.send(json.dumps(reply(frame)))
            if len(joins) == 1:
                connection.close()
                return
            connection.send(json.dumps({'topic': TOPIC, 'event': 'broadcast',
                'payload': {'event': 'inbox_changed', 'type': 'broadcast', 'payload': {}}}))
            try: connection.recv(timeout=3)
            except Exception: pass
        with ledger() as state, websocket_server(server) as connect:
            wait = RealtimeWait(api(), {'agent_id': AGENT}, state, connect=connect,
                                clock=clock, sleep=clock.sleep)
            try:
                wait.prepare(lambda: False)
                wait.wait(lambda: False)
                self.assertEqual(state.setting('listener-wait')['mode'], 'polling_fallback')
                wait.wait(lambda: False)
                self.assertEqual(len(joins), 2)
                self.assertEqual(state.setting('listener-wait')['mode'], 'realtime')
                wait.wait(lambda: False)
                self.assertEqual(state.setting('listener-wait')['signals'], 1)
            finally: wait.close()

    def test_foreign_topics_and_events_do_not_wake_the_agent(self):
        from quorum_realtime import RealtimeWait
        clock = Clock()
        socket = Socket(clock)
        with ledger() as state:
            wait = RealtimeWait(api(), {'agent_id': AGENT}, state, connect=lambda *a, **k: socket,
                                clock=clock, sleep=clock.sleep)
            try:
                wait.prepare(lambda: False)
                for topic, event in [('realtime:other-agent', 'inbox_changed'), (TOPIC, 'other-event')]:
                    socket.incoming.append(json.dumps({'topic': topic, 'event': 'broadcast',
                        'payload': {'event': event, 'payload': {}}}))
                wait.wait(lambda: False)
                self.assertEqual(clock.now, 60)
            finally: wait.close()

    def test_stopping_releases_socket_promptly_without_waiting_for_safety_poll(self):
        from quorum_realtime import RealtimeWait
        clock = Clock()
        socket = Socket(clock)
        with ledger() as state:
            wait = RealtimeWait(api(), {'agent_id': AGENT}, state, connect=lambda *a, **k: socket,
                                clock=clock, sleep=clock.sleep)
            wait.prepare(lambda: False)
            wait.wait(lambda: clock.now >= 1)
            wait.close()
            self.assertEqual(clock.now, 1)
            self.assertTrue(socket.closed)

    def test_token_provider_auth_failure_stops_instead_of_falling_back(self):
        from quorum_realtime import RealtimeWait
        from quorum_api import QuorumError
        def missing(): raise QuorumError('auth_required')
        with ledger() as state:
            wait = RealtimeWait(api(missing), {'agent_id': AGENT}, state)
            with self.assertRaisesRegex(QuorumError, '^auth_required$'):
                wait.prepare(lambda: False)

    def test_listener_subscribes_before_initial_drain_and_wakes_without_ten_second_delay(self):
        from quorum_realtime import RealtimeWait
        from quorum_listener import Control, run_listener
        clock = Clock()
        socket = Socket(clock)
        with ledger() as state:
            control = Control(state)
            wait = RealtimeWait(api(), {'agent_id': AGENT}, state, connect=lambda *a, **k: socket,
                                clock=clock, sleep=clock.sleep)
            class Worker:
                calls = 0
                def reconcile(self):
                    if state.setting('listener-wait')['mode'] != 'realtime':
                        raise AssertionError('inbox read before subscribing')
                def run_once(self):
                    self.calls += 1
                    if self.calls == 1:
                        # Message committed after the empty inbox read. Its hint
                        # is already buffered; the listener must not sleep ten seconds.
                        socket.incoming.append(json.dumps({'topic': TOPIC, 'event': 'broadcast',
                            'payload': {'event': 'inbox_changed', 'payload': {}}}))
                        return 'idle'
                    control.request_stop()
                    return 'delivered'
            worker = Worker()
            result = run_listener(state, lambda: worker, 'synthetic-run', waiter=wait)
            self.assertEqual((worker.calls, result['handled'], result['state']), (2, 1, 'stopped'))
            self.assertEqual(clock.now, 0)
            self.assertTrue(socket.closed)
            self.assertEqual(result['wait_mode'], 'stopped')
            self.assertEqual(result['notification_signals'], 1)
            self.assertIsInstance(result['last_signal_at'], float)

    def test_listener_unknown_submission_is_not_retried_and_closes_notification_channel(self):
        from quorum_realtime import RealtimeWait
        from quorum_listener import run_listener
        clock = Clock()
        socket = Socket(clock)
        with ledger() as state:
            wait = RealtimeWait(api(), {'agent_id': AGENT}, state, connect=lambda *a, **k: socket,
                                clock=clock, sleep=clock.sleep)
            class Worker:
                calls = 0
                def reconcile(self): pass
                def run_once(self):
                    self.calls += 1
                    return 'unknown'
            worker = Worker()
            result = run_listener(state, lambda: worker, 'synthetic-run', waiter=wait)
            self.assertEqual((worker.calls, result['state']), (1, 'unknown'))
            self.assertTrue(socket.closed)

    def test_a_lost_hint_is_recovered_from_the_durable_inbox_by_the_safety_scan(self):
        from quorum_realtime import RealtimeWait
        from quorum_listener import Control, run_listener
        from quorum_session import Relay
        clock, delivered = Clock(), []
        socket = Socket(clock)
        with ledger() as state:
            control = Control(state)
            record = {'agent_id': AGENT, 'session_ref': '22222222-2222-4222-8222-222222222222'}
            message = {'id': '33333333-3333-4333-8333-333333333333', 'recipient_id': AGENT,
                       'body': 'durable message; its hint was lost', 'reply_to': None}
            class Api:
                config = api().config
                token = staticmethod(lambda: 'synthetic-owner-jwt')
                def call(self, operation, payload):
                    if operation == 'session_requirements': return {'protocol_version':2, 'skill_version':'0.3.0'}
                    if operation == 'session_inbox':
                        return [message] if clock.now >= 30 and not delivered else []
                    if operation == 'session_ack':
                        control.request_stop()
                        return {'queued': True}
                    raise AssertionError('unexpected RPC ' + operation)
            backend = Api()
            wait = RealtimeWait(backend, record, state, connect=lambda *a, **k: socket,
                                clock=clock, sleep=clock.sleep)
            result = run_listener(state, lambda: Relay(backend, state, record, delivered.append),
                                  'synthetic-run', waiter=wait)
            self.assertEqual(delivered, [message])
            self.assertEqual(clock.now, 60)
            self.assertEqual(result['notification_signals'], 0)
            self.assertEqual(result['last_wake'], 'safety_poll')
            self.assertEqual(state.setting('delivery:' + message['id'])['state'], 'acked')


if __name__ == '__main__': unittest.main()
