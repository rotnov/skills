"""A Grok listener outliving its TUI (#140): real sockets, ledgers and listener runs; no Grok process or model."""
import os
from pathlib import Path
import socket
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from quorum_test_support import isolated_profiles
from test_quorum_session import RequirementsApi, SID, without_client_session

SCRIPTS = Path(__file__).resolve().parents[1] / 'skills/quorum/scripts'
sys.path.insert(0, str(SCRIPTS))
RUN = '33333333-3333-4333-8333-333333333333'


class HeartbeatApi(RequirementsApi):
    def call(self, operation, payload, request_id=None):
        if operation == 'session_heartbeat':
            self.calls.append((operation, payload))
            return {'new_connection': False}
        return super().call(operation, payload, request_id)

    def beats(self):
        return sum(operation == 'session_heartbeat' for operation, _ in self.calls)


@unittest.skipUnless(hasattr(socket, 'AF_UNIX'), 'Unix transport')
class TransportAliveTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='grok-l-', dir='/tmp')
        self.addCleanup(temporary.cleanup)
        self.path = Path(temporary.name) / 'leader.sock'
        self.server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.addCleanup(self.server.close)
        self.server.bind(str(self.path))
        self.server.listen()
        self.transport = {'leader_socket':str(self.path), 'tui_pid':100, 'tui_start':5, 'cwd':'/project'}

    def alive(self, transport=None, births=None):
        from quorum_grok_leader import transport_alive
        table = {100:5} if births is None else births
        def birth(pid):
            if pid not in table: raise OSError('process_unavailable')
            return table[pid]
        with patch('quorum_grok_leader.process_birth', side_effect=birth):
            return transport_alive(transport or self.transport)

    def test_live_tui_and_listening_socket_are_alive(self):
        self.assertTrue(self.alive())

    def test_a_closed_or_reused_tui_process_is_not_alive(self):
        self.assertFalse(self.alive(births={}))
        self.assertFalse(self.alive(births={100:6}))

    def test_a_dead_or_removed_leader_socket_is_not_alive(self):
        self.server.close()
        self.assertTrue(self.path.exists())
        self.assertFalse(self.alive())  # A stale socket file refuses the connection.
        self.path.unlink()
        self.assertFalse(self.alive())

    def test_a_slow_leader_is_alive_because_only_refusal_or_absence_counts(self):
        for error in (TimeoutError, BlockingIOError):
            with self.subTest(error=error.__name__), patch('socket.socket.connect', side_effect=error):
                self.assertTrue(self.alive())

    def test_a_malformed_transport_is_not_alive(self):
        self.assertFalse(self.alive({'cwd':'/project'}))

    def test_windows_transport_checks_the_leader_process_and_opens_no_pipe(self):
        transport = {'leader_socket':'unused', 'leader_pipe':'grok-leader-0', 'leader_pid':200, 'leader_start':7,
                     'tui_pid':100, 'tui_start':5, 'cwd':'/project'}
        with patch('socket.socket', side_effect=AssertionError('no socket on Windows')):
            self.assertTrue(self.alive(transport, births={100:5, 200:7}))
            self.assertFalse(self.alive(transport, births={100:5}))
            self.assertFalse(self.alive(transport, births={100:5, 200:8}))

    @unittest.skipUnless(sys.platform == 'darwin', 'macOS process ABI')
    def test_process_birth_is_the_identity_the_binding_stored(self):
        from quorum_grok_leader import process_birth, process_identity
        self.assertEqual(process_birth(os.getpid()), process_identity(os.getpid())['pid_start'])
        with self.assertRaises(OSError): process_birth(2**31 - 1)


class WatchTests(unittest.TestCase):
    def watch(self, reads):
        from quorum_grok_leader import TransportWatch
        self.now, reads = [0], iter(reads)
        return TransportWatch({}, alive=lambda _: next(reads), clock=lambda: self.now[0])

    def run_reads(self, reads, spacing=20):
        watch = self.watch(reads)
        for _ in reads:
            watch.check()
            self.now[0] += spacing

    def test_a_healthy_listener_survives_a_single_failed_read(self):
        self.run_reads([True, False, True, True])

    def test_failures_must_be_consecutive(self):
        self.run_reads([False, False, True, False, False, True, False, False])

    def test_recovery_restarts_the_window(self):
        watch = self.watch([False, False, True, False, False])
        for _ in range(5):
            watch.check()
            self.now[0] += 20
        self.assertEqual(watch.failures, 2)

    def test_repeated_failures_end_the_listener(self):
        with self.assertRaisesRegex(ValueError, '^grok_tui_gone$'):
            self.run_reads([False, False, False])

    def test_a_burst_of_reads_is_not_a_restart_window(self):
        # A Realtime reconnect resets the tick, so three reads may land within one second.
        self.run_reads([False] * 4, spacing=.1)
        with self.assertRaisesRegex(ValueError, '^grok_tui_gone$'):
            self.run_reads([False, False, False], spacing=15)


