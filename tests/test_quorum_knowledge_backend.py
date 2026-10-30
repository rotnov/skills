"""Actual PostgreSQL 17 + pgvector 0.8.7, opt-in and entirely synthetic.

QUORUM_KNOWLEDGE_DOCKER=1 uses the already fetched public test image pinned by
OCI digest. No network, exposed ports, service configuration or provider calls.
"""
from pathlib import Path
import os
import unittest
from unittest.mock import patch

import test_quorum_close_race as pg
from quorum_update_sql_support import wait_statement

ROOT=Path(__file__).resolve().parents[1]
IMAGE='pgvector/pgvector@sha256:ac08538c6f8b9904c33c8224c5e5706dbe760aca29db1d096972b4052c22a75d'
MIGRATION=ROOT/'skills/quorum/supabase/migrations/20261030142000_project_knowledge.sql'


@unittest.skipUnless(os.environ.get('QUORUM_KNOWLEDGE_DOCKER')=='1','opt-in disposable pgvector PostgreSQL')
class KnowledgeBackendTests(unittest.TestCase):
    def test_actual_pgvector_permissions_lifecycle_retrieval_and_cas(self):
        self.assertTrue(MIGRATION.is_file(),'project knowledge migration missing')
        with patch.object(pg,'IMAGE',IMAGE), pg.Cluster() as cluster:
            cluster.sql('postgres','create database knowledge')
            source=(ROOT/'tests/quorum_backend_fixture.sql').read_text()
            source+=(ROOT/'skills/quorum/references/server-bootstrap.sql').read_text()
            for path in sorted((ROOT/'skills/quorum/supabase/migrations').glob('*.sql')):
                if path.name<MIGRATION.name: source+=path.read_text()
            source+=MIGRATION.read_text()
            cluster.sql('knowledge',source,single=True)
            self.assertEqual(cluster.sql('knowledge',"select extversion from pg_extension where extname='vector'").strip(),'0.8.7')
            self.assertIn('knowledge_contract_green',cluster.sql('knowledge',(ROOT/'tests/quorum_knowledge_backend.sql').read_text(),single=True))
            # Independent backend transactions compete on the same expected revision.
            fixture=cluster.sql('knowledge',"""
              select set_config('request.jwt.claim.sub','11111111-1111-4111-8111-111111111159',false);
              select public.knowledge_write('aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaa59','owner-session',
                '55555555-5555-4555-8555-555555555559','propose',
                '{"entry_id":"eeeeeeee-eeee-4eee-8eee-eeeeeeeeee59","type":"fact","title":"CAS","body":"initial"}','concurrency-seed');
            """)
            self.assertIn('revision',fixture)
            one=cluster.session('knowledge','knowledge_cas_one')
            two=cluster.session('knowledge','knowledge_cas_two')
            pre="begin; select set_config('request.jwt.claim.sub','11111111-1111-4111-8111-111111111159',true);"
            call="""select public.knowledge_write('aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaa59','owner-session',
              '55555555-5555-4555-8555-555555555559','revise',
              '{"entry_id":"eeeeeeee-eeee-4eee-8eee-eeeeeeeeee59","expected_revision":1,"type":"fact","title":"CAS","body":"%s"}','%s');"""
            try:
                one.send(pre)
                before=wait_statement(cluster,'knowledge',one.label,'set_config',
                    lambda r:r['state']=='idle in transaction','pre-operation idle phase')
                self.assertNotIn('public.knowledge_write',before['query'])
                one.send(call%('first','cas-first'))
                held=wait_statement(cluster,'knowledge',one.label,
                    'public.knowledge_write',lambda r:r['state']=='idle in transaction' and "'cas-first'" in r['query'],
                    'actual first revision completed and held')
                two.send(pre+call%('second','cas-second'))
                wait_statement(cluster,'knowledge',two.label,'public.knowledge_write',
                    lambda r:held['pid'] in r['blocking'] and "'cas-second'" in r['query'],
                    'actual second revision waits on first backend')
                one.send('commit;')
                first=one.finish()
                second=two.finish()
                self.assertEqual(first[0],0,first)
                self.assertNotEqual(second[0],0,second)
                self.assertIn('knowledge_revision_conflict',second[2])
                self.assertEqual(cluster.sql('knowledge',"select current_revision from quorum_private.knowledge_entries where id='eeeeeeee-eeee-4eee-8eee-eeeeeeeeee59'").strip(),'2')
            finally:
                for session in (one,two):
                    if session.proc.poll() is None:
                        session.send('rollback;'); session.finish()
        self.assertTrue(cluster.removed)


if __name__=='__main__': unittest.main()
