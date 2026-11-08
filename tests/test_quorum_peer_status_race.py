"""Heartbeat peer status on an opt-in, network-none PostgreSQL 17 container (#113, #104).

QUORUM_STATUS_DOCKER=1 uses only the existing local image and removes its own
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
MIGRATION = '20261105160000_peer_status_visibility.sql'
# What every server required before the hash-only release (20261108120000), which this file predates.
RELEASE = {'protocol_version': 2, 'skill_version': '0.3.0'}


def uid(number):
    return f'{number:08x}-3333-4333-8333-333333333333'


OWNER, QUORUM, DEVICE, VIEWER, WORKER = uid(1), uid(10), uid(20), uid(30), uid(31)
CONNECTION, RUN = uid(40), uid(50)


@unittest.skipUnless(os.environ.get('QUORUM_STATUS_DOCKER') == '1', 'opt-in disposable peer status races')
class PeerStatusTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.cluster = Cluster().__enter__()
        cls.addClassCleanup(cls.cluster.__exit__)
        cls.cluster.sql('postgres', 'create database peer_status')
        setup = (ROOT / 'tests/quorum_backend_fixture.sql').read_text()
        setup += (ROOT / 'skills/quorum/references/server-bootstrap.sql').read_text()
        names = sorted((ROOT / 'skills/quorum/supabase/migrations').glob('*.sql'))
        # pgvector extensions are optional and need another image; the status file does not depend on them.
        setup += ''.join(path.read_text() for path in names if path.name <= MIGRATION
                         and not path.name.endswith(('_project_knowledge.sql', '_llm_wiki.sql')))
        cls.cluster.sql('peer_status', setup, single=True)
        cls.cluster.sql('peer_status', f"""
          insert into auth.users(id,raw_user_meta_data) values('{OWNER}','{{}}');
          insert into auth.identities(user_id,provider,provider_id,identity_data)
            values('{OWNER}','github','9000000000301','{{}}');
          insert into quorum_private.quorums(id,slug,owner_id) values('{QUORUM}','peer-status','{OWNER}');
          insert into quorum_private.memberships(quorum_id,user_id,role) values('{QUORUM}','{OWNER}','owner');
          insert into quorum_private.devices(id,owner_id,label) values('{DEVICE}','{OWNER}','owner');
          insert into quorum_private.agents(id,quorum_id,device_id,owner_id,project_ref,role,engine,session_ref)
            values('{VIEWER}','{QUORUM}','{DEVICE}','{OWNER}','p','viewer','claude','viewer-session'),
                  ('{WORKER}','{QUORUM}','{DEVICE}','{OWNER}','p','worker','codex','worker-session');
        """, single=True)

    def as_owner(self):
        return f"select set_config('request.jwt.claim.sub','{OWNER}',true);"

    def heartbeat(self, count):
        return (self.as_owner() + f"select public.session_heartbeat('{WORKER}','worker-session','{CONNECTION}',"
                f"{RELEASE['protocol_version']},'{RELEASE['skill_version']}',{count},null,'{RUN}',now());")

    def worker_view(self):
        out = self.cluster.sql('peer_status', 'begin;' + self.as_owner()
                               + f"select e.value from jsonb_array_elements(public.session_peers('{VIEWER}',"
                                 f"'viewer-session')) e where e.value->>'agent_id'='{WORKER}'; commit;")
        return json.loads(next(line for line in out.splitlines() if line.startswith('{')))

    def test_suites_pass_on_real_postgresql(self):
        release = json.dumps({'protocol_version': RELEASE['protocol_version'],
                              'skill_version': RELEASE['skill_version']})
        for name in ('quorum_hello_backend.sql', 'quorum_peer_status_backend.sql'):
            self.cluster.sql('peer_status', f"begin; select set_config('quorum_test.release','{release}',true);"
                             + (ROOT / 'tests' / name).read_text() + 'rollback;')

    def test_concurrent_heartbeats_serialize_on_health_then_the_last_status_wins(self):
        self.cluster.sql('peer_status', 'begin;' + self.heartbeat(0) + 'commit;')
        blocker = self.cluster.session('peer_status', 'status_blocker')
        blocker.send('begin;' + self.heartbeat(3))
        wait_statement(self.cluster, 'peer_status', blocker.label, 'session_heartbeat',
                       lambda row: row['state'] == 'idle in transaction', 'held heartbeat')
        # A reader neither waits nor sees the uncommitted status.
        self.assertEqual(self.worker_view()['awaiting_owner'], 0)
        worker = self.cluster.session('peer_status', 'status_worker')
        worker.send('begin;' + self.heartbeat(5) + 'commit;')
        held = self.cluster.sql('peer_status', "select pid from pg_stat_activity where application_name="
                                               f"'{blocker.label}'").strip()
        wait_statement(self.cluster, 'peer_status', worker.label, 'session_heartbeat',
                       lambda row: row['wait_event_type'] == 'Lock' and int(held) in row['blocking'],
                       'second heartbeat waits for the first')
        blocker.send('commit;')
        self.assertEqual(blocker.finish()[0], 0)
        code, out, error = worker.finish()
        self.assertEqual(code, 0, error)
        view = self.worker_view()
        self.assertEqual((view['availability'], view['awaiting_owner']), ('awaiting_owner', 5))
        self.assertEqual(self.cluster.sql('peer_status', 'select count(*) from quorum_private.session_status').strip(), '1')

    def test_an_invalid_count_rolls_back_without_touching_health(self):
        self.cluster.sql('peer_status', 'begin;' + self.heartbeat(1) + 'commit;')
        before =self.cluster.sql('peer_status', f"select last_seen from quorum_private.session_health "
                                                 f"where agent_id='{WORKER}'").strip()
        with self.assertRaisesRegex(RuntimeError, 'invalid_peer_status'):
            self.cluster.sql('peer_status', 'begin;' + self.heartbeat(33) + 'commit;')
        after = self.cluster.sql('peer_status', f"select last_seen from quorum_private.session_health "
                                                f"where agent_id='{WORKER}'").strip()
        self.assertTrue(before)
        self.assertEqual(before, after)
        self.assertEqual(self.cluster.sql('peer_status', f"select awaiting_owner from quorum_private.session_status "
                                                         f"where agent_id='{WORKER}'").strip(), '1')


if __name__ == '__main__':
    unittest.main()
