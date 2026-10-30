"""Portable project-knowledge client contracts; no service or provider calls."""
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

SCRIPTS = Path(__file__).resolve().parents[1] / 'skills/quorum/scripts'
sys.path.insert(0, str(SCRIPTS))
from quorum_state import LocalLedger

SID = '11111111-1111-4111-8111-111111111111'
SPACE = '22222222-2222-4222-8222-222222222222'
RECORD = {'agent_id':'33333333-3333-4333-8333-333333333333', 'session_ref':SID,
          'quorum_id':'44444444-4444-4444-8444-444444444444', 'project_ref':'same',
          'engine':'codex', 'cwd':'/tmp/project'}
ENTRY = {'entry_id':'55555555-5555-4555-8555-555555555555', 'type':'fact',
         'title':'ERR_59', 'body':'Synthetic result', 'source_refs':['issue:59'], 'versions':[]}


class KnowledgeTests(unittest.TestCase):
    def test_owner_operations_require_explicit_current_assertion_before_rpc(self):
        from quorum_knowledge import invoke, KnowledgeError
        calls=[]
        class Api:
            def call(self, *args): calls.append(args); return {}
        for action,payload in [('admin',{'operation':'create','payload':{'name':'same'}}),
                               ('write',{'operation':'verify','space_id':SPACE,
                                         'payload':{'entry_id':ENTRY['entry_id'],'expected_revision':1,
                                                    'evidence':'issue:59'}})]:
            with self.subTest(action=action), self.assertRaises(KnowledgeError):
                invoke(Api(), RECORD, action, payload, request_id='r', owner_action=False)
        self.assertEqual(calls, [])
        invoke(Api(), RECORD, 'admin', {'operation':'create','payload':{'name':'same'}},
               request_id='r', owner_action=True)
        self.assertEqual(calls[0][0], 'knowledge_admin')
        self.assertEqual(calls[0][1]['agent_id'], RECORD['agent_id'])
        self.assertEqual(calls[0][1]['session_ref'], SID)

    def test_untrusted_payload_cannot_override_binding_or_trigger_provider(self):
        from quorum_knowledge import invoke, KnowledgeError
        class Api:
            def call(self,*args): raise AssertionError('must refuse locally')
        for value in ({'agent_id':'other'}, {'operation':'propose','payload':{**ENTRY,'verified':True}},
                      {'operation':'embed','provider':'https://secret.invalid'}):
            with self.subTest(value=value), self.assertRaises(KnowledgeError):
                invoke(Api(), RECORD, 'write', value, request_id='r')

    def test_capture_is_finite_exact_scoped_and_proposals_only(self):
        from quorum_knowledge import capture_grant, capture, KnowledgeError
        calls=[]
        class Api:
            def call(self,*args): calls.append(args); return {'revision':1}
        with tempfile.TemporaryDirectory() as root:
            ledger=LocalLedger(Path(root),clock=lambda:100)
            ledger.setting('session-agent',RECORD)
            for owner in (False,):
                with self.assertRaises(KnowledgeError):
                    capture_grant(ledger,RECORD,SPACE,ttl=60,owner_action=owner)
            capture_grant(ledger,RECORD,SPACE,ttl=60,owner_action=True)
            for changed in ({**RECORD,'agent_id':SPACE}, {**RECORD,'cwd':'/other'},
                            {**RECORD,'quorum_id':SPACE}, {**RECORD,'session_ref':SPACE},
                            {**RECORD,'project_ref':'other'}):
                with self.assertRaises(KnowledgeError):
                    capture(Api(),ledger,changed,SPACE,ENTRY,'r',material_result=True)
            with self.assertRaises(KnowledgeError):
                capture(Api(),ledger,RECORD,SPACE,ENTRY,'r',material_result=False)
            with self.assertRaises(KnowledgeError):
                capture(Api(),ledger,RECORD,SPACE,{**ENTRY,'status':'verified'},'r',material_result=True)
            capture(Api(),ledger,RECORD,SPACE,ENTRY,'r',material_result=True)
            self.assertEqual(calls[-1][0],'knowledge_write')
            self.assertEqual(calls[-1][1]['operation'],'propose')
            ledger.clock=lambda:161
            with self.assertRaises(KnowledgeError):
                capture(Api(),ledger,RECORD,SPACE,ENTRY,'r2',material_result=True)
            ledger.clock=lambda:100
            capture_grant(ledger,RECORD,SPACE,owner_action=True,revoke=True)
            with self.assertRaises(KnowledgeError):
                capture(Api(),ledger,RECORD,SPACE,ENTRY,'r2',material_result=True)
            ledger.db.close()

    def test_nonfinite_or_incompatible_vector_refused_before_rpc(self):
        from quorum_knowledge import invoke, KnowledgeError
        class Api:
            def call(self,*args): raise AssertionError('must refuse locally')
        for vector in ([float('nan'),0], [float('inf'),0], [10**400,0], [True,0], [0], '1,2'):
            with self.subTest(vector=vector), self.assertRaises(KnowledgeError):
                invoke(Api(),RECORD,'index',{'space_id':SPACE,'entry_id':ENTRY['entry_id'],
                    'revision':1,'content_sha256':'a'*64,'model':'synthetic-v1','dimensions':2,
                    'vector':vector},request_id='index')

    def test_capture_rechecks_scope_after_token_resolution(self):
        from quorum_knowledge import capture_grant, capture, KnowledgeError
        from quorum_api import QuorumApi, ServiceConfig
        with tempfile.TemporaryDirectory() as root:
            ledger=LocalLedger(Path(root),clock=lambda:100)
            ledger.setting('session-agent',RECORD)
            capture_grant(ledger,RECORD,SPACE,ttl=60,owner_action=True)
            def token():
                capture_grant(ledger,RECORD,SPACE,owner_action=True,revoke=True)
                return 'synthetic-token'
            def transport(*args): raise AssertionError('revoked capture must not reach transport')
            api=QuorumApi(ServiceConfig('https://synthetic.invalid','public'),token,request=transport)
            with self.assertRaises(KnowledgeError):
                capture(api,ledger,RECORD,SPACE,ENTRY,'r',material_result=True)
            ledger.db.close()

    def test_capture_rejects_truthy_nonboolean_grants(self):
        from quorum_knowledge import capture_grant, KnowledgeError
        with tempfile.TemporaryDirectory() as root:
            ledger=LocalLedger(Path(root),clock=lambda:100)
            ledger.setting('session-agent',RECORD)
            with self.assertRaises(KnowledgeError):
                capture_grant(ledger,RECORD,SPACE,owner_action=True,revoke='false')
            ledger.db.close()

    def test_capture_detects_binding_change_during_token_resolution(self):
        from quorum_knowledge import capture_grant, capture, KnowledgeError
        from quorum_api import QuorumApi, ServiceConfig
        with tempfile.TemporaryDirectory() as root:
            ledger=LocalLedger(Path(root),clock=lambda:100)
            ledger.setting('session-agent',RECORD)
            capture_grant(ledger,RECORD,SPACE,ttl=60,owner_action=True)
            def token():
                ledger.setting('session-agent',{**RECORD,'agent_id':SPACE})
                return 'synthetic-token'
            def transport(*args): raise AssertionError('changed binding must not reach transport')
            api=QuorumApi(ServiceConfig('https://synthetic.invalid','public'),token,request=transport)
            with self.assertRaises(KnowledgeError):
                capture(api,ledger,RECORD,SPACE,ENTRY,'r',material_result=True)
            ledger.db.close()

    def test_capture_full_binding_changes_end_grant_at_token_boundary(self):
        from quorum_knowledge import capture_grant, capture, KnowledgeError
        from quorum_api import QuorumApi, ServiceConfig
        replacements=({'transport':'tool-inbox'},{'worker_binding':{'parent_thread_id':SPACE}},
                      {'messaging_socket':'new'},{'grok_transport':{'leader_socket':'new'}},
                      {'registration':{'cwd':'/changed'}},{'device_id':SPACE})
        for replacement in replacements:
            with self.subTest(replacement=replacement), tempfile.TemporaryDirectory() as root:
                ledger=LocalLedger(Path(root),clock=lambda:100)
                ledger.setting('session-agent',RECORD)
                capture_grant(ledger,RECORD,SPACE,ttl=60,owner_action=True)
                def token():
                    ledger.setting('session-agent',{**RECORD,**replacement})
                    return 'synthetic-token'
                def transport(*args): raise AssertionError('changed full binding reached HTTP')
                api=QuorumApi(ServiceConfig('https://synthetic.invalid','public'),token,request=transport)
                with self.assertRaises(KnowledgeError):
                    capture(api,ledger,RECORD,SPACE,ENTRY,'r',material_result=True)
                ledger.db.close()

    def test_deep_direct_payload_serialization_is_a_fixed_local_error(self):
        from quorum_knowledge import invoke,KnowledgeError
        class Api:
            def call(self,*args): raise AssertionError('deep payload reached RPC')
        nested=[]
        for _ in range(2000): nested=[nested]
        with self.assertRaises(KnowledgeError):
            invoke(Api(),RECORD,'write',{'space_id':SPACE,'operation':'propose',
                'payload':{**ENTRY,'source_refs':nested}},request_id='deep')

    def test_clean_individual_codex_and_claude_copies_need_no_repository_import(self):
        with tempfile.TemporaryDirectory() as root:
            for directory in ('.agents/skills/quorum','.claude/skills/quorum'):
                skill=Path(root)/directory
                shutil.copytree(SCRIPTS.parent,skill,ignore=shutil.ignore_patterns('.venv','__pycache__'))
                result=subprocess.run([sys.executable,str(skill/'scripts/quorum.py'),'knowledge','--help'],
                    cwd=root,env={**os.environ,'PYTHONPATH':''},capture_output=True,text=True,timeout=20)
                self.assertEqual(result.returncode,0,result.stderr)
                self.assertIn('capture-grant',result.stdout)
                self.assertIn('search',result.stdout)




