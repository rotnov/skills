"""Keepalive has no model turns and reconnect notices have stable retry IDs."""
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'skills/quorum/scripts'))
from quorum_test_support import isolated_profiles


class PresenceTests(unittest.TestCase):
    def setUp(self):
        quiet = patch('quorum_presence.Presence._report_release')
        quiet.start()
        self.addCleanup(quiet.stop)

    @isolated_profiles
    def test_tick_throttles_and_resubscription_within_health_window_keeps_epoch(self):
        from quorum_presence import Presence
        from quorum_state import LocalLedger
        with tempfile.TemporaryDirectory() as root:
            ledger = LocalLedger(Path(root))
            try:
                calls, now = [], [0]
                class Api:
                    def call(self, operation, payload):
                        if operation == 'session_requirements': return {'protocol_version':2, 'skill_version':'0.3.0'}
                        calls.append((operation, payload.copy()))
                        return {'online':True, 'last_seen':'now'}
                health = Presence(Api(), {'agent_id':'a','session_ref':'s'}, ledger, clock=lambda:now[0])
                health.tick(); health.tick()
                self.assertEqual(len(calls), 1)
                now[0] = 20
                health.tick()
                self.assertEqual(calls[0][1]['connection_id'], calls[1][1]['connection_id'])
                # A Realtime resubscription refreshes health immediately, but peers
                # never saw this listener offline, so it is the same Hello epoch.
                now[0] = 25
                health.connected()
                self.assertEqual(len(calls), 3)
                self.assertEqual(calls[1][1]['connection_id'], calls[2][1]['connection_id'])
                self.assertTrue(all(op=='session_heartbeat' for op,_ in calls))
                # A genuinely new listener (stop/start) says Hello even within 60s.
                restarted = Presence(Api(), {'agent_id':'a','session_ref':'s'}, ledger, clock=lambda:now[0])
                now[0] = 30
                restarted.connected()
                self.assertNotEqual(calls[-1][1]['connection_id'], calls[2][1]['connection_id'])
            finally: ledger.db.close()

    @isolated_profiles
    def test_outage_beyond_health_window_starts_exactly_one_new_epoch(self):
        from quorum_api import QuorumError
        from quorum_presence import Presence
        from quorum_state import LocalLedger
        with tempfile.TemporaryDirectory() as root:
            ledger = LocalLedger(Path(root))
            try:
                calls, now, down = [], [0], [False]
                class Api:
                    def call(self, operation, payload):
                        if operation == 'session_requirements': return {'protocol_version':2, 'skill_version':'0.3.0'}
                        calls.append(payload['connection_id'])
                        if down[0]: raise QuorumError('service_unavailable')
                        return {'online':True}
                health = Presence(Api(), {'agent_id':'a','session_ref':'s'}, ledger, clock=lambda:now[0])
                health.tick()
                first = calls[-1]
                down[0] = True
                for second in (20, 40):
                    now[0] = second; health.tick()
                self.assertEqual(set(calls), {first}, 'transient retries must not rotate the epoch')
                for second in (60, 80):
                    now[0] = second; health.tick()
                down[0] = False
                now[0] = 100; health.connected()
                now[0] = 101; health.connected()
                now[0] = 140; health.tick()
                self.assertEqual(len(set(calls)), 2, 'one genuine reconnect after the 60s health window')
                self.assertEqual(calls[-1], calls[-2])
                self.assertNotEqual(calls[-1], first)
            finally: ledger.db.close()

    @isolated_profiles
    def test_system_sleep_beyond_health_window_is_a_genuine_reconnect(self):
        from unittest.mock import patch
        from quorum_presence import Presence
        from quorum_state import LocalLedger
        with tempfile.TemporaryDirectory() as root:
            ledger = LocalLedger(Path(root))
            try:
                calls, wall = [], [1000.0]
                class Api:
                    def call(self, operation, payload):
                        if operation == 'session_requirements': return {'protocol_version':2, 'skill_version':'0.3.0'}
                        calls.append(payload['connection_id'])
                        return {'online':True}
                with patch('quorum_presence.time.time', lambda: wall[0]):
                    # Monotonic time is frozen across macOS sleep; only wall time moves.
                    health = Presence(Api(), {'agent_id':'a','session_ref':'s'}, ledger, clock=lambda: 0)
                    health.tick()
                    wall[0] += 30; health.connected()
                    self.assertEqual(len(set(calls)), 1)
                    wall[0] += 600; health.connected()
                    self.assertEqual(len(set(calls)), 2)
            finally: ledger.db.close()

    @isolated_profiles
    def test_lost_heartbeat_response_retries_same_epoch_and_keeps_receipts(self):
        from quorum_api import QuorumError
        from quorum_presence import Presence
        from quorum_state import LocalLedger
        with tempfile.TemporaryDirectory() as root:
            ledger=LocalLedger(Path(root))
            try:
                calls,now=[],[0]
                class Api:
                    def call(self, operation, payload):
                        if operation == 'session_requirements': return {'protocol_version':2, 'skill_version':'0.3.0'}
                        calls.append(payload.copy())
                        if len(calls)==1: raise QuorumError('service_unavailable')
                        return {'online':True}
                ledger.setting('delivery:m', {'state':'submitted'})
                health=Presence(Api(),{'agent_id':'a','session_ref':'s'},ledger,clock=lambda:now[0])
                health.tick()
                now[0]=20; health.tick()
                self.assertEqual(calls[0]['connection_id'],calls[1]['connection_id'])
                self.assertEqual(ledger.setting('delivery:m'),{'state':'submitted'})
                self.assertEqual(ledger.setting('presence')['state'],'online')
            finally: ledger.db.close()

    @isolated_profiles
    def test_realtime_wakes_and_quick_resubscription_do_not_hello_again(self):
        from quorum_presence import Presence
        from quorum_realtime import RealtimeWait
        from test_quorum_realtime import Clock, Socket, api, AGENT
        from quorum_state import LocalLedger
        import json
        with tempfile.TemporaryDirectory() as root:
            ledger=LocalLedger(Path(root))
            try:
                clock,calls,sockets=Clock(),[],[]
                backend=api()
                backend.call=lambda operation,payload: ({'protocol_version':2, 'skill_version':'0.3.0'}
                    if operation=='session_requirements' else calls.append((operation,payload.copy())) or {'online':True})
                def connect(*args,**kwargs):
                    socket=Socket(clock);sockets.append(socket);return socket
                presence=Presence(backend,{'agent_id':AGENT,'session_ref':'s'},ledger,clock=clock)
                wait=RealtimeWait(backend,{'agent_id':AGENT},ledger,connect=connect,clock=clock,
                                  sleep=clock.sleep,keepalive=presence)
                try:
                    wait.prepare(lambda:False)
                    first=calls[0][1]['connection_id']
                    for _ in range(3):
                        sockets[-1].incoming.append(json.dumps({'topic':wait.topic,'event':'broadcast',
                            'payload':{'event':'inbox_changed'}}))
                        wait.wait(lambda:False)
                    self.assertEqual(len(calls),1,'wake hints must not send Hello/heartbeats')
                    wait.wait(lambda:False)
                    self.assertGreaterEqual(len(calls),3)
                    self.assertTrue(all(payload['connection_id']==first for _,payload in calls))
                    wait._fallback();clock.sleep(10);wait.wait(lambda:False)
                    self.assertEqual(len(sockets),2)
                    self.assertTrue(all(payload['connection_id']==first for _,payload in calls))
                finally: wait.close()
            finally: ledger.db.close()

    @isolated_profiles
    def test_flapping_realtime_with_quiet_heartbeat_keeps_one_hello_epoch(self):
        # Production shape: the channel keeps erroring/rejoining while every
        # session_heartbeat RPC succeeds. Peers never see this agent offline.
        from quorum_presence import Presence
        from quorum_realtime import RealtimeWait
        from test_quorum_realtime import Clock, Socket, api, AGENT
        from quorum_state import LocalLedger
        import json
        with tempfile.TemporaryDirectory() as root:
            ledger=LocalLedger(Path(root))
            try:
                clock,epochs,sockets=Clock(),[],[]
                backend=api()
                backend.call=lambda operation,payload: ({'protocol_version':2, 'skill_version':'0.3.0'}
                    if operation=='session_requirements' else epochs.append(payload['connection_id']) or {'online':True})
                def connect(*args,**kwargs):
                    socket=Socket(clock);sockets.append(socket);return socket
                presence=Presence(backend,{'agent_id':AGENT,'session_ref':'s'},ledger,clock=clock)
                wait=RealtimeWait(backend,{'agent_id':AGENT},ledger,connect=connect,clock=clock,
                                  sleep=clock.sleep,keepalive=presence)
                try:
                    wait.prepare(lambda:False)
                    for _ in range(4):
                        sockets[-1].incoming.append(json.dumps({'topic':wait.topic,'event':'phx_error','payload':{}}))
                        wait.wait(lambda:False)   # drop -> polling fallback (heartbeats continue)
                        wait.wait(lambda:False)   # resubscribe
                    self.assertEqual(len(sockets),5)
                    self.assertEqual(len(set(epochs)),1,'resubscription must not create new Hello epochs')
                finally: wait.close()
            finally: ledger.db.close()

    @isolated_profiles
    def test_busy_delivery_drain_still_heartbeats_without_entering_idle_wait(self):
        from quorum_presence import Presence
        from quorum_realtime import RealtimeWait
        from quorum_listener import Control, run_listener
        from quorum_state import LocalLedger
        from test_quorum_realtime import Clock, api, AGENT
        with tempfile.TemporaryDirectory() as root:
            ledger=LocalLedger(Path(root))
            try:
                clock,calls=Clock(),[]
                backend=api()
                backend.call=lambda op,payload: ({'protocol_version':2, 'skill_version':'0.3.0'}
                    if op=='session_requirements' else calls.append(clock()) or {'online':True})
                presence=Presence(backend,{'agent_id':AGENT,'session_ref':'s'},ledger,clock=clock)
                wait=RealtimeWait(backend,{'agent_id':AGENT},ledger,clock=clock,sleep=clock.sleep,keepalive=presence)
                # Exercise the actual loop and its real pulse, without an idle path.
                wait.prepare=lambda stopping:presence.tick()
                wait.wait=lambda stopping:self.fail('busy drain must not need idle wait')
                class Worker:
                    def reconcile(self): pass
                    def run_once(self):
                        clock.sleep(21)
                        if clock()>=84: Control(ledger).request_stop()
                        return 'delivered'
                result=run_listener(ledger,Worker,'drain',waiter=wait,timeout=0)
                self.assertEqual(result['handled'],4)
                self.assertEqual(calls,[0,21,42,63])
            finally: ledger.db.close()

    @isolated_profiles
    def test_missing_server_extension_fails_with_actionable_code(self):
        from quorum_api import QuorumError
        from quorum_presence import Presence
        from quorum_state import LocalLedger
        with tempfile.TemporaryDirectory() as root:
            ledger=LocalLedger(Path(root))
            try:
                class Api:
                    def call(self,*args): raise QuorumError('service_error')
                health=Presence(Api(),{'agent_id':'a','session_ref':'s'},ledger)
                with self.assertRaisesRegex(QuorumError,'coordination_unavailable'): health.tick()
                self.assertEqual(ledger.setting('presence')['code'],'coordination_unavailable')
            finally: ledger.db.close()


