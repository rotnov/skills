"""Space default member role (#128) against disposable pinned pgvector PostgreSQL 17.

QUORUM_KNOWLEDGE_DOCKER=1 uses the already fetched public test image pinned by
OCI digest. No network, exposed ports, service configuration or provider calls.
"""
import hang_guard  # noqa: F401
from pathlib import Path
import os
import unittest
from unittest.mock import patch

import test_quorum_close_race as pg
from test_quorum_knowledge_backend import IMAGE
from quorum_update_sql_support import wait_statement

ROOT = Path(__file__).resolve().parents[1]
MIGRATIONS = ROOT / 'skills/quorum/supabase/migrations'
DEFAULT_ROLE = '20261106100000_knowledge_member_default_role.sql'
KNOWLEDGE = '20261030142000_project_knowledge.sql'
WIKI = '20261103075741_llm_wiki.sql'
PINNED = (
    'quorum_private.knowledge_scope(uuid,text,uuid,boolean,boolean)',
    'public.knowledge_admin(uuid,text,text,jsonb,text)',
    'quorum_private.knowledge_binding(uuid,text)',
    'quorum_private.native_session(uuid,text)',
    'quorum_private.knowledge_fields(jsonb,text[],text[])',
    'quorum_private.knowledge_cached(text,jsonb,text)',
    'quorum_private.knowledge_save(text,jsonb,text,jsonb)',
    'quorum_private.knowledge_mutation_lock(uuid)',
    'public.knowledge_write(uuid,text,uuid,text,jsonb,text)',
    'public.knowledge_index(uuid,text,uuid,uuid,integer,text,text,integer,jsonb,text)',
    'public.knowledge_search(uuid,text,uuid,text,integer,integer,text,jsonb)',
    'public.knowledge_read(uuid,text,uuid,uuid,integer,integer,integer,boolean,boolean)',
    'public.knowledge_source_write(uuid,text,uuid,jsonb,text)',
    'public.knowledge_source_read(uuid,text,uuid,uuid,integer,integer,boolean)',
    'public.knowledge_catalog(uuid,text,uuid,uuid,integer,integer)',
)
UNCHANGED = """select (to_regclass('quorum_private.knowledge_member_defaults') is null
  and to_regprocedure('quorum_private.knowledge_scope_before_default_role(uuid,text,uuid,boolean,boolean)') is null
  and to_regprocedure('quorum_private.knowledge_admin_before_default_role(uuid,text,text,jsonb,text)') is null
  and (to_regprocedure('public.knowledge_admin(uuid,text,text,jsonb,text)') is not null
   or to_regclass('quorum_private.knowledge_grants') is null))::text"""
OWNER = "select set_config('request.jwt.claim.sub','11111111-1111-4111-8111-111111111128',true);"
LATER = "select set_config('request.jwt.claim.sub','66666666-6666-4666-8666-666666666128',true);"
SPACE = '88888888-8888-4888-8888-888888888128'


def chain(skip=()):
    source = ''
    for path in sorted(MIGRATIONS.glob('*.sql')):
        if path.name != DEFAULT_ROLE and path.name not in skip:
            source += path.read_text()
    return source


def default_role(permission, request):
    return (f"select public.knowledge_admin('a0000000-0000-4000-8000-000000000128','dr-owner','default_role',"
            f"'{{\"space_id\":\"{SPACE}\",\"permission\":\"{permission}\"}}','{request}');")


def propose(entry, request):
    return (f"select public.knowledge_write('f0000000-0000-4000-8000-000000000128','dr-later','{SPACE}','propose',"
            f"'{{\"entry_id\":\"{entry}\",\"type\":\"fact\",\"title\":\"Race\",\"body\":\"Race\"}}','{request}');")


SEARCH = f"select public.knowledge_search('f0000000-0000-4000-8000-000000000128','dr-later','{SPACE}','DEFAULT_ROLE_128');"


