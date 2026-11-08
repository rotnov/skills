"""Presence without per-peer Hello on an opt-in, network-none PostgreSQL 17 container (#196).

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
MIGRATION = '20261108140000_presence_without_hello.sql'
OPTIONAL = ('_project_knowledge.sql', '_llm_wiki.sql', '_knowledge_member_default_role.sql')
CURRENT = json.loads((ROOT / 'skills/quorum/assets/release.json').read_text())


def uid(number):
    return f'{number:08x}-1961-4196-8196-196196196196'


OWNER, QUORUM, DEVICE, NEWCOMER, ALPHA, BETA = uid(1), uid(10), uid(20), uid(30), uid(31), uid(32)


def release(value):
    return f"select set_config('quorum_test.release','{json.dumps(value)}',true);"


@unittest.skipUnless(os.environ.get('QUORUM_RELEASE_DOCKER') == '1', 'opt-in disposable presence release')
class PresenceWithoutHelloTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.cluster = Cluster().__enter__()
        cls.addClassCleanup(cls.cluster.__exit__)
        cls.cluster.sql('postgres', 'create database hash_only')
        setup = (ROOT / 'tests/quorum_backend_fixture.sql').read_text()
        setup += (ROOT / 'skills/quorum/references/server-bootstrap.sql').read_text()
        # pgvector extensions are optional and need another image; this release does not depend on them.
        setup += ''.join(path.read_text() for path in sorted(MIGRATIONS.glob('*.sql'))
                         if path.name < MIGRATION and not path.name.endswith(OPTIONAL))
        cls.cluster.sql('hash_only', setup, single=True)
        cls.cluster.sql('hash_only', f"""
          insert into auth.users(id,raw_user_meta_data) values('{OWNER}','{{}}');
          insert into auth.identities(user_id,provider,provider_id,identity_data)
            values('{OWNER}','github','9000000000197','{{}}');
          insert into quorum_private.quorums(id,slug,owner_id) values('{QUORUM}','presence-race','{OWNER}');
          insert into quorum_private.memberships(quorum_id,user_id,role) values('{QUORUM}','{OWNER}','owner');
          insert into quorum_private.devices(id,owner_id,label) values('{DEVICE}','{OWNER}','owner');
          insert into quorum_private.agents(id,quorum_id,device_id,owner_id,project_ref,role,engine,session_ref) values
            ('{NEWCOMER}','{QUORUM}','{DEVICE}','{OWNER}','p','newcomer','codex','newcomer-session'),
            ('{ALPHA}','{QUORUM}','{DEVICE}','{OWNER}','p','alpha','claude','alpha-session'),
            ('{BETA}','{QUORUM}','{DEVICE}','{OWNER}','p','beta','grok','beta-session');
        """, single=True)

    def database(self, name):
        self.cluster.sql('postgres', f'create database {name} template hash_only')
        return name

    def as_owner(self):
        return f"select set_config('request.jwt.claim.sub','{OWNER}',true);"

    def heartbeat(self):
        return f"select public.session_heartbeat('{NEWCOMER}','newcomer-session',gen_random_uuid(),2);"

    def peer_messages(self, db):
        return self.cluster.sql(db, 'select count(*) from quorum_private.messages where recipient_id<>sender_id;').strip()

    def test_suite_passes_on_real_postgresql(self):
        db = self.database('suite')
        self.cluster.sql(db, 'begin;' + (MIGRATIONS / MIGRATION).read_text() + release(CURRENT)
                         + (ROOT / 'tests/quorum_presence_backend.sql').read_text() + 'rollback;')

    def test_it_applies_once_and_a_second_application_changes_nothing(self):
        db = self.database('reapplied')
        self.cluster.sql(db, (MIGRATIONS / MIGRATION).read_text(), single=True)
        functions = """select string_agg(p.oid::regprocedure::text||':'||md5(p.prosrc),',' order by p.oid::regprocedure::text)
          from pg_proc p join pg_namespace n on n.oid=p.pronamespace where n.nspname in ('public','quorum_private');"""
        before = self.cluster.sql(db, functions)
        with self.assertRaisesRegex(RuntimeError, 'presence_without_hello_present'):
            self.cluster.sql(db, (MIGRATIONS / MIGRATION).read_text(), single=True)
        self.assertEqual(self.cluster.sql(db, functions), before)

    def test_concurrent_new_connections_of_one_agent_wait_in_order_and_greet_no_one(self):
        db = self.database('concurrent')
        self.cluster.sql(db, (MIGRATIONS / MIGRATION).read_text(), single=True)
        first = self.cluster.session(db, 'first_connection')
        first.send('begin;' + self.as_owner() + self.heartbeat())
        wait_statement(self.cluster, db, first.label, 'session_heartbeat',
                       lambda row: row['state'] == 'idle in transaction', 'held first connection')
        second = self.cluster.session(db, 'second_connection')
        second.send('begin;' + self.as_owner() + self.heartbeat() + 'commit;')
        held = self.cluster.sql(db, f"select pid from pg_stat_activity where application_name='{first.label}'").strip()
        # The second connection waits on the sender's peer rows, before any health or connection lock.
        wait_statement(self.cluster, db, second.label, 'session_heartbeat',
                       lambda row: row['wait_event_type'] == 'Lock' and int(held) in row['blocking'],
                       'the second connection waits for the first')
        first.send('commit;')
        for session in (first, second):
            code, out, error = session.finish()
            self.assertEqual(code, 0, error)
            beat = json.loads([line for line in out.splitlines() if line.startswith('{')][-1])
            self.assertEqual((beat['new_connection'], beat['hello']['peers'], beat['hello']['skipped']), (True, 2, 2))
        self.assertEqual(self.peer_messages(db), '0')
        # Each connection still got its own quiet roster notice, and nothing else was written as a message.
        self.assertEqual(self.cluster.sql(db, 'select count(*) from quorum_private.messages;').strip(), '2')


if __name__ == '__main__':
    unittest.main()
