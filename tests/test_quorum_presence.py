"""Keepalive has no model turns and reconnect notices have stable retry IDs."""
from pathlib import Path
import sys
import tempfile
import unittest
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'skills/quorum/scripts'))
from quorum_test_support import isolated_profiles


class PresenceTests(unittest.TestCase):
    @isolated_profiles
    def test_tick_throttles_but_real_reconnect_changes_connection_id(self):
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
                health.connected()
                self.assertNotEqual(calls[1][1]['connection_id'], calls[2][1]['connection_id'])
                self.assertTrue(all(op=='session_heartbeat' for op,_ in calls))
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
    def test_realtime_wakes_do_not_hello_again_but_resubscription_does(self):
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
                    self.assertNotEqual(calls[-1][1]['connection_id'],first)
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
