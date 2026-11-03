"""Wiki contracts against disposable pinned PostgreSQL, never a live service."""
from pathlib import Path
import os
import json
import unittest
from unittest.mock import patch
import test_quorum_close_race as pg
from test_quorum_knowledge_backend import IMAGE
from quorum_update_sql_support import wait_statement

ROOT = Path(__file__).resolve().parents[1]

@unittest.skipUnless(os.environ.get('QUORUM_KNOWLEDGE_DOCKER') == '1', 'opt-in disposable PostgreSQL')
class WikiBackendTests(unittest.TestCase):
    def test_wiki_contract(self):
        with patch.object(pg, 'IMAGE', IMAGE), pg.Cluster() as cluster:
            cluster.sql('postgres', 'create database wiki')
            source = (ROOT / 'tests/quorum_backend_fixture.sql').read_text()
            source += (ROOT / 'skills/quorum/references/server-bootstrap.sql').read_text()
            for path in sorted((ROOT / 'skills/quorum/supabase/migrations').glob('*.sql')):
                source += path.read_text()
            cluster.sql('wiki', source, single=True)
            cluster.sql('wiki', (ROOT / 'tests/quorum_knowledge_backend.sql').read_text(), single=True)
            self.assertIn('wiki_contract_green', cluster.sql('wiki', (ROOT / 'tests/quorum_wiki_backend.sql').read_text(), single=True))
            self._concurrency(cluster)
            self._page_identity_race(cluster)
        self.assertTrue(cluster.removed)

    def _concurrency(self, cluster):
        owner = "select set_config('request.jwt.claim.sub','11111111-1111-4111-8111-111111111159',true);"
        agent = "'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaa59','owner-session'"
        space = "'55555555-5555-4555-8555-555555555559'"
        entry = '60000000-0000-4000-8000-000000000059'
        source = '20000000-0000-4000-8000-000000000059'
        new_source = '70000000-0000-4000-8000-000000000059'
        related = '50000000-0000-4000-8000-000000000059'
        original = dict(entry_id=entry, entry_kind='wiki_page', type='fact', title='Wiki CAS', body='Initial wiki',
                        source_ids=[source], related_pages=[related], source_refs=[], conflicts_with=[])
        def write(operation, payload, request):
            return f"select public.knowledge_write({agent},{space},'{operation}','{json.dumps(payload)}','{request}');"
        retained = dict(source_id=new_source, reference='synthetic:cas', author='Synthetic',
                        source_date='2026-01-01T00:00:00Z', body='CAS retained evidence', is_excerpt=False, excerpt_scope='')
        cluster.sql('wiki', 'begin;'+owner+f"select public.knowledge_source_write({agent},{space},'{json.dumps(retained)}','cas-retain');"+write('propose',original,'wiki-cas-seed')+'commit;')
        winner = dict(original, expected_revision=1, source_ids=[new_source], related_pages=[])
        del winner['entry_kind']
        loser = dict(winner, source_ids=[source], body='Losing update')
        one = cluster.session('wiki', 'wiki_cas_one')
        two = cluster.session('wiki', 'wiki_cas_two')
        try:
            one.send('begin;'+owner+write('revise',winner,'wiki-cas-first'))
            held = wait_statement(cluster,'wiki',one.label,'public.knowledge_write',
                                  lambda r:r['state']=='idle in transaction' and "'wiki-cas-first'" in r['query'], 'winner held')
            two.send('begin;'+owner+write('revise',loser,'wiki-cas-second'))
            wait_statement(cluster,'wiki',two.label,'public.knowledge_write',
                           lambda r:held['pid'] in r['blocking'], 'loser blocked on winner')
            one.send('commit;')
            self.assertEqual(one.finish()[0],0)
            result = two.finish()
            self.assertNotEqual(result[0],0)
            self.assertIn('knowledge_revision_conflict',result[2])
            history=json.loads(cluster.sql('wiki',f"select jsonb_agg(jsonb_build_object('revision',revision,'source_ids',source_ids,'related_pages',related_pages,'hash',content_sha256) order by revision) from quorum_private.knowledge_revisions where entry_id='{entry}'"))
            self.assertEqual([x['revision'] for x in history],[1,2])
            self.assertEqual(history[0]['source_ids'],[source])
            self.assertEqual(history[0]['related_pages'],[related])
            self.assertEqual(history[1]['source_ids'],[new_source])
            self.assertEqual(history[1]['related_pages'],[])
            self.assertNotEqual(history[0]['hash'],history[1]['hash'])
        finally:
            for session in (one,two):
                if session.proc.poll() is None:
                    session.send('rollback;'); session.finish()
        # A concurrent retained-source withdrawal wins before validation. The
        # blocked writer must recheck after the real transaction lock releases.
        one = cluster.session('wiki','wiki_withdraw_one')
        two = cluster.session('wiki','wiki_withdraw_two')
        source_hash = cluster.sql('wiki',f"select content_sha256 from quorum_private.knowledge_sources where source_id='{new_source}'").strip()
        admin_payload = json.dumps(dict(space_id=space.strip("'"),source_id=new_source,expected_content_sha256=source_hash))
        try:
            one.send('begin;'+owner+f"select public.knowledge_admin({agent},'withdraw_source','{admin_payload}','wiki-race-withdraw');")
            held=wait_statement(cluster,'wiki',one.label,'public.knowledge_admin',lambda r:r['state']=='idle in transaction','source withdrawal held')
            two.send('begin;'+owner+write('revise',dict(winner,expected_revision=2),'wiki-race-revise'))
            wait_statement(cluster,'wiki',two.label,'public.knowledge_write',lambda r:held['pid'] in r['blocking'],'writer waits on source withdrawal')
            one.send('commit;')
            self.assertEqual(one.finish()[0],0)
            result=two.finish()
            self.assertNotEqual(result[0],0)
            self.assertIn('knowledge_invalid_link',result[2])
            cluster.sql('wiki','begin;'+owner+write('revise',dict(loser,expected_revision=2),'wiki-reconciled')+'commit;')
            self.assertEqual(cluster.sql('wiki',f"select current_revision from quorum_private.knowledge_entries where id='{entry}'").strip(),'3')
        finally:
            for session in (one,two):
                if session.proc.poll() is None:
                    session.send('rollback;'); session.finish()

    def _page_identity_race(self, cluster):
        # A different space has a different mutation lock. The shared page UUID
        # must still wait for purge and recheck its permanent reservation.
        owner = "select set_config('request.jwt.claim.sub','11111111-1111-4111-8111-111111111159',true);"
        member = "select set_config('request.jwt.claim.sub','22222222-2222-4222-8222-222222222259',true);"
        owner_agent = "'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaa59','owner-session'"
        member_agent = "'bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbb59','member-session'"
        space = '55555555-5555-4555-8555-555555555559'
        other_space = '72000000-0000-4000-8000-000000000059'
        entry = '71000000-0000-4000-8000-000000000059'
        payload = dict(entry_id=entry, type='fact', title='Identity race', body='Original content')
        cluster.sql('wiki', 'begin;'+owner+f"""
            select public.knowledge_write({owner_agent},'{space}','propose','{json.dumps(payload)}','identity-race-seed');
            select public.knowledge_admin({owner_agent},'create','{{"space_id":"{other_space}","name":"Identity race"}}','identity-race-space');
            select public.knowledge_admin({owner_agent},'attach','{{"space_id":"{other_space}","quorum_id":"44444444-4444-4444-8444-444444444459","project_ref":"same"}}','identity-race-attach');
            select public.knowledge_admin({owner_agent},'grant','{{"space_id":"{other_space}","user_id":"22222222-2222-4222-8222-222222222259","permission":"writer"}}','identity-race-grant');
            commit;
        """)
        one = cluster.session('wiki', 'wiki_identity_purge')
        two = cluster.session('wiki', 'wiki_identity_reuse')
        replacement = dict(payload, body='Unrelated replacement')
        try:
            one.send('begin;'+owner+f"select public.knowledge_admin({owner_agent},'purge','{{\"space_id\":\"{space}\",\"entry_id\":\"{entry}\",\"expected_revision\":1}}','identity-race-purge');")
            held = wait_statement(cluster, 'wiki', one.label, 'public.knowledge_admin',
                                  lambda r: r['state']=='idle in transaction', 'purge held before commit')
            two.send('begin;'+member+f"select public.knowledge_write({member_agent},'{other_space}','propose','{json.dumps(replacement)}','identity-race-reuse');")
            wait_statement(cluster, 'wiki', two.label, 'public.knowledge_write',
                           lambda r: held['pid'] in r['blocking'], 'cross-space writer waits on page identity')
            one.send('commit;')
            self.assertEqual(one.finish()[0], 0)
            result = two.finish()
            self.assertNotEqual(result[0], 0)
            self.assertIn('knowledge_page_immutable', result[2])
            replacement['entry_id'] = '73000000-0000-4000-8000-000000000059'
            cluster.sql('wiki', 'begin;'+member+f"select public.knowledge_write({member_agent},'{other_space}','propose','{json.dumps(replacement)}','identity-race-distinct');commit;")
            self.assertEqual(cluster.sql('wiki', f"select count(*) from quorum_private.knowledge_entries where id='{entry}'").strip(), '0')
            self.assertEqual(cluster.sql('wiki', f"select count(*) from quorum_private.knowledge_page_tombstones where entry_id='{entry}' and space_id='{space}'").strip(), '1')
        finally:
            for session in (one, two):
                if session.proc.poll() is None:
                    session.send('rollback;'); session.finish()