class HelloReportTests(unittest.TestCase):
    def setUp(self):
        quiet = patch('quorum_presence.Presence._report_release')
        quiet.start()
        self.addCleanup(quiet.stop)

    """#88, #196: the connection's presence outcome and roster are persisted once and reported by status."""
    RECORD = {'agent_id':'a', 'session_ref':'s', 'project_ref':'shared'}

    def api(self, responses, calls):
        class Api:
            def call(self, operation, payload):
                if operation == 'session_requirements': return {'protocol_version':2, 'skill_version':'0.3.0'}
                calls.append(payload['connection_id'])
                return {'online':True, **responses(payload['connection_id'], len(calls))}
        return Api()

    @staticmethod
    def hello(peers, roles, available=None):
        return {'peers':peers, 'skipped':0, 'roles':roles, 'available':len(roles) if available is None else available,
                'available_roles':roles, 'roster_message_id':'00000000-0000-4000-8000-000000000001'}

    @isolated_profiles
    def test_new_connection_reports_peers_and_later_heartbeats_do_not_rewrite_it(self):
        from quorum_presence import Presence
        from quorum_state import LocalLedger
        with tempfile.TemporaryDirectory() as root:
            ledger = LocalLedger(Path(root))
            try:
                calls, now, writes = [], [0], []
                hello = self.hello(2, ['alpha', 'zeta'])
                api = self.api(lambda _, n: {'new_connection':n == 1, 'hello':hello}, calls)
                health = Presence(api, self.RECORD, ledger, clock=lambda:now[0])
                original = ledger.setting
                def spy(key, value=None):
                    if key == 'hello' and value is not None: writes.append(value)
                    return original(key, value)
                ledger.setting = spy
                health.tick()
                for second in (20, 40): now[0] = second; health.tick()
                self.assertEqual(len(calls), 3)
                self.assertEqual(len(writes), 1, 'one persisted outcome per connection, not per heartbeat')
                saved = ledger.setting('hello')
                self.assertEqual((saved['sent'], saved['available'], saved['reused'], saved['roster']), (2, 2, False, True))
                self.assertEqual(saved['summary'], 'Presence published in shared: 2 peers (alpha, zeta); roster: 2 available; '
                                                   'the server still sent Hello to 2 peers (presence migration not applied)')
            finally: ledger.db.close()

    def test_zero_and_many_peers_are_explicit_and_bounded(self):
        from quorum_presence import hello_result, hello_summary
        zero = {'connection_id':'c', 'project_ref':'alone', 'reused':False, 'kept':0, **hello_result(self.hello(0, []))}
        self.assertEqual(hello_summary(zero), '0 peers share project reference alone; roster: 0 available')
        roles = ['peer-%02d' % n for n in range(1, 17)]
        many = {'connection_id':'c', 'project_ref':'crowd', 'reused':False, 'kept':0,
                **hello_result({**self.hello(20, roles + ['x'] * 9, 20), 'roles':roles})}
        self.assertEqual(len(many['roles']), 16)
        self.assertTrue(hello_summary(many).startswith('Presence published in crowd: 20 peers (peer-01, '))
        self.assertIn('peer-16, ...); roster: 20 available', hello_summary(many))
        # Malformed or hostile counts are dropped rather than reported.
        self.assertIsNone(hello_result({'peers':'9', 'available':10**6, 'roles':'x'})['sent'])
        self.assertIsNone(hello_result({'peers':True})['sent'])
        legacy = {'connection_id':'c', 'project_ref':'p', 'reused':False, 'kept':0, **hello_result(None)}
        self.assertEqual(hello_summary(legacy), 'Presence published in p; roster not reported')

    def test_sent_and_skipped_are_reported_apart_and_an_old_server_is_tolerated(self):
        from quorum_presence import hello_result, hello_summary
        def report(**hello):
            record = {'connection_id':'c', 'project_ref':'p', 'reused':False, 'kept':0,
                      **hello_result({**self.hello(3, ['a', 'b', 'c']), **hello})}
            return record, hello_summary(record)
        record, summary = report(skipped=2)
        self.assertEqual((record['sent'], record['skipped']), (1, 2))
        self.assertEqual(summary, 'Presence published in p: 3 peers (a, b, c); roster: 3 available; '
                                  'the server still sent Hello to 1 peer (presence migration not applied)')
        record, summary = report(skipped=3)
        self.assertEqual((record['sent'], record['skipped']), (0, 3))
        self.assertEqual(summary, 'Presence published in p: 3 peers (a, b, c); roster: 3 available')
        # An old server, an old row and a nonsensical split all leave the count marked as unsplit.
        for hello in ({'skipped':None}, {'skipped':4}, {'skipped':True}, {'skipped':'1'}):
            with self.subTest(hello=hello):
                record, summary = report(**hello)
                self.assertEqual((record['sent'], record['skipped']), (3, None))
                self.assertTrue(summary.endswith('the server still sent Hello to up to 3 peers '
                                                 '(presence migration not applied)'), summary)
        old = self.hello(1, ['a'])
        del old['skipped']
        self.assertEqual(hello_result(old)['skipped'], None)
        # A record saved before this release has no `skipped` key.
        self.assertTrue(hello_summary({'project_ref':'p', 'sent':2}).startswith('Presence published in p: 2 peers'))

    @isolated_profiles
    def test_reused_tool_inbox_epoch_reports_that_no_new_hello_was_sent(self):
        from quorum_presence import Presence
        from quorum_state import LocalLedger
        with tempfile.TemporaryDirectory() as root:
            ledger = LocalLedger(Path(root))
            try:
                calls, wall = [], [1000.0]
                hello = self.hello(3, ['a', 'b', 'c'])
                fresh = [True]
                def respond(connection, n):
                    new, fresh[0] = fresh[0], False
                    return {'new_connection':new, 'hello':hello}
                api = self.api(respond, calls)
                Presence(api, self.RECORD, ledger, persist_epoch=True, wall=lambda:wall[0]).tick()
                self.assertFalse(ledger.setting('hello')['reused'])
                # The next bounded receive is a new process with the same persisted epoch.
                wall[0] = 1010.0
                Presence(api, self.RECORD, ledger, persist_epoch=True, wall=lambda:wall[0]).tick()
                self.assertEqual(calls[0], calls[1])
                saved = ledger.setting('hello')
                self.assertTrue(saved['reused'])
                self.assertEqual(saved['summary'], 'Reused connection ' + calls[0][:8] + ': no new connection was '
                                 'announced; earlier: Presence published in shared: 3 peers (a, b, c); roster: 3 '
                                 'available; the server still sent Hello to 3 peers (presence migration not applied)')
                # After a real gap the epoch rotates and a new Hello is reported plainly again.
                fresh[0] = True
                wall[0] = 1100.0
                Presence(api, self.RECORD, ledger, persist_epoch=True, wall=lambda:wall[0]).tick()
                self.assertNotEqual(calls[2], calls[0])
                self.assertFalse(ledger.setting('hello')['reused'])
            finally: ledger.db.close()

    @isolated_profiles
    def test_lost_response_retry_is_not_misreported_as_reuse(self):
        from quorum_api import QuorumError
        from quorum_presence import Presence
        from quorum_state import LocalLedger
        with tempfile.TemporaryDirectory() as root:
            ledger = LocalLedger(Path(root))
            try:
                calls, now = [], [0]
                hello = self.hello(1, ['peer'])
                def respond(connection, n):
                    if n == 1: raise QuorumError('service_unavailable')  # Server sent Hello; response lost.
                    return {'new_connection':False, 'hello':hello}
                health = Presence(self.api(respond, calls), self.RECORD, ledger, clock=lambda:now[0])
                health.tick()
                self.assertIsNone(ledger.setting('hello'))
                now[0] = 20; health.tick()
                self.assertEqual(calls[0], calls[1])
                saved = ledger.setting('hello')
                self.assertEqual((saved['reused'], saved['sent']), (False, 1))
                self.assertEqual(saved['summary'], 'Presence published in shared: 1 peer (peer); roster: 1 available; '
                                                   'the server still sent Hello to 1 peer (presence migration not applied)')
            finally: ledger.db.close()

    @isolated_profiles
    def test_realtime_resubscription_that_keeps_the_connection_is_reported(self):
        from quorum_listener import Control
        from quorum_presence import Presence
        from quorum_state import LocalLedger
        with tempfile.TemporaryDirectory() as root:
            ledger = LocalLedger(Path(root))
            try:
                calls, now = [], [0]
                hello = self.hello(0, [])
                health = Presence(self.api(lambda _, n: {'new_connection':n == 1, 'hello':hello}, calls),
                                  self.RECORD, ledger, clock=lambda:now[0])
                health.connected()
                self.assertEqual(ledger.setting('hello')['kept'], 0)
                now[0] = 5; health.connected()
                saved = ledger.setting('hello')
                self.assertEqual((saved['kept'], saved['reused']), (1, False))
                self.assertTrue(saved['summary'].startswith('Reconnect kept connection ' + calls[0][:8]
                                                            + ': no new connection was announced; earlier: 0 peers share'))
                status = Control(ledger).status()
                self.assertEqual(status['hello']['summary'], saved['summary'])
                self.assertEqual(status['hello']['connection'], 'last')  # No listener lock is held here.
            finally: ledger.db.close()


