"""Release gates exercise real dispatch/relay loops with an offline RPC boundary."""
import contextlib
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch
from quorum_test_support import isolated_profiles

SCRIPTS = Path(__file__).resolve().parents[1] / 'skills/quorum/scripts'
sys.path.insert(0, str(SCRIPTS))
SID = '11111111-1111-4111-8111-111111111111'
AID = '22222222-2222-4222-8222-222222222222'
MID = '33333333-3333-4333-8333-333333333333'
RELEASE = {'protocol_version': 1, 'skill_version': '0.2.0'}


class Backend:
    def __init__(self, required=None):
        self.required = RELEASE.copy() if required is None else required
        self.calls = []
    def call(self, op, payload, request_id=None):
        self.calls.append((op, payload.copy()))
        if op == 'session_requirements': return self.required
        if op == 'session_inbox': return [{'id':MID, 'recipient_id':AID}]
        if op == 'session_heartbeat': return {'online':True, 'required':self.required}
        if op == 'session_ack': return {'queued':True}
        raise AssertionError('unexpected RPC: ' + op)


class ReleaseTests(unittest.TestCase):
    @isolated_profiles
    def test_offline_version_needs_no_state_profile_or_authentication(self):
        from quorum import main
        with tempfile.TemporaryDirectory() as root, \
                patch('quorum.QuorumApi', side_effect=AssertionError('network')), \
                contextlib.redirect_stdout(io.StringIO()) as output:
            state = Path(root) / 'must-not-create'
            self.assertEqual(main(['--state',str(state),'version']), 0)
            self.assertEqual(json.loads(output.getvalue()), {'ok':True,'code':'ok','data':RELEASE})
            self.assertFalse(state.exists())

    def test_clean_copied_cli_rejects_missing_or_invalid_manifest_without_private_text(self):
        with tempfile.TemporaryDirectory() as root:
            skill = Path(root)/'quorum'
            shutil.copytree(SCRIPTS.parent, skill, ignore=shutil.ignore_patterns('.venv','__pycache__'))
            manifest = skill/'assets/release.json'
            valid = subprocess.run([sys.executable,str(skill/'scripts/quorum.py'),'version'],
                                   capture_output=True,text=True,timeout=20)
            self.assertEqual(valid.returncode,0,valid.stderr)
            self.assertEqual(json.loads(valid.stdout),{'ok':True,'code':'ok','data':RELEASE})
            for body in (None, '{private-secret', '{"protocol_version":true,"skill_version":"0.2.0"}'):
                if body is None: manifest.unlink(missing_ok=True)
                else: manifest.write_text(body)
                result = subprocess.run([sys.executable,str(skill/'scripts/quorum.py'),'version'],
                                        capture_output=True,text=True,timeout=20)
                self.assertEqual(result.returncode, 4)
                self.assertEqual(json.loads(result.stdout),
                                 {'ok':False,'code':'release_manifest_invalid','data':{}})
                self.assertNotIn('private-secret', result.stdout+result.stderr)

    @isolated_profiles
    def test_connect_rejects_missing_foreign_and_executable_requirements_before_registration(self):
        from quorum_session import dispatch
        from quorum_state import LocalLedger
        from quorum_api import QuorumError
        cases = ({}, {'protocol_version':2,'skill_version':'0.2.0'},
                 {'protocol_version':1,'skill_version':'0.2.1'},
                 {'protocol_version':1,'skill_version':'https://secret.invalid/code',
                  'command':'execute private-secret'})
        for required in cases:
            with self.subTest(required=required), tempfile.TemporaryDirectory() as root:
                ledger=LocalLedger(Path(root)); api=Backend(required)
                clean={k:v for k,v in os.environ.items() if k not in
                       ('CODEX_THREAD_ID','CLAUDE_CODE_SESSION_ID','GROK_SESSION_ID')}
                clean['CODEX_THREAD_ID']=SID
                try:
                    args=SimpleNamespace(session_id=SID, action='connect', role='worker', quorum='team',
                                         project_ref='project',accept_tasks_from=None)
                    with patch.dict(os.environ,clean,clear=True), \
                            patch('quorum_live.api_for',return_value=(api,None,None)), \
                            patch('quorum_session.shutil.which',return_value='/synthetic/codex'):
                        with self.assertRaisesRegex(QuorumError,'^skill_update_required$'):
                            dispatch(args,ledger,lambda *a,**kw:self.fail('registered mismatching client'))
                    local=LocalLedger(ledger.root/'sessions'/SID)
                    try:
                        self.assertIsNone(local.setting('session-agent'))
                        diagnostic=dispatch(SimpleNamespace(session_id=SID,action='status'),ledger,None)
                        self.assertTrue(diagnostic['release']['update_required'])
                        self.assertEqual(diagnostic['release']['installed'],RELEASE)
                        self.assertNotIn('private-secret',json.dumps(diagnostic))
                        self.assertEqual([op for op,_ in api.calls],['session_requirements'])
                    finally: local.db.close()
                finally: ledger.db.close()

    @isolated_profiles
    def test_mismatch_rejects_start_and_run_before_enabling_delivery(self):
        from quorum_session import dispatch
        from quorum_state import LocalLedger
        from quorum_api import QuorumError
        clean={k:v for k,v in os.environ.items() if k not in
               ('CODEX_THREAD_ID','CLAUDE_CODE_SESSION_ID','GROK_SESSION_ID')}
        clean['CODEX_THREAD_ID']=SID
        for action in ('start','run'):
            with self.subTest(action=action), tempfile.TemporaryDirectory() as root:
                ledger=LocalLedger(Path(root)); local=LocalLedger(ledger.root/'sessions'/SID)
                local.setting('session-agent',{'engine':'codex','agent_id':AID,'session_ref':SID})
                try:
                    with patch.dict(os.environ,clean,clear=True), \
                            patch('quorum_live.api_for',return_value=(Backend({}),None,None)), \
                            patch('quorum_session.start',side_effect=AssertionError('spawned')), \
                            patch('quorum_session.run_listener',side_effect=AssertionError('received')):
                        with self.assertRaisesRegex(QuorumError,'^skill_update_required$'):
                            dispatch(SimpleNamespace(session_id=SID,action=action,run_id='test'),ledger,None)
                finally: local.db.close(); ledger.db.close()

    @isolated_profiles
    def test_matching_relay_delivers_then_foreign_requirement_preserves_inbox_and_receipts(self):
        from quorum_session import Relay
        from quorum_state import LocalLedger
        from quorum_api import QuorumError
        with tempfile.TemporaryDirectory() as root:
            ledger=LocalLedger(Path(root)); api=Backend(); queued=[]
            try:
                relay=Relay(api,ledger,{'agent_id':AID,'session_ref':SID},lambda m:queued.append(m))
                self.assertEqual(relay.run_once(),'delivered')
                self.assertEqual(len(queued),1)
                api.required={'protocol_version':1,'skill_version':'0.2.1'}
                before=list(api.calls)
                with self.assertRaisesRegex(QuorumError,'^skill_update_required$'): relay.run_once()
                self.assertEqual(api.calls[len(before):],[('session_requirements',{})])
                self.assertEqual(len(queued),1)
                self.assertEqual(ledger.setting('delivery:'+MID),{'state':'acked'})
            finally: ledger.db.close()

    @isolated_profiles
    def test_heartbeat_invalidation_stops_real_listener_before_queue_ack_or_hello(self):
        from quorum_listener import run_listener
        from quorum_presence import Presence
        from quorum_realtime import RealtimeWait
        from quorum_session import Relay
        from quorum_state import LocalLedger
        from test_quorum_realtime import api as realtime_api, Clock
        with tempfile.TemporaryDirectory() as root:
            ledger=LocalLedger(Path(root)); backend=Backend(); clock=Clock(); transport=realtime_api()
            transport.call=backend.call
            try:
                presence=Presence(transport,{'agent_id':AID,'session_ref':SID},ledger,clock=clock)
                presence.tick()
                self.assertEqual(backend.calls[-1][1]['protocol_version'],1)
                self.assertEqual(backend.calls[-1][1]['skill_version'],'0.2.0')
                backend.required={'protocol_version':3,'skill_version':'0.2.0'}
                clock.sleep(20); backend.calls.clear()
                waiter=RealtimeWait(transport,{'agent_id':AID},ledger,clock=clock,sleep=clock.sleep,keepalive=presence)
                waiter.prepare=lambda stopping:None
                result=run_listener(ledger,lambda:Relay(backend,ledger,{'agent_id':AID,'session_ref':SID},
                                    lambda _:self.fail('queued mismatch')), 'test', waiter=waiter,timeout=0)
                self.assertEqual(result['code'],'skill_update_required')
                self.assertTrue(ledger.setting('release')['update_required'])
                self.assertFalse(any(op in ('session_inbox','session_ack','session_heartbeat') for op,_ in backend.calls))
            finally: ledger.db.close()

    @isolated_profiles
    def test_realtime_reconnect_must_not_swallow_release_mismatch_into_polling(self):
        from quorum_presence import Presence
        from quorum_realtime import RealtimeWait
        from quorum_state import LocalLedger
        from quorum_api import QuorumError
        from test_quorum_realtime import api as realtime_api, Clock, Socket
        with tempfile.TemporaryDirectory() as root:
            ledger=LocalLedger(Path(root)); clock=Clock(); backend=realtime_api(); backend.call=Backend({}).call
            try:
                presence=Presence(backend,{'agent_id':AID,'session_ref':SID},ledger,clock=clock)
                waiter=RealtimeWait(backend,{'agent_id':AID},ledger,clock=clock,sleep=clock.sleep,
                                    keepalive=presence,connect=lambda *a,**kw:Socket(clock))
                with self.assertRaisesRegex(QuorumError,'^skill_update_required$'):
                    waiter.prepare(lambda:False)
                self.assertIsNone(waiter.socket)
            finally: ledger.db.close()