class PresenceTests(unittest.TestCase):
    def setUp(self):
        from quorum_state import LocalLedger
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.ledger = LocalLedger(Path(temporary.name))
        self.addCleanup(self.ledger.db.close)
        self.api, self.now = HeartbeatApi(), [0]

    def presence(self, reads):
        from quorum_grok_leader import TransportWatch
        from quorum_presence import Presence
        reads = iter(reads)
        watch = TransportWatch({}, alive=lambda _: next(reads), clock=lambda: self.now[0])
        return Presence(self.api, {'agent_id':'a', 'session_ref':SID}, self.ledger, clock=lambda: self.now[0],
                        run_id=RUN, liveness=watch.check)

    def tick(self, presence, times):
        for _ in range(times):
            presence.tick()
            self.now[0] += 20

    def test_a_single_failed_read_keeps_every_heartbeat(self):
        self.tick(self.presence([True, False, True, True]), 4)
        self.assertEqual(self.api.beats(), 4)
        self.assertEqual(self.ledger.setting('presence')['state'], 'online')

    def test_a_gone_tui_stops_the_heartbeat_at_the_confirming_read(self):
        presence = self.presence([False, False, False, False])
        self.tick(presence, 2)
        self.assertEqual(self.api.beats(), 2)
        with self.assertRaisesRegex(ValueError, '^grok_tui_gone$'): presence.tick()
        self.assertEqual(self.api.beats(), 2)

    def test_realtime_treats_it_as_terminal_instead_of_falling_back(self):
        from quorum_realtime import TERMINAL
        self.assertIn('grok_tui_gone', TERMINAL)

    def test_other_clients_have_no_liveness_read(self):
        from quorum_presence import Presence
        presence = Presence(self.api, {'agent_id':'a', 'session_ref':SID}, self.ledger, clock=lambda: 0)
        presence.tick()
        self.assertEqual(self.api.beats(), 1)


class ListenerRunTests(unittest.TestCase):
    """The real run loop, lock and reception grant around a TUI that closes while idle."""
    def setUp(self):
        from quorum_state import LocalLedger
        env = patch.dict(os.environ, without_client_session(), clear=True)
        env.start()
        self.addCleanup(env.stop)
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.ledger = LocalLedger(self.root / 'sessions' / SID)
        self.addCleanup(self.ledger.db.close)
        self.transport = {'leader_socket':'/synthetic/leader.sock', 'tui_pid':123, 'tui_start':456,
                          'cwd':str(self.root)}
        registration = {'engine':'grok', 'session_ref':SID, 'project_ref':'project', 'cwd':str(self.root)}
        self.record = {**registration, 'registration':registration, 'agent_id':RUN, 'quorum_id':'quorum',
                       'role':'grok-macos-test', 'quorum':'team', 'grok_transport':self.transport}
        self.ledger.setting('session-agent', self.record)
        self.api, self.now = HeartbeatApi(), [0]

    def run_with(self, alive):
        from quorum_grok_leader import TransportWatch
        from quorum_listener import run_listener
        from quorum_presence import Presence
        from quorum_realtime import RealtimeWait
        from quorum_resume import enable
        enable(self.ledger, self.record)
        stop = threading.Event()
        watch = TransportWatch(self.transport, alive=alive, clock=lambda: self.now[0])
        presence = Presence(self.api, self.record, self.ledger, clock=lambda: self.now[0], run_id=RUN,
                            liveness=watch.check)
        class Idle:
            def reconcile(self): pass
            def run_once(self): return 'idle'
        def sleep(_): self.now[0] += 2
        waiter = RealtimeWait(self.api, self.record, self.ledger, keepalive=presence, stop_aware=True,
                              connect=lambda *a, **k: (_ for _ in ()).throw(OSError('offline')),
                              clock=lambda: self.now[0], sleep=sleep)
        waiter.api.token = lambda: 'jwt'
        return run_listener(self.ledger, Idle, RUN, stop_event=stop, timeout=0, waiter=waiter, retry=True), stop

    def test_a_closed_tui_ends_the_run_and_marks_reception_lost(self):
        from quorum_resume import listener_loss, loss_notice
        status, _ = self.run_with(lambda _: False)
        self.assertEqual((status['listener'], status['state'], status['code']), ('offline', 'error', 'grok_tui_gone'))
        self.assertEqual(status['presence']['state'] if 'presence' in status else 'offline', 'offline')
        loss = listener_loss(self.ledger)
        self.assertEqual((loss['run_id'], loss['code']), (RUN, 'grok_tui_gone'))
        step = loss_notice(loss, self.root)['next_step']
        self.assertIn('--resume', step)
        self.assertIn('session recover', step)

    def test_a_transient_failed_read_keeps_the_run_alive(self):
        from quorum_listener import Control
        reads = iter([True, False] + [True] * 100000)
        results = []
        def alive(_):
            if self.api.beats() >= 12: Control(self.ledger).request_stop(RUN)
            return next(reads)
        results.append(self.run_with(alive)[0])
        self.assertEqual(results[0]['state'], 'stopped')
        self.assertGreaterEqual(self.api.beats(), 12)
        self.assertNotIn('code', results[0])


if __name__ == '__main__':
    unittest.main()