class PresenceNotHelloTests(unittest.TestCase):
    """#196: a connection is announced by one presence heartbeat; the roster is still reported locally."""
    RECORD = {'agent_id':'a', 'session_ref':'s', 'project_ref':'shared'}

    def setUp(self):
        quiet = patch('quorum_presence.Presence._report_release')
        quiet.start()
        self.addCleanup(quiet.stop)

    @isolated_profiles
    def test_connect_and_reconnect_send_only_the_heartbeat_and_keep_the_roster(self):
        from quorum_listener import Control
        from quorum_presence import Presence
        from quorum_state import LocalLedger
        with tempfile.TemporaryDirectory() as root:
            ledger = LocalLedger(Path(root))
            try:
                operations, connections, now = [], [], [0]
                class Api:
                    def call(self, operation, payload):
                        operations.append(operation)
                        if operation == 'session_requirements': return {'protocol_version':2}
                        new = payload['connection_id'] not in connections
                        connections.append(payload['connection_id'])
                        # A migrated server counts every peer as already announced and greets no one.
                        return {'online':True, 'new_connection':new, 'hello':{
                            'peers':3, 'skipped':3, 'roles':['alpha', 'beta', 'zeta'], 'available':2,
                            'available_roles':['alpha', 'zeta'], 'roster_message_id':'00000000-0000-4000-8000-000000000001'}}
                health = Presence(Api(), self.RECORD, ledger, clock=lambda:now[0])
                health.tick()
                for second in (20, 40): now[0] = second; health.tick()
                # A genuine reconnect after the health window is one more presence heartbeat, nothing else.
                now[0] = 200; health.tick()
                self.assertEqual(len(set(connections)), 2)
                self.assertEqual(set(operations) - {'session_requirements'}, {'session_heartbeat'})
                saved = ledger.setting('hello')
                self.assertEqual((saved['sent'], saved['peers'], saved['available'], saved['available_roles'],
                                  saved['roles']), (0, 3, 2, ['alpha', 'zeta'], ['alpha', 'beta', 'zeta']))
                self.assertEqual(saved['summary'], 'Presence published in shared: 3 peers (alpha, beta, zeta); '
                                                   'roster: 2 available')
                self.assertNotIn('Hello', saved['summary'])
                self.assertEqual(Control(ledger).status()['hello']['available_roles'], ['alpha', 'zeta'])
            finally: ledger.db.close()