class RunningReleaseTests(unittest.TestCase):
    @isolated_profiles
    def test_low_level_start_cannot_reuse_a_stale_running_listener(self):
        from quorum_session import start
        from quorum_state import LocalLedger
        from quorum_listener import Control
        from quorum_api import QuorumError
        with tempfile.TemporaryDirectory() as root:
            ledger=LocalLedger(Path(root))
            try:
                ledger.setting('session-agent',{'agent_id':AID,'session_ref':SID})
                ledger.setting('listener-release',{'protocol_version':1,'skill_version':'0.1.0'})
                Control(ledger).begin('old-run',0)
                with patch('quorum_listener.locked',return_value=True), \
                        patch('quorum_session.locked',return_value=True), \
                        patch('quorum_session.subprocess.Popen',side_effect=AssertionError('spawned')):
                    with self.assertRaisesRegex(QuorumError,'^skill_update_required$'):
                        start(Path(root),ledger,SID,api=Backend())
                self.assertTrue(Control(ledger).stopping('old-run'))
                self.assertEqual(ledger.setting('release')['listener'],
                                 {'protocol_version':1,'skill_version':'0.1.0'})
            finally: ledger.db.close()

    @isolated_profiles
    def test_requirement_change_on_a_later_heartbeat_is_fatal_without_new_hello(self):
        from quorum_presence import Presence
        from quorum_api import QuorumError
        from quorum_state import LocalLedger
        with tempfile.TemporaryDirectory() as root:
            ledger=LocalLedger(Path(root)); backend=Backend(); now=[0]
            try:
                presence=Presence(backend,{'agent_id':AID,'session_ref':SID},ledger,clock=lambda:now[0])
                presence.tick()
                ledger.setting('delivery:'+MID,{'state':'submitted'})
                backend.required={'protocol_version':1,'skill_version':'0.3.0'}; now[0]=20
                before=len(backend.calls)
                with self.assertRaisesRegex(QuorumError,'^skill_update_required$'): presence.tick()
                self.assertEqual(backend.calls[before:],[('session_requirements',{})])
                self.assertEqual(ledger.setting('delivery:'+MID),{'state':'submitted'})
                self.assertEqual(ledger.setting('release')['required'],backend.required)
            finally: ledger.db.close()

    @isolated_profiles
    def test_server_atomic_heartbeat_rejection_is_not_reported_online(self):
        from quorum_presence import Presence
        from quorum_api import QuorumError
        from quorum_state import LocalLedger
        class ChangedOnHeartbeat(Backend):
            def call(self,op,payload,request_id=None):
                if op=='session_heartbeat':
                    return {'code':'skill_update_required',
                            'required':{'protocol_version':2,'skill_version':'0.2.0'}, 'secret':'private-secret'}
                return super().call(op,payload,request_id)
        with tempfile.TemporaryDirectory() as root:
            ledger=LocalLedger(Path(root))
            try:
                with self.assertRaisesRegex(QuorumError,'^skill_update_required$'):
                    Presence(ChangedOnHeartbeat(),{'agent_id':AID,'session_ref':SID},ledger).tick()
                self.assertIsNone(ledger.setting('presence'))
                self.assertEqual(ledger.setting('release')['required'],
                                 {'protocol_version':2,'skill_version':'0.2.0'})
                self.assertNotIn('private-secret',json.dumps(ledger.setting('release')))
            finally: ledger.db.close()

    def test_process_release_snapshot_cannot_be_upgraded_by_replacing_manifest(self):
        with tempfile.TemporaryDirectory() as root:
            skill=Path(root)/'quorum'
            shutil.copytree(SCRIPTS.parent,skill,ignore=shutil.ignore_patterns('.venv','__pycache__'))
            script=("import json,sys; from pathlib import Path; sys.path.insert(0,sys.argv[1]); "
                    "from quorum_release import installed_release; before=installed_release().copy(); "
                    "Path(sys.argv[2]).write_text('{\"protocol_version\":2,\"skill_version\":\"0.3.0\"}'); "
                    "print(json.dumps([before,installed_release()]))")
            result=subprocess.run([sys.executable,'-c',script,str(skill/'scripts'),str(skill/'assets/release.json')],
                                  capture_output=True,text=True,timeout=20)
            self.assertEqual(result.returncode,0,result.stderr)
            self.assertEqual(json.loads(result.stdout),[RELEASE,RELEASE])

    @isolated_profiles
    def test_requirement_flipping_after_inbox_read_prevents_ui_queue(self):
        from quorum_session import Relay
        from quorum_state import LocalLedger
        from quorum_api import QuorumError
        class ChangedAtInbox(Backend):
            def call(self,op,payload,request_id=None):
                result=super().call(op,payload,request_id)
                if op=='session_inbox': self.required={'protocol_version':1,'skill_version':'0.2.1'}
                return result
        with tempfile.TemporaryDirectory() as root:
            ledger=LocalLedger(Path(root)); backend=ChangedAtInbox()
            try:
                with self.assertRaisesRegex(QuorumError,'^skill_update_required$'):
                    Relay(backend,ledger,{'agent_id':AID,'session_ref':SID},
                          lambda _:self.fail('queued mismatching inbox')).run_once()
                self.assertIsNone(ledger.setting('delivery:'+MID))
                self.assertNotIn('session_ack',[op for op,_ in backend.calls])
            finally: ledger.db.close()

    @isolated_profiles
    def test_requirement_flipping_during_ui_queue_preserves_queue_receipt_without_ack(self):
        from quorum_session import Relay
        from quorum_state import LocalLedger
        from quorum_api import QuorumError
        with tempfile.TemporaryDirectory() as root:
            ledger=LocalLedger(Path(root)); backend=Backend(); queued=[]
            try:
                def queue(message):
                    queued.append(message)
                    backend.required={'protocol_version':1,'skill_version':'0.2.1'}
                with self.assertRaisesRegex(QuorumError,'^skill_update_required$'):
                    Relay(backend,ledger,{'agent_id':AID,'session_ref':SID},queue).run_once()
                self.assertEqual(len(queued),1)
                self.assertEqual(ledger.setting('delivery:'+MID),{'state':'queued'})
                self.assertNotIn('session_ack',[op for op,_ in backend.calls])
            finally: ledger.db.close()

    @isolated_profiles
    def test_a_prior_runs_matching_version_marker_cannot_bless_a_legacy_listener(self):
        from quorum_release import ReleaseGuard
        from quorum_listener import Control
        from quorum_api import QuorumError
        from quorum_state import LocalLedger
        with tempfile.TemporaryDirectory() as root:
            ledger=LocalLedger(Path(root))
            try:
                ledger.setting('listener-release',{**RELEASE,'run_id':'old-modern-run'})
                Control(ledger).begin('new-legacy-run',0)
                guard=ReleaseGuard(Backend(),ledger)
                guard.check()
                with patch('quorum_listener.locked',return_value=True):
                    with self.assertRaisesRegex(QuorumError,'^skill_update_required$'): guard.running()
                self.assertTrue(Control(ledger).stopping('new-legacy-run'))
            finally: ledger.db.close()

    @isolated_profiles
    def test_chat_receipt_commands_cannot_bypass_a_mismatching_listener(self):
        from quorum_session import dispatch
        from quorum_state import LocalLedger
        from quorum_api import QuorumError
        clean={k:v for k,v in os.environ.items() if k not in
               ('CODEX_THREAD_ID','CLAUDE_CODE_SESSION_ID','GROK_SESSION_ID')}
        clean['CLAUDE_CODE_SESSION_ID']=SID
        for action in ('received','reconcile','reply'):
            with self.subTest(action=action), tempfile.TemporaryDirectory() as root:
                auth=LocalLedger(Path(root)); local=LocalLedger(auth.root/'sessions'/SID); backend=Backend({})
                try:
                    local.setting('session-agent',{'agent_id':AID,'session_ref':SID,'engine':'claude'})
                    local.setting('delivery:'+MID,{'state':'submitted'})
                    with patch.dict(os.environ,clean,clear=True), \
                            patch('quorum_live.api_for',return_value=(backend,None,None)), \
                            patch('quorum_session.sys.stdin',SimpleNamespace(buffer=io.BytesIO(b'reply'))):
                        with self.assertRaisesRegex(QuorumError,'^skill_update_required$'):
                            dispatch(SimpleNamespace(session_id=SID,action=action,message_id=MID),auth,None)
                    self.assertEqual(local.setting('delivery:'+MID),{'state':'submitted'})
                    self.assertEqual([op for op,_ in backend.calls],['session_requirements'])
                finally: local.db.close(); auth.db.close()
