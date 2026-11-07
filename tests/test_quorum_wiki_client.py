"""Retained-source wiki transport and independently copied client contracts."""
import hang_guard  # noqa: F401
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

SCRIPTS = Path(os.environ.get('QUORUM_WIKI_SCRIPTS',
    Path(__file__).resolve().parents[1] / 'skills/quorum/scripts'))
sys.path.insert(0, str(SCRIPTS))
from quorum_api import QuorumApi, QuorumError, ServiceConfig
from quorum_knowledge import invoke, KnowledgeError
from quorum_state import LocalLedger
from test_quorum_knowledge import RECORD, SID, SPACE, ENTRY
import test_quorum_knowledge as existing_tests
# The reusable test harness inserts its source checkout path. Runtime imports in
# copied-skill scenarios must still resolve only from the individual skill copy.
if 'QUORUM_WIKI_SCRIPTS' in os.environ:
    sys.path = [str(SCRIPTS)] + [p for p in sys.path if p != str(existing_tests.SCRIPTS) and p != str(SCRIPTS)]

SOURCE_ID = '66666666-6666-4666-8666-666666666666'
SOURCE = {'source_id': SOURCE_ID, 'reference': 'https://example.org/public',
          'author': 'Public author', 'source_date': '2026-11-03T10:00:00Z',
          'body': 'Retained public evidence', 'is_excerpt': True,
          'excerpt_scope': 'Section 2 only'}
WIKI = {**ENTRY, 'entry_kind': 'wiki_page', 'source_ids': [SOURCE_ID], 'related_pages': []}


