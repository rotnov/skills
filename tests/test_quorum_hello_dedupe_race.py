"""Reconnect Hello dedupe on an opt-in, network-none PostgreSQL 17 container (#126).

QUORUM_DEDUPE_DOCKER=1 uses only the existing local image and removes its own
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
# The dedupe races run on the newest release that replaces its Hello wrapper.
MIGRATION = '20261106150000_hello_sent_skipped.sql'
# What every server required before the hash-only release (20261108120000), which this file predates.
RELEASE = {'protocol_version': 2, 'skill_version': '0.3.0'}


def uid(number):
    return f'{number:08x}-4444-4444-8444-444444444444'


OWNER, QUORUM, DEVICE, SENDER, PEER = uid(1), uid(10), uid(20), uid(30), uid(31)


@unittest.skipUnless(os.environ.get('QUORUM_DEDUPE_DOCKER') == '1', 'opt-in disposable Hello dedupe races')
class HelloDedupeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.cluster = Cluster().__enter__()
        cls.addClassCleanup(cls.cluster.__exit__)
        cls.cluster.sql('postgres', 'create database hello_dedupe')
        setup = (ROOT / 'tests/quorum_backend_fixture.sql').read_text()
        setup += (ROOT / 'skills/quorum/references/server-bootstrap.sql').read_text()
        names = sorted((ROOT / 'skills/quorum/supabase/migrations').glob('*.sql'))
        # pgvector knowledge (and its default-role release) needs another image; the dedupe file does not depend on it.
        setup += ''.join(path.read_text() for path in names if path.name <= MIGRATION
                         and not path.name.endswith(('_project_knowledge.sql', '_llm_wiki.sql',
                                                    '_knowledge_member_default_role.sql')))
        cls.cluster.sql('hello_dedupe', setup, single=True)
        cls.cluster.sql('hello_dedupe', f"""
          insert into auth.users(id,raw_user_meta_data) values('{OWNER}','{{}}');
          insert into auth.identities(user_id,provider,provider_id,identity_data)
            values('{OWNER}','github','9000000000326','{{}}');
          insert into quorum_private.quorums(id,slug,owner_id) values('{QUORUM}','dedupe-race','{OWNER}');
          insert into quorum_private.memberships(quorum_id,user_id,role) values('{QUORUM}','{OWNER}','owner');
          insert into quorum_private.devices(id,owner_id,label) values('{DEVICE}','{OWNER}','owner');
          insert into quorum_private.agents(id,quorum_id,device_id,owner_id,project_ref,role,engine,session_ref)
            values('{SENDER}','{QUORUM}','{DEVICE}','{OWNER}','p','flaky','codex','sender-session'),
                  ('{PEER}','{QUORUM}','{DEVICE}','{OWNER}','p','steady','claude','peer-session');
        """, single=True)

    def heartbeat(self, connection):
        return (f"select set_config('request.jwt.claim.sub','{OWNER}',true);"
                f"select public.session_heartbeat('{SENDER}','sender-session','{connection}',"
                f"{RELEASE['protocol_version']},'{RELEASE['skill_version']}');")

    def hellos(self):
        return int(self.cluster.sql('hello_dedupe', f"select count(*) from quorum_private.messages "
                                                    f"where sender_id='{SENDER}' and recipient_id='{PEER}'").strip())

    def test_suites_pass_on_real_postgresql(self):
        release = json.dumps({'protocol_version': RELEASE['protocol_version'],
                              'skill_version': RELEASE['skill_version']})
        for name in ('quorum_hello_backend.sql', 'quorum_hello_dedupe_backend.sql'):
            self.cluster.sql('hello_dedupe', f"begin; select set_config('quorum_test.release','{release}',true);"
                             + (ROOT / 'tests' / name).read_text() + 'rollback;')

    def test_concurrent_reconnects_serialize_on_health_and_greet_once(self):
        # Two new connections of one sender at once: the second waits on the health row lock the
        # first holds, then reads the first's committed Hello and sends none of its own.
        self.cluster.sql('hello_dedupe', f"delete from quorum_private.session_hello_sent where sender_id='{SENDER}'")
        before = self.hellos()
        first = self.cluster.session('hello_dedupe', 'dedupe_first')
        first.send('begin;' + self.heartbeat(uid(40)))
        wait_statement(self.cluster, 'hello_dedupe', first.label, 'session_heartbeat',
                       lambda row: row['state'] == 'idle in transaction', 'held reconnect')
        second = self.cluster.session('hello_dedupe', 'dedupe_second')
        second.send('begin;' + self.heartbeat(uid(41)) + 'commit;')
        held = self.cluster.sql('hello_dedupe', "select pid from pg_stat_activity where application_name="
                                                f"'{first.label}'").strip()
        wait_statement(self.cluster, 'hello_dedupe', second.label, 'session_heartbeat',
                       lambda row: row['wait_event_type'] == 'Lock' and int(held) in row['blocking'],
                       'second reconnect waits for the first')
        first.send('commit;')
        self.assertEqual(first.finish()[0], 0)
        code, out, error = second.finish()
        self.assertEqual(code, 0, error)
        self.assertEqual(self.hellos(), before + 1)
        # Both connections still report the peer; neither result carries the internal key.
        self.assertIn('"peers": 1', out)
        self.assertNotIn('hello_skipped', out)

    def test_a_reconnect_after_an_absence_announces_again(self):
        self.cluster.sql('hello_dedupe', 'begin;' + self.heartbeat(uid(50)) + 'commit;')
        before = self.hellos()
        self.cluster.sql('hello_dedupe', f"update quorum_private.session_health set last_seen=now()-interval "
                                         f"'2 minutes' where agent_id='{SENDER}'")
        self.cluster.sql('hello_dedupe', 'begin;' + self.heartbeat(uid(51)) + 'commit;')
        self.assertEqual(self.hellos(), before + 1)
        # Committed time passes between these statements: an immediate reconnect is deduplicated.
        self.cluster.sql('hello_dedupe', 'begin;' + self.heartbeat(uid(52)) + 'commit;')
        self.assertEqual(self.hellos(), before + 1)


if __name__ == '__main__':
    unittest.main()
