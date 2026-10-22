"""Alternate installed-release entrypoints cannot register or execute around gates."""
import contextlib
import io
import json
import os
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch
from quorum_test_support import isolated_profiles
from test_quorum_release import Backend, RELEASE

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'skills/quorum/scripts'))


class AlternateReleaseTests(unittest.TestCase):
    @isolated_profiles
    def test_shared_registration_checks_before_any_registration_and_preserves_other_listener(self):
        from quorum import register_agent
        from quorum_api import QuorumError
        from quorum_state import LocalLedger
        from quorum_listener import Control
        with tempfile.TemporaryDirectory() as root:
            ledger=LocalLedger(Path(root)); backend=Backend({})
            try:
                Control(ledger).begin('unrelated-listener',60)
                data=dict(engine='claude',project_ref='project',session_ref='pilot',cwd=root)
                with self.assertRaisesRegex(QuorumError,'^skill_update_required$'):
                    register_agent(backend,ledger,'team','worker',data)
                self.assertEqual([op for op,_ in backend.calls],['session_requirements'])
                self.assertFalse(Control(ledger).stopping('unrelated-listener'))
                self.assertIsNone(ledger.setting('device'))
            finally: ledger.db.close()

    @isolated_profiles
    def test_top_level_join_reports_safe_release_diagnostics_without_registering(self):
        import quorum
        backend=Backend({'protocol_version':1,'skill_version':'0.3.0','secret':'private-secret'})
        with tempfile.TemporaryDirectory() as root, \
                patch('quorum_profiles.resolve_profile',return_value=(None,None)), \
                patch('quorum.token_provider',return_value=lambda:'not-used'), \
                patch('quorum.QuorumApi',return_value=backend), \
                patch('quorum.sys.stdin',SimpleNamespace(buffer=io.BytesIO(json.dumps(dict(
                    engine='claude',project_ref='project',session_ref='main',cwd=root)).encode()))), \
                contextlib.redirect_stdout(io.StringIO()) as output:
            result=quorum.main(['--state',root,'join','team','--role','worker','--json-stdin'])
            self.assertEqual(result,4)
            response=json.loads(output.getvalue())
            self.assertEqual(response['code'],'skill_update_required')
            self.assertEqual(response['data']['release']['required'],
                             {'protocol_version':1,'skill_version':'0.3.0'})
            self.assertNotIn('private-secret',output.getvalue())
            self.assertEqual([op for op,_ in backend.calls],['session_requirements'])

    @isolated_profiles
    def test_legacy_connect_and_pilot_init_gate_before_membership_or_registration(self):
        from quorum_live import dispatch
        from quorum_api import QuorumError
        from quorum_state import LocalLedger
        for command in ('connect','pilot-init'):
            with self.subTest(command=command), tempfile.TemporaryDirectory() as root:
                ledger=LocalLedger(Path(root)); backend=Backend({})
                try:
                    args=SimpleNamespace(command=command,invite_file=Path(root)/'unused',quorum='team',
                                         role='worker',session_ref='pilot')
                    invitation={'origin':'https://example.com','publishable_key':'public','token':'x'*64,
                                'quorum':'team','quorum_id':'q'}
                    with patch('quorum_live.read_invitation',return_value=invitation), \
                            patch('quorum_live.api_for',return_value=(backend,None,None)), \
                            patch('quorum_profiles.ensure_login',return_value=None):
                        with self.assertRaisesRegex(QuorumError,'^skill_update_required$'):
                            dispatch(args,ledger,lambda *a,**kw:self.fail('registered mismatch'))
                    self.assertEqual([op for op,_ in backend.calls],['session_requirements'])
                    self.assertFalse((Path(root)/'scratch').exists())
                finally: ledger.db.close()

    @isolated_profiles
    def test_listen_start_checks_both_new_and_already_running_paths(self):
        from quorum_listener import start_listener, Control
        from quorum_state import LocalLedger
        from quorum_api import QuorumError
        for locked in (False,True):
            with self.subTest(locked=locked), tempfile.TemporaryDirectory() as root:
                ledger=LocalLedger(Path(root)); backend=Backend({})
                try:
                    Control(ledger).begin('bound-listener',60)
                    with patch('quorum_live.api_for',return_value=(backend,None,None)), \
                            patch('quorum_listener.locked',return_value=locked), \
                            patch('quorum_listener.subprocess.Popen',side_effect=AssertionError('spawned')):
                        with self.assertRaisesRegex(QuorumError,'^skill_update_required$'):
                            start_listener(ledger,Path(root),60,True)
                    self.assertEqual([op for op,_ in backend.calls],['session_requirements'])
                    self.assertTrue(Control(ledger).stopping('bound-listener'))
                finally: ledger.db.close()

    @isolated_profiles
    def test_listen_start_cannot_bless_a_stale_running_release(self):
        from quorum_listener import start_listener, Control
        from quorum_state import LocalLedger
        from quorum_api import QuorumError
        with tempfile.TemporaryDirectory() as root:
            ledger=LocalLedger(Path(root)); backend=Backend()
            try:
                Control(ledger).begin('active-run',60)
                ledger.setting('listener-release',{'run_id':'active-run','protocol_version':1,'skill_version':'0.1.0'})
                with patch('quorum_live.api_for',return_value=(backend,None,None)), \
                        patch('quorum_listener.locked',return_value=True):
                    with self.assertRaisesRegex(QuorumError,'^skill_update_required$'):
                        start_listener(ledger,Path(root),60,True)
                self.assertTrue(Control(ledger).stopping('active-run'))
            finally: ledger.db.close()

    @isolated_profiles
    def test_listen_run_and_pilot_worker_gate_before_preparation_or_consumption(self):
        from quorum_listener import dispatch as listen
        from quorum_live import dispatch, prepare_worker
        from quorum_state import LocalLedger
        from quorum_api import QuorumError
        for action in ('listen-run','pilot-worker','prepare-worker'):
            with self.subTest(action=action), tempfile.TemporaryDirectory() as root:
                ledger=LocalLedger(Path(root)); backend=Backend({})
                try:
                    ledger.setting('pilot-agent',{'agent_id':'a','quorum_id':'q'})
                    args=SimpleNamespace(command='pilot-worker',action='run',toolchain=Path(root),timeout=60,
                                         approve_subscription=True,wait_seconds=1,run_id='run')
                    with patch('quorum_live.api_for',return_value=(backend,None,None)), \
                            patch('quorum_listener.run_listener',side_effect=AssertionError('consumed')), \
                            patch('quorum_acp.ClaudePilotRunner',side_effect=AssertionError('constructed runner')):
                        with self.assertRaisesRegex(QuorumError,'^skill_update_required$'):
                            if action=='listen-run': listen(args,ledger)
                            elif action=='pilot-worker': dispatch(args,ledger,None)
                            else: prepare_worker(ledger,backend,Path(root),60,True)
                    self.assertEqual([op for op,_ in backend.calls],['session_requirements'])
                finally: ledger.db.close()

    @isolated_profiles
    def test_direct_worker_blocks_claim_saved_reply_and_reconciliation_on_mismatch(self):
        from quorum_worker import Worker
        from quorum_acp import ExecutionPolicy
        from quorum_api import QuorumError
        from quorum_state import LocalLedger
        for mode in ('claim','saved-reply','reconcile'):
            with self.subTest(mode=mode), tempfile.TemporaryDirectory() as root:
                ledger=LocalLedger(Path(root)); backend=Backend({})
                try:
                    if mode=='saved-reply': ledger.set_attempt('attempt',1,'reply_ready','saved-answer')
                    worker=Worker(backend,ledger,SimpleNamespace(start=lambda *a:self.fail('model')),
                                  'd',ExecutionPolicy(1,60,'subscription',True,True))
                    with self.assertRaisesRegex(QuorumError,'^skill_update_required$'):
                        worker.reconcile() if mode=='reconcile' else worker.run_once()
                    self.assertEqual([op for op,_ in backend.calls],['session_requirements'])
                    if mode=='saved-reply': self.assertEqual(ledger.attempt('attempt')['state'],'reply_ready')
                finally: ledger.db.close()

    @isolated_profiles
    def test_worker_rechecks_before_launch_and_completion_and_during_model_heartbeat(self):
        from quorum_worker import Worker
        from quorum_acp import ExecutionPolicy
        from quorum_api import QuorumError
        from quorum_state import LocalLedger, SessionBinding
        for edge in ('context','heartbeat','completion'):
            with self.subTest(edge=edge), tempfile.TemporaryDirectory() as root:
                ledger=LocalLedger(Path(root)); starts=[]; cancelled=[]; calls=[]
                class Api:
                    required=RELEASE.copy()
                    def call(self,op,data,request_id=None):
                        calls.append(op)
                        if op=='session_requirements': return self.required
                        if op=='inbox_claim': return {'attempt_id':'attempt','generation':1,
                            'delivery':{'conversation_id':'c','body':'hello'}}
                        if op=='context_read':
                            if edge=='context': self.required={}
                            return []
                        if op=='delivery_complete': raise AssertionError('completed mismatching release')
                        return {'state':'running'}
                backend=Api()
                class Runner:
                    def start(self,*args): starts.append('started');return 'synthetic-handle'
                    def events(self,handle):
                        yield {'type':'started'}
                        backend.required={}
                        if edge=='heartbeat': yield {'type':'heartbeat'}
                        else: yield {'type':'completed','success':True,'text':'answer'}
                    def cancel(self,handle): cancelled.append(handle)
                try:
                    ledger.bind_session(SessionBinding('q','p','d','a','c','claude',root,'main'))
                    worker=Worker(backend,ledger,Runner(),'d',ExecutionPolicy(1,60,'subscription',True,True))
                    with self.assertRaisesRegex(QuorumError,'^skill_update_required$'): worker.run_once()
                    self.assertNotIn('delivery_complete',calls)
                    if edge=='context':
                        self.assertEqual(starts,[])
                        self.assertIsNone(ledger.attempt('attempt'))
                    elif edge=='heartbeat':
                        self.assertEqual(cancelled,['synthetic-handle'])
                        self.assertEqual(ledger.attempt('attempt')['state'],'unknown')
                    else: self.assertEqual(ledger.attempt('attempt')['state'],'reply_ready')
                finally: ledger.db.close()

    @isolated_profiles
    def test_matching_listen_run_keeps_the_supported_route_and_exact_run_marker(self):
        from quorum_listener import dispatch, Control
        from quorum_state import LocalLedger
        with tempfile.TemporaryDirectory() as root:
            ledger=LocalLedger(Path(root)); backend=Backend()
            class Idle:
                def reconcile(self): pass
                def run_once(self): Control(ledger).request_stop();return 'idle'
            try:
                args=SimpleNamespace(action='run',toolchain=Path(root),timeout=60,
                                     approve_subscription=True,run_id='matching-run')
                with patch('quorum_live.api_for',return_value=(backend,None,None)), \
                        patch('quorum_live.prepare_worker',return_value=Idle()), patch('signal.signal'):
                    result=dispatch(args,ledger)
                self.assertEqual((result['listener'],result['state']),('offline','stopped'))
                self.assertEqual(ledger.setting('listener-release'),{**RELEASE,'run_id':'matching-run'})
                self.assertEqual(result['release']['required'],RELEASE)
                self.assertFalse(result['release']['update_required'])
                self.assertEqual([op for op,_ in backend.calls],['session_requirements'])
            finally: ledger.db.close()

    @isolated_profiles
    def test_native_child_startup_mismatch_keeps_typed_failure_diagnostics(self):
        from quorum_session import start
        from quorum_listener import Control
        from quorum_state import LocalLedger
        from quorum_api import QuorumError
        with tempfile.TemporaryDirectory() as root:
            ledger=LocalLedger(Path(root)); backend=Backend()
            failure={'installed':RELEASE,'required':{'protocol_version':1,'skill_version':'0.3.0'},
                     'update_required':True,'code':'skill_update_required'}
            def spawn(command,**kwargs):
                run_id=command[command.index('--run-id')+1]
                ledger.setting('release',failure)
                Control(ledger).update(run_id,'error',0,timeout=0,code='skill_update_required')
                return SimpleNamespace(poll=lambda:0)
            try:
                ledger.setting('session-agent',{'session_ref':'session'})
                with patch('quorum_session.subprocess.Popen',side_effect=spawn):
                    with self.assertRaisesRegex(QuorumError,'^skill_update_required$') as raised:
                        start(Path(root),ledger,'session',api=backend)
                self.assertEqual(getattr(raised.exception,'diagnostic',None),failure)
            finally: ledger.db.close()