class WikiTransportTests(unittest.TestCase):
    def api(self, response=None, token=None):
        self.sent = []
        def request(url, headers, body):
            self.sent.append((url.rsplit('/', 1)[-1], json.loads(body)))
            if isinstance(response, Exception): raise response
            return response if response is not None else {'ok': True}
        return QuorumApi(ServiceConfig('https://synthetic.invalid', 'public'),
                         token or (lambda: 'synthetic-only'), request=request)

    def test_source_write_maps_action_and_preserves_exact_payload_and_bound_identity(self):
        api = self.api({'source_id': SOURCE_ID, 'content_sha256': 'a'*64})
        self.assertEqual(invoke(api, RECORD, 'source-write', {'space_id': SPACE, 'payload': SOURCE},
                                request_id='source-1')['source_id'], SOURCE_ID)
        self.assertEqual(self.sent, [('knowledge_source_write', {
            'p_agent_id': RECORD['agent_id'], 'p_session_ref': SID,
            'p_space_id': SPACE, 'p_payload': SOURCE, 'p_request_id': 'source-1'})])

    def test_catalog_source_read_history_and_wiki_write_transport(self):
        for action, data, rpc, request in (
            ('catalog', {'space_id': SPACE, 'after_id': ENTRY['entry_id'], 'limit': 2,
                         'context_budget': 2048}, 'knowledge_catalog', None),
            ('source-read', {'space_id': SPACE, 'source_id': SOURCE_ID, 'offset': 3,
                             'length': 40, 'audit': False}, 'knowledge_source_read', None),
            ('read', {'space_id': SPACE, 'entry_id': ENTRY['entry_id'], 'include_history': True},
                     'knowledge_read', None),
            ('write', {'space_id': SPACE, 'operation': 'propose', 'payload': WIKI},
                      'knowledge_write', 'page-1'),
            ('write', {'space_id': SPACE, 'operation': 'revise',
                       'payload': {k: v for k,v in {**WIKI, 'expected_revision': 1,
                                                   'conflicts_with': []}.items() if k != 'entry_kind'}},
                      'knowledge_write', 'page-2')):
            with self.subTest(action=action):
                api = self.api()
                invoke(api, RECORD, action, data, request_id=request)
                expected = {'p_agent_id': RECORD['agent_id'], 'p_session_ref': SID,
                            **{'p_'+key: value for key,value in data.items()}}
                if request: expected['p_request_id'] = request
                self.assertEqual(self.sent, [(rpc, expected)])

    def test_source_timestamp_rejects_overflowing_offset_before_http(self):
        for offset in ('+00:99','-00:60','+15:60','+16:00','-24:00'):
            with self.subTest(offset=offset):
                api=self.api()
                with self.assertRaises(KnowledgeError) as error:
                    invoke(api,RECORD,'source-write',{'space_id':SPACE,
                        'payload':{**SOURCE,'source_date':'2026-11-03T10:00:00'+offset}},request_id='offset')
                self.assertEqual(error.exception.code,'knowledge_invalid_payload')
                self.assertEqual(self.sent,[])

    def test_source_timestamp_preserves_valid_z_and_signed_offset_inputs(self):
        for offset in ('Z','+00:00','+05:30','-03:30','+15:59','-15:59'):
            with self.subTest(offset=offset):
                api=self.api()
                date='2026-11-03T10:00:00'+offset
                invoke(api,RECORD,'source-write',{'space_id':SPACE,
                    'payload':{**SOURCE,'source_date':date}},request_id='offset')
                self.assertEqual(len(self.sent),1)
                self.assertEqual(self.sent[0][1]['p_payload']['source_date'],date)

    def test_typed_bounded_source_metadata_and_unknown_fields_fail_before_http(self):
        patches = [{'source_id': 'not-uuid'}, {'reference': ''}, {'reference': 'x'*513},
                   {'author': 'x'*201}, {'author': 1}, {'body': ''}, {'body': 'é'*4097},
                   {'body': '\ud800'}, {'is_excerpt': 'false'}, {'is_excerpt': 1},
                   {'excerpt_scope': ''}, {'excerpt_scope': 'x'*513},
                   {'source_date': '2026-11-03'}, {'source_date': '2026-11-03T10:00Z'},
                   {'source_date': '2026-11-03T10:00:00'},
                   {'source_date': '2026-02-30T10:00:00Z'}, {'capturer_user': SPACE},
                   {'owner_action': True}, {'agent_id': SPACE}]
        for patch in patches:
            with self.subTest(patch=patch):
                api = self.api()
                with self.assertRaises(KnowledgeError) as error:
                    invoke(api, RECORD, 'source-write', {'space_id': SPACE, 'payload': {**SOURCE, **patch}}, request_id='bad')
                self.assertEqual(error.exception.code, 'knowledge_invalid_payload')
                self.assertEqual(self.sent, [])
        for key in SOURCE:
            api = self.api()
            with self.assertRaises(KnowledgeError):
                invoke(api, RECORD, 'source-write', {'space_id': SPACE,
                    'payload': {k:v for k,v in SOURCE.items() if k != key}}, request_id='missing')
            self.assertEqual(self.sent, [])

    def test_wiki_and_retrieval_invalid_types_or_authority_overrides_fail_before_http(self):
        for action,data in (
            ('source-read', {'space_id': SPACE, 'source_id': SOURCE_ID, 'audit': 'false'}),
            ('source-read', {'space_id': SPACE, 'source_id': SOURCE_ID, 'offset': True}),
            ('source-read', {'space_id': SPACE, 'source_id': SOURCE_ID, 'length': 4097}),
            ('catalog', {'space_id': SPACE, 'agent_id': SPACE}),
            ('catalog', {'space_id': SPACE, 'after_id': 'foreign-string'}),
            ('catalog', {'space_id': SPACE, 'limit': True}),
            ('catalog', {'space_id': SPACE, 'context_budget': 6001}),
            ('read', {'space_id': SPACE, 'entry_id': ENTRY['entry_id'], 'include_history': 'true'}),
            ('write', {'space_id': SPACE, 'operation': 'propose', 'payload': {**WIKI, 'source_ids': [SOURCE_ID]*2}}),
            ('write', {'space_id': SPACE, 'operation': 'propose', 'payload': {**WIKI, 'source_ids': []}}),
            ('write', {'space_id': SPACE, 'operation': 'propose', 'payload': {**WIKI, 'related_pages': [SPACE]*9}}),
            ('write', {'space_id': SPACE, 'operation': 'propose', 'payload': {**WIKI, 'entry_kind': 'source'}}),
            ('write', {'space_id': SPACE, 'operation': 'revise', 'payload': {**WIKI, 'expected_revision': 1}})):
            with self.subTest(action=action,data=data):
                api = self.api()
                with self.assertRaises(KnowledgeError) as error:
                    invoke(api, RECORD, action, data, request_id='bad')
                self.assertEqual(error.exception.code, 'knowledge_invalid_payload')
                self.assertEqual(self.sent, [])

    def test_source_mutation_ids_owner_audit_and_source_admin_require_explicit_authority(self):
        api = self.api()
        with self.assertRaisesRegex(KnowledgeError, 'knowledge_request_id_required'):
            invoke(api, RECORD, 'source-write', {'space_id': SPACE, 'payload': SOURCE})
        for action,data in [('source-read', {'space_id': SPACE, 'source_id': SOURCE_ID, 'audit': True})] + [
            ('admin', {'operation': op, 'payload': {'space_id': SPACE, 'source_id': SOURCE_ID,
                                                   'expected_content_sha256': 'a'*64}})
            for op in ('withdraw_source', 'purge_source')]:
            with self.subTest(action=action), self.assertRaisesRegex(KnowledgeError, 'knowledge_owner_action_required'):
                invoke(api, RECORD, action, data, request_id='owner')
            invoke(api, RECORD, action, data, request_id='owner', owner_action=True)
        self.assertEqual([op for op,_ in self.sent], ['knowledge_source_read', 'knowledge_admin', 'knowledge_admin'])

    def test_unavailable_source_backend_has_fixed_diagnostic(self):
        api = self.api(QuorumError('service_error'))
        with self.assertRaisesRegex(KnowledgeError, '^knowledge_backend_unavailable_or_rejected$'):
            invoke(api, RECORD, 'source-write', {'space_id': SPACE, 'payload': SOURCE}, request_id='unavailable')
        self.assertEqual(self.sent[0][0], 'knowledge_source_write')