class KnowledgeDispatchTests(unittest.TestCase):
    """Actual dispatcher and bounded HTTP seam, synthetic identity only."""
    def run_command(self, root, action, data, *, record=None, token_hook=None, required=None,
                    owner=False, material=False, request='dispatch', prepare=None, proof=None, response_hook=None, profile_hook=None, raw_stdin=None):
        import contextlib
        import io
        import json
        from unittest.mock import patch
        from quorum import main
        from quorum_api import QuorumApi,ServiceConfig
        value={**RECORD,'cwd':str(Path.cwd().resolve()),'device_id':SPACE,'role':'worker',
               'quorum':'synthetic','registration':{'session_ref':SID,'cwd':str(Path.cwd().resolve())},
               'transport':'native',**(record or {})}
        path=Path(root)/'sessions'/SID
        saved=LocalLedger(path); saved.setting('session-agent',value); saved.db.close()
        if prepare: prepare(path,value)
        calls=[]
        def token():
            if token_hook: token_hook(path,value)
            return 'synthetic-only'
        def transport(url,headers,body):
            operation=url.rsplit('/',1)[-1]; calls.append(operation)
            if response_hook: response_hook(path,value,operation)
            return (required or {'protocol_version':2,'skill_version':'0.3.0'}) if operation=='session_requirements' else {'revision':1}
        api=QuorumApi(ServiceConfig('https://synthetic.invalid','public'),token,request=transport)
        def selected_api(*args):
            if profile_hook: profile_hook(path,value)
            return api,None,None
        args=['--state',root,'knowledge',action,'--json-stdin']
        if request is not None: args+=['--request-id',request]
        if owner: args+=['--owner-action']
        if material: args+=['--material-result']
        with patch.dict(os.environ,{'CODEX_THREAD_ID':SID},clear=True), \
                patch('quorum_live.api_for',side_effect=selected_api), \
                (patch('quorum_native_inbox.require_worker',side_effect=proof) if proof else contextlib.nullcontext()), \
                patch('sys.stdin',io.TextIOWrapper(io.BytesIO(raw_stdin if raw_stdin is not None else json.dumps(data).encode()))), \
                contextlib.redirect_stdout(io.StringIO()) as output:
            code=main(args)
        return code,json.loads(output.getvalue()),calls

    def test_real_dispatch_refuses_mismatching_release_before_knowledge_mutation(self):
        with tempfile.TemporaryDirectory() as root:
            code,result,calls=self.run_command(root,'write',{'space_id':SPACE,'operation':'propose','payload':ENTRY},
                required={'protocol_version':2,'skill_version':'999.0.0'})
            self.assertEqual(code,4)
            self.assertEqual(result['code'],'skill_update_required')
            self.assertEqual(calls,['session_requirements'])

    def test_real_dispatch_success_checks_release_then_exact_binding_write(self):
        with tempfile.TemporaryDirectory() as root:
            code,result,calls=self.run_command(root,'write',{'space_id':SPACE,'operation':'propose','payload':ENTRY})
            self.assertEqual(code,0,result)
            self.assertEqual(calls,['session_requirements','knowledge_write'])

    def test_actual_token_boundary_rejects_changed_transport_proof_and_endpoint_markers(self):
        replacements=({'transport':'tool-inbox'},{'worker_binding':{'parent_thread_id':SPACE}},
                      {'messaging_socket':'new-socket'},{'grok_transport':{'leader_socket':'new-socket'}},
                      {'registration':{'cwd':'/other'}},{'device_id':ENTRY['entry_id']})
        for replacement in replacements:
            with self.subTest(replacement=replacement), tempfile.TemporaryDirectory() as root:
                def changed(path,record):
                    current=LocalLedger(path); current.setting('session-agent',{**record,**replacement}); current.db.close()
                code,result,calls=self.run_command(root,'write',{'space_id':SPACE,'operation':'propose','payload':ENTRY},token_hook=changed)
                self.assertNotEqual(code,0,result)
                self.assertEqual(calls,[],'changed exact binding must be refused before HTTP')

    def test_malformed_payloads_are_bounded_local_errors_with_no_private_text(self):
        for data in ([],{'space_id':SPACE,'operation':[],'payload':{'body':'private-probe'}},
                     {'space_id':SPACE,'operation':'propose','payload':{'body':'private-probe','verified':True}}):
            with self.subTest(data=data), tempfile.TemporaryDirectory() as root:
                code,result,calls=self.run_command(root,'write',data)
                self.assertEqual(code,3)
                self.assertEqual(result['code'],'knowledge_invalid_payload')
                self.assertEqual(calls,[])
                self.assertNotIn('private-probe',str(result))

    def test_local_grant_revocation_needs_no_release_or_remote_call(self):
        with tempfile.TemporaryDirectory() as root:
            code,result,calls=self.run_command(root,'capture-grant',{'space_id':SPACE,'revoke':True},owner=True)
            self.assertEqual((code,calls),(0,[]),result)
            self.assertEqual(result['data']['capture'],'revoked')

    def test_actual_cwd_and_profile_delay_are_rechecked_before_any_http(self):
        with tempfile.TemporaryDirectory() as root:
            code,result,calls=self.run_command(root,'write',{'space_id':SPACE,'operation':'propose','payload':ENTRY},
                record={'cwd':'/other-working-directory'})
            self.assertEqual(code,3,result)
            self.assertEqual(calls,[])
        def changed(path,record):
            current=LocalLedger(path); current.setting('session-agent',{**record,'transport':'tool-inbox'}); current.db.close()
        with tempfile.TemporaryDirectory() as root:
            code,result,calls=self.run_command(root,'write',{'space_id':SPACE,'operation':'propose','payload':ENTRY},profile_hook=changed)
            self.assertEqual(code,3,result)
            self.assertEqual(calls,[])

    def test_actual_dispatch_worker_proof_receives_latest_record_after_token(self):
        seen=[]
        def token(path,record):
            current=LocalLedger(path); current.setting('session-agent',{**record,'receive_metadata':'fresh'}); current.db.close()
        def proof(record): seen.append(record.get('receive_metadata'))
        with tempfile.TemporaryDirectory() as root:
            code,result,calls=self.run_command(root,'write',{'space_id':SPACE,'operation':'propose','payload':ENTRY},
                record={'transport':'tool-inbox','worker_binding':{'synthetic':'proof-seam'}},token_hook=token,proof=proof)
            self.assertEqual(code,0,result)
            self.assertEqual(calls,['session_requirements','knowledge_write'])
            self.assertIn('fresh',seen)

    def test_actual_capture_grant_ends_during_token_resolution_before_release_http(self):
        from quorum_knowledge import capture_grant
        def prepare(path,record):
            ledger=LocalLedger(path); capture_grant(ledger,record,SPACE,owner_action=True); ledger.db.close()
        def token(path,record):
            ledger=LocalLedger(path); capture_grant(ledger,record,SPACE,owner_action=True,revoke=True); ledger.db.close()
        with tempfile.TemporaryDirectory() as root:
            code,result,calls=self.run_command(root,'capture',{'space_id':SPACE,'entry':ENTRY},
                prepare=prepare,token_hook=token,material=True)
            self.assertEqual(code,3,result)
            self.assertEqual(calls,[])

    def test_server_response_cannot_return_after_exact_scope_changes(self):
        def response(path,record,operation):
            if operation=='knowledge_write':
                ledger=LocalLedger(path); ledger.setting('session-agent',{**record,'worker_binding':{'changed':True}}); ledger.db.close()
        with tempfile.TemporaryDirectory() as root:
            code,result,calls=self.run_command(root,'write',{'space_id':SPACE,'operation':'propose','payload':ENTRY},response_hook=response)
            self.assertEqual(code,3,result)
            self.assertEqual(result['data'],{})
            self.assertEqual(calls,['session_requirements','knowledge_write'])

    def test_deep_bounded_json_has_fixed_diagnostic_without_rpc_or_traceback(self):
        raw=b'['*20000+b'0'+b']'*20000
        self.assertLess(len(raw),131072)
        with tempfile.TemporaryDirectory() as root:
            code,result,calls=self.run_command(root,'write',{},raw_stdin=raw)
            self.assertEqual((code,result['code'],calls),(3,'knowledge_invalid_payload',[]))

    def test_malformed_capture_expiry_cannot_authorize_release_or_write_rpc(self):
        from quorum_knowledge import capture_grant
        for expiry in (float('nan'),float('inf'),float('-inf'),'later',True,None):
            with self.subTest(expiry=expiry), tempfile.TemporaryDirectory() as root:
                def prepare(path,record):
                    ledger=LocalLedger(path); capture_grant(ledger,record,SPACE,owner_action=True)
                    grant=ledger.setting('knowledge-capture:'+SPACE)
                    ledger.setting('knowledge-capture:'+SPACE,{**grant,'expires_at':expiry}); ledger.db.close()
                code,result,calls=self.run_command(root,'capture',{'space_id':SPACE,'entry':ENTRY},prepare=prepare,material=True)
                self.assertEqual(code,3,result)
                self.assertEqual(result['code'],'knowledge_capture_denied')
                self.assertEqual(calls,[])


if __name__ == '__main__': unittest.main()