@unittest.skipUnless(os.environ.get('QUORUM_KNOWLEDGE_DOCKER') == '1', 'opt-in disposable pgvector PostgreSQL')
class DefaultRoleBackendTests(unittest.TestCase):
    def test_guards_contract_and_races(self):
        self.assertTrue((MIGRATIONS / DEFAULT_ROLE).is_file())
        migration = (MIGRATIONS / DEFAULT_ROLE).read_text()
        with patch.object(pg, 'IMAGE', IMAGE), pg.Cluster() as cluster:
            # Cluster-wide fixture roles exist once; every database is cloned from this one.
            cluster.sql('postgres', 'create database fixtured')
            cluster.sql('fixtured', (ROOT / 'tests/quorum_backend_fixture.sql').read_text()
                        + (ROOT / 'skills/quorum/references/server-bootstrap.sql').read_text(), single=True)
            cluster.sql('postgres', 'create database base template fixtured')
            cluster.sql('base', chain(), single=True)
            self._guards(cluster, migration)
            cluster.sql('postgres', 'create database contract template base')
            cluster.sql('contract', migration, single=True)
            # Earlier knowledge and wiki contracts keep passing on the upgraded schema.
            self.assertIn('knowledge_contract_green',
                          cluster.sql('contract', (ROOT / 'tests/quorum_knowledge_backend.sql').read_text(), single=True))
            self.assertIn('wiki_contract_green',
                          cluster.sql('contract', (ROOT / 'tests/quorum_wiki_backend.sql').read_text(), single=True))
            self.assertIn('default_role_contract_green', cluster.sql(
                'contract', (ROOT / 'tests/quorum_knowledge_default_role_backend.sql').read_text(), single=True))
            self._races(cluster)
        self.assertTrue(cluster.removed)

    def _refused(self, cluster, db, migration, expected):
        with self.assertRaises(RuntimeError) as raised:
            cluster.sql(db, migration, single=True)
        self.assertIn(expected, str(raised.exception))
        self.assertEqual(cluster.sql(db, UNCHANGED).strip(), 'true')

    def _guards(self, cluster, migration):
        # Reapplication is refused before any change.
        cluster.sql('postgres', 'create database reapplied template base')
        cluster.sql('reapplied', migration, single=True)
        with self.assertRaises(RuntimeError) as raised:
            cluster.sql('reapplied', migration, single=True)
        self.assertIn('knowledge_default_role_present', str(raised.exception))
        # Without the wiki (or without knowledge at all) nothing changes.
        for name, skip in (('nowiki', (WIKI,)), ('noknowledge', (KNOWLEDGE, WIKI))):
            cluster.sql('postgres', f'create database {name} template fixtured')
            cluster.sql(name, chain(skip), single=True)
            self._refused(cluster, name, migration, 'knowledge_wiki_required')
        # A drifted reviewed body fails closed: same behaviour, different source text.
        for index, fn in enumerate(PINNED):
            db = f'drift{index}'
            cluster.sql('postgres', f'create database {db} template base')
            cluster.sql(db, f"""do $d$ declare def text:=pg_get_functiondef('{fn}'::regprocedure); begin
              execute regexp_replace(def, '\\$(function|\\$)?\\$', '$\\1$ /* drift */ ', ''); end $d$;""")
            self._refused(cluster, db, migration, 'reviewed_dispatcher_changed: ' + fn)

    def _blocked(self, cluster, first, second, what):
        """Run `first` (held open), then `second`, which must wait on the first backend."""
        one = cluster.session('contract', 'dr_' + what + '_one')
        two = cluster.session('contract', 'dr_' + what + '_two')
        try:
            one.send('begin;' + first)
            held = wait_statement(cluster, 'contract', one.label, 'select', lambda r: r['state'] == 'idle in transaction',
                                  what + ' held')
            two.send('begin;' + second + 'commit;')
            wait_statement(cluster, 'contract', two.label, 'select', lambda r: held['pid'] in r['blocking'],
                           what + ' waits on the held backend')
            one.send('commit;')
            return one.finish(), two.finish()
        finally:
            for session in (one, two):
                if session.proc.poll() is None:
                    session.send('rollback;'); session.finish()

    def _races(self, cluster):
        # The suite leaves a writer default on an attached space; the later joiner has no grant.
        # A writer that waited on a removal is refused after the removal commits.
        first, second = self._blocked(cluster, OWNER + default_role('none', 'race-none'),
                                      LATER + propose('a1000000-0000-4000-8000-000000000128', 'race-write'), 'downgrade_write')
        self.assertEqual(first[0], 0, first)
        self.assertNotEqual(second[0], 0, second)
        self.assertIn('knowledge_denied', second[2])
        # A reader that waited on the space row sees the committed default, too.
        cluster.sql('contract', 'begin;' + OWNER + default_role('reader', 'race-reader') + 'commit;')
        first, second = self._blocked(cluster, OWNER + default_role('none', 'race-none-2'), LATER + SEARCH, 'downgrade_read')
        self.assertEqual(first[0], 0, first)
        self.assertIn('knowledge_denied', second[2])
        # An upgrade that commits while a writer waits admits that writer.
        first, second = self._blocked(cluster, OWNER + default_role('writer', 'race-writer'),
                                      LATER + propose('a2000000-0000-4000-8000-000000000128', 'race-write-2'), 'upgrade_write')
        self.assertEqual((first[0], second[0]), (0, 0), (first, second))
        # A default change waits for an in-flight default writer; that write was authorized and stays.
        first, second = self._blocked(cluster, LATER + propose('a3000000-0000-4000-8000-000000000128', 'race-write-3'),
                                      OWNER + default_role('none', 'race-none-3'), 'write_then_downgrade')
        self.assertEqual((first[0], second[0]), (0, 0), (first, second))
        self.assertEqual(cluster.sql('contract', "select count(*) from quorum_private.knowledge_entries where id in "
                                     "('a2000000-0000-4000-8000-000000000128','a3000000-0000-4000-8000-000000000128')").strip(), '2')
        self.assertEqual(cluster.sql('contract', "select count(*) from quorum_private.knowledge_entries where id="
                                     "'a1000000-0000-4000-8000-000000000128'").strip(), '0')
        self.assertEqual(cluster.sql('contract', f"select count(*) from quorum_private.knowledge_member_defaults "
                                     f"where space_id='{SPACE}'").strip(), '0')


if __name__ == '__main__':
    unittest.main()