class WikiDispatchTests(unittest.TestCase):
    run_command = existing_tests.KnowledgeDispatchTests.run_command

    def grant(self, path, record):
        from quorum_knowledge import capture_grant
        ledger = LocalLedger(path)
        capture_grant(ledger, record, SPACE, owner_action=True)
        ledger.db.close()

    def test_source_and_wiki_capture_use_existing_grant_and_release_guard(self):
        for action,data,rpc in [('capture-source', {'space_id': SPACE, 'payload': SOURCE}, 'knowledge_source_write'),
                                ('capture', {'space_id': SPACE, 'entry': WIKI}, 'knowledge_write')]:
            with self.subTest(action=action), tempfile.TemporaryDirectory() as root:
                code,result,calls = self.run_command(root, action, data, prepare=self.grant, material=True)
                self.assertEqual((code,calls), (0,['session_requirements',rpc]), result)

    def test_source_capture_denies_missing_material_grant_and_mismatching_release(self):
        for options,want,calls_want in [({},'knowledge_material_result_required',[]),
             ({'material':True},'knowledge_capture_denied',[]),
             ({'material':True,'prepare':self.grant,'required':{'protocol_version':99}},
               'skill_update_required',['session_requirements'])]:
            with self.subTest(options=options), tempfile.TemporaryDirectory() as root:
                code,result,calls = self.run_command(root,'capture-source',{'space_id':SPACE,'payload':SOURCE},**options)
                self.assertNotEqual(code,0)
                self.assertEqual((result['code'],calls),(want,calls_want))

    def test_source_capture_rejects_payload_or_binding_change_before_http(self):
        def change(path,record):
            ledger=LocalLedger(path); ledger.setting('session-agent',{**record,'agent_id':SPACE}); ledger.db.close()
        for payload,hook,want in [({**SOURCE,'capturer_agent':SPACE},None,'knowledge_invalid_payload'),
                                  (SOURCE,change,'knowledge_binding_required')]:
            with self.subTest(want=want), tempfile.TemporaryDirectory() as root:
                code,result,calls=self.run_command(root,'capture-source',{'space_id':SPACE,'payload':payload},
                    prepare=self.grant,material=True,token_hook=hook)
                self.assertEqual((code,result['code'],calls),(3,want,[]))

    def test_source_capture_rechecks_grant_after_token_delay_and_before_return(self):
        from quorum_knowledge import capture_grant
        def revoke(path,record):
            ledger=LocalLedger(path); capture_grant(ledger,record,SPACE,owner_action=True,revoke=True); ledger.db.close()
        for boundary in ('release-token','source-token','source-response'):
            count=[0]
            def token(path,record):
                count[0]+=1
                if count[0] == (1 if boundary=='release-token' else 2) and boundary!='source-response': revoke(path,record)
            def response(path,record,operation):
                if boundary=='source-response' and operation=='knowledge_source_write': revoke(path,record)
            with self.subTest(boundary=boundary), tempfile.TemporaryDirectory() as root:
                code,result,calls=self.run_command(root,'capture-source',{'space_id':SPACE,'payload':SOURCE},
                    prepare=self.grant,material=True,token_hook=token,response_hook=response)
                self.assertEqual((code,result['code']),(3,'knowledge_capture_denied'))
                expected=[] if boundary=='release-token' else ['session_requirements']
                if boundary=='source-response': expected+=['knowledge_source_write']
                self.assertEqual(calls,expected)


class WikiCopiedSkillTests(unittest.TestCase):
    def test_individual_codex_and_claude_copies_execute_source_success_denial_and_unavailable(self):
        with tempfile.TemporaryDirectory() as root:
            for directory in ('.agents/skills/quorum', '.claude/skills/quorum'):
                with self.subTest(client=directory):
                    skill=Path(root)/directory
                    shutil.copytree(SCRIPTS.parent,skill,ignore=shutil.ignore_patterns('.venv','__pycache__'))
                    runner = ('import runpy,unittest; '
                        f'm=runpy.run_path({str(Path(__file__).resolve())!r}); '
                        'suite=unittest.TestSuite(unittest.defaultTestLoader.loadTestsFromTestCase(m[n]) '
                        'for n in ("WikiTransportTests","WikiDispatchTests")); '
                        'r=unittest.TextTestRunner(verbosity=2).run(suite); '
                        'import sys,os; from pathlib import Path; '
                        'assert all(Path(v.__file__).resolve().is_relative_to(Path(os.environ["QUORUM_WIKI_SCRIPTS"]).resolve()) '
                        'for k,v in sys.modules.items() if k=="quorum" or k.startswith("quorum_")); '
                        'raise SystemExit(not r.wasSuccessful())')
                    result=subprocess.run([sys.executable,'-c',runner],cwd=root,
                        env={**os.environ,'PYTHONPATH':str(Path(__file__).parent),
                             'QUORUM_WIKI_SCRIPTS':str(skill/'scripts')},capture_output=True,text=True,timeout=30)
                    self.assertEqual(result.returncode,0,result.stdout+result.stderr)


if __name__ == '__main__': unittest.main()
