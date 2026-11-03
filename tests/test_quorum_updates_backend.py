"""Opt-in actual SQL acceptance; local image only, network-none disposable PostgreSQL."""
import os
import json
from pathlib import Path
import unittest

from test_quorum_close_race import Cluster
from quorum_update_sql_support import wait_statement

ROOT = Path(__file__).resolve().parents[1]
MIGRATIONS = ROOT/'skills/quorum/supabase/migrations'
UPDATE = '20261030140000_owner_update_maintenance.sql'


@unittest.skipUnless(os.environ.get('QUORUM_UPDATE_DOCKER') == '1', 'set QUORUM_UPDATE_DOCKER=1 for local SQL acceptance')
class UpdateBackendTests(unittest.TestCase):
    def test_actual_sql_all_quorum_fanout_retry_receipt_and_report_authority(self):
        self.assertTrue((MIGRATIONS/UPDATE).is_file(), 'the all-quorum maintenance protocol is missing')
        with Cluster() as cluster:
            cluster.sql('postgres', 'create database updates')
            script = (ROOT/'tests/quorum_backend_fixture.sql').read_text()
            script += (ROOT/'skills/quorum/references/server-bootstrap.sql').read_text()
            for path in sorted(MIGRATIONS.glob('*.sql')):
                if path.name <= UPDATE: script += path.read_text()
            cluster.sql('updates', script, single=True)
            result = cluster.sql('updates', (ROOT/'tests/quorum_updates_backend.sql').read_text(), single=True)
            self.assertIn('updates:ok', result)
            actor = json.loads(cluster.sql('updates', "select json_build_object('agent_id',id,'quorum_id',quorum_id,"
                                          "'session_ref',session_ref,'owner_id',owner_id) from quorum_private.agents "
                                          "where role='initiator'").strip())
            cluster.sql('updates', 'update quorum_private.memberships set revoked=false')
            self.concurrent_retries(cluster, actor)
        self.assertTrue(cluster.removed)

    def concurrent_retries(self, cluster, actor):
        key = "hashtextextended('update-request:" + actor['agent_id'] + ":parallel',0)"
        blocker = cluster.session('updates', 'update_blocker')
        blocker.send('begin; select pg_advisory_xact_lock(' + key + ');')
        wait_statement(cluster, 'updates', blocker.label, 'pg_advisory_xact_lock',
                       lambda row:row['state']=='idle in transaction', 'held request lock', advisory=True)
        workers = [cluster.session('updates', 'update_retry') for _ in range(2)]
        sql = ("begin; select set_config('request.jwt.claim.sub','"+actor['owner_id']+"',true); "
               "select public.session_update_request('"+actor['agent_id']+"','"+actor['session_ref']+"','"
               +actor['quorum_id']+"',2,'0.3.0',repeat('c',64),'parallel'); commit;")
        for worker in workers:
            worker.send(sql)
            wait_statement(cluster, 'updates', worker.label, 'public.session_update_request',
                           lambda row:row['wait_event_type']=='Lock', 'request fence')
        blocker.send('rollback;')
        blocker.finish()
        answers = []
        for worker in workers:
            code, out, error = worker.finish()
            self.assertEqual(code, 0, error)
            answers.append(json.loads(next(line for line in out.splitlines() if line.startswith('{'))))
        self.assertEqual(answers[0], answers[1])
        self.assertEqual(answers[0]['queued'], 136)
        count = cluster.sql('updates', "select count(*) from quorum_private.update_notices n join "
                            "quorum_private.update_targets t on t.id=n.target_id where t.content_sha256=repeat('c',64)")
        self.assertEqual(count.strip(), '136')


if __name__ == '__main__': unittest.main()
