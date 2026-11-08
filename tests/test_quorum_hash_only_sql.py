"""The hash-only release on an opt-in, network-none PostgreSQL 17 container (#172).

QUORUM_RELEASE_DOCKER=1 uses only the existing local image and removes its own
container. No real service configuration, credentials or client process is read.
"""
import hang_guard  # noqa: F401
import json
import os
from pathlib import Path
import unittest

from test_quorum_close_race import Cluster
from quorum_update_sql_support import wait_statement

ROOT = Path(__file__).resolve().parents[1]
MIGRATIONS = ROOT / 'skills/quorum/supabase/migrations'
MIGRATION = '20261108120000_hash_only_release.sql'
OPTIONAL = ('_project_knowledge.sql', '_llm_wiki.sql', '_knowledge_member_default_role.sql')
# What every server required before this release, and what it requires afterwards: the manifest.
PHASE_ONE = {'protocol_version': 2, 'skill_version': '0.3.0'}
CURRENT = json.loads((ROOT / 'skills/quorum/assets/release.json').read_text())


def uid(number):
    return f'{number:08x}-7272-4272-8272-727272727272'


OWNER, QUORUM, DEVICE, AGENT = uid(1), uid(10), uid(20), uid(30)


def release(value):
    return f"select set_config('quorum_test.release','{json.dumps(value)}',true);"


@unittest.skipUnless(os.environ.get('QUORUM_RELEASE_DOCKER') == '1', 'opt-in disposable hash-only release')
class HashOnlyReleaseTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.cluster = Cluster().__enter__()
        cls.addClassCleanup(cls.cluster.__exit__)
        cls.cluster.sql('postgres', 'create database phase_one')
        setup = (ROOT / 'tests/quorum_backend_fixture.sql').read_text()
        setup += (ROOT / 'skills/quorum/references/server-bootstrap.sql').read_text()
        # pgvector extensions are optional and need another image; this release does not depend on them.
        setup += ''.join(path.read_text() for path in sorted(MIGRATIONS.glob('*.sql'))
                         if path.name < MIGRATION and not path.name.endswith(OPTIONAL))
        cls.cluster.sql('phase_one', setup, single=True)
        cls.cluster.sql('phase_one', f"""
          insert into auth.users(id,raw_user_meta_data) values('{OWNER}','{{}}');
          insert into auth.identities(user_id,provider,provider_id,identity_data)
            values('{OWNER}','github','9000000000272','{{}}');
          insert into quorum_private.quorums(id,slug,owner_id) values('{QUORUM}','hash-only','{OWNER}');
          insert into quorum_private.memberships(quorum_id,user_id,role) values('{QUORUM}','{OWNER}','owner');
          insert into quorum_private.devices(id,owner_id,label) values('{DEVICE}','{OWNER}','owner');
          insert into quorum_private.agents(id,quorum_id,device_id,owner_id,project_ref,role,engine,session_ref)
            values('{AGENT}','{QUORUM}','{DEVICE}','{OWNER}','p','listener','codex','listener-session');
        """, single=True)

    def database(self, name):
        self.cluster.sql('postgres', f'create database {name} template phase_one')
        return name

    def as_owner(self):
        return f"select set_config('request.jwt.claim.sub','{OWNER}',true);"

    def test_suites_pass_on_real_postgresql(self):
        db = self.database('suites')
        self.cluster.sql(db, 'begin;' + release(PHASE_ONE) + (ROOT / 'tests/quorum_hash_only_before_backend.sql').read_text()
                         + (MIGRATIONS / MIGRATION).read_text() + release(CURRENT)
                         + (ROOT / 'tests/quorum_hash_only_backend.sql').read_text() + 'rollback;')

    def test_it_applies_once_and_a_second_application_changes_nothing(self):
        db = self.database('reapplied')
        self.cluster.sql(db, (MIGRATIONS / MIGRATION).read_text(), single=True)
        functions = """select string_agg(p.oid::regprocedure::text||':'||md5(p.prosrc),',' order by p.oid::regprocedure::text)
          from pg_proc p join pg_namespace n on n.oid=p.pronamespace where n.nspname in ('public','quorum_private');"""
        before = self.cluster.sql(db, functions)
        with self.assertRaisesRegex(RuntimeError, 'hash_only_release_present'):
            self.cluster.sql(db, (MIGRATIONS / MIGRATION).read_text(), single=True)
        self.assertEqual(self.cluster.sql(db, functions), before)
        out = self.cluster.sql(db, 'begin;' + self.as_owner() + 'select public.session_requirements(); rollback;')
        self.assertEqual(json.loads(out.strip().splitlines()[-1]), CURRENT)

    def test_a_phase_one_heartbeat_that_commits_during_the_migration_leaves_no_number(self):
        db = self.database('in_flight')
        self.cluster.sql(db, 'begin;' + self.as_owner() + f"select public.session_heartbeat('{AGENT}','listener-session',"
                         f"gen_random_uuid(),2,'0.3.0'); commit;")
        # A later heartbeat holds the health row: the migration's clearing waits for it and then reads its write.
        heartbeat = self.cluster.session(db, 'old_heartbeat')
        heartbeat.send('begin;' + self.as_owner() + f"select public.session_heartbeat('{AGENT}','listener-session',"
                       f"gen_random_uuid(),2,'0.3.0');")
        wait_statement(self.cluster, db, heartbeat.label, 'session_heartbeat',
                       lambda row: row['state'] == 'idle in transaction', 'held phase-one heartbeat')
        migration = self.cluster.session(db, 'migration')
        migration.send('begin;' + (MIGRATIONS / MIGRATION).read_text() + 'commit;')
        held = self.cluster.sql(db, f"select pid from pg_stat_activity where application_name='{heartbeat.label}'").strip()
        wait_statement(self.cluster, db, migration.label, 'session_health',
                       lambda row: row['wait_event_type'] == 'Lock' and int(held) in row['blocking'],
                       'the migration waits for the held health row')
        heartbeat.send('commit;')
        self.assertEqual(heartbeat.finish()[0], 0)
        code, _, error = migration.finish()
        self.assertEqual(code, 0, error)
        self.assertEqual(self.cluster.sql(db, 'select count(*) from quorum_private.session_health '
                                              'where skill_version is not null;').strip(), '0')
        # After it, the copy that still sends the number is heard, and so is one that sends none.
        out = self.cluster.sql(db, 'begin;' + self.as_owner()
                               + f"select public.session_heartbeat('{AGENT}','listener-session',gen_random_uuid(),2,'0.3.0');"
                               + f"select public.session_heartbeat(p_agent_id=>'{AGENT}',p_session_ref=>'listener-session',"
                                 f"p_connection_id=>gen_random_uuid(),p_protocol_version=>2); commit;")
        beats = [json.loads(line) for line in out.splitlines() if line.startswith('{')]
        self.assertEqual(len(beats), 2)
        for beat in beats:
            self.assertNotIn('code', beat)
            self.assertEqual(beat['required'], CURRENT)


if __name__ == '__main__':
    unittest.main()
