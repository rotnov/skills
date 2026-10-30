"""Forced SQL waits and revocation serialization; disposable local PostgreSQL only."""
import json
import os
from pathlib import Path
import unittest

from test_quorum_close_race import Cluster
from quorum_update_sql_support import wait_statement

ROOT = Path(__file__).resolve().parents[1]


@unittest.skipUnless(os.environ.get('QUORUM_UPDATE_DOCKER') == '1', 'opt-in disposable update SQL races')
class UpdateAuthorityRaceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.cluster = Cluster().__enter__()
        cls.addClassCleanup(cls.cluster.__exit__)
        cls.cluster.sql('postgres', 'create database update_races')
        setup = (ROOT/'tests/quorum_backend_fixture.sql').read_text()
        setup += (ROOT/'skills/quorum/references/server-bootstrap.sql').read_text()
        setup += ''.join(path.read_text() for path in sorted((ROOT/'skills/quorum/supabase/migrations').glob('*.sql'))
                         if path.name <= '20261030141000_member_task_authority.sql')
        cls.cluster.sql('update_races', setup, single=True)
        cls.cluster.sql('update_races', (ROOT/'tests/quorum_updates_backend.sql').read_text(), single=True)
        cls.actor = json.loads(cls.cluster.sql('update_races', "select json_build_object('agent',id,'quorum',quorum_id,"
            "'session',session_ref,'owner',owner_id,'device',device_id) from quorum_private.agents where role='initiator'").strip())
        cls.recipient = json.loads(cls.cluster.sql('update_races', "select json_build_object('agent',a.id,'quorum',a.quorum_id,"
            "'session',a.session_ref,'owner',a.owner_id,'notice',n.id,'device',a.device_id) from quorum_private.update_notices n "
            "join quorum_private.update_targets t on t.id=n.target_id join quorum_private.agents a on a.id=n.recipient_id "
            "where t.content_sha256=repeat('a',64) limit 1").strip())

    def setUp(self):
        self.cluster.sql('update_races', 'update quorum_private.agents set revoked=false;'
                         'update quorum_private.devices set revoked=false;update quorum_private.memberships set revoked=false;')

    def request(self, actor, request, digest='a'*64):
        return (f"select public.session_update_request('{actor['agent']}','{actor['session']}','{actor['quorum']}',"
                f"2,'0.3.0','{digest}','{request}');")

    def snapshot(self):
        return self.cluster.sql('update_races', 'select json_build_array((select count(*) from quorum_private.messages),'
            '(select count(*) from quorum_private.update_operations),(select count(*) from quorum_private.update_reports), '
            '(select count(*) from quorum_private.session_receipts), '
            '(select count(*) from quorum_private.update_notices where received_at is not null))').strip()

    def revoke(self, actor, kind):
        table, condition = {'agent': ('agents', f"id='{actor['agent']}'"),
                            'device': ('devices', f"id='{actor['device']}'"),
                            'member': ('memberships', f"quorum_id='{actor['quorum']}' and user_id='{actor['owner']}'")}[kind]
        return f'update quorum_private.{table} set revoked=true where {condition};'

    def denied_after_wait(self, actor, lock, rpc, kind):
        before = self.snapshot()
        blocker = self.cluster.session('update_races', 'update_auth_blocker')
        blocker.send('begin;' + lock)
        held = wait_statement(self.cluster, 'update_races', blocker.label, lock.rstrip(';'),
                              lambda row: row['state'] == 'idle in transaction', 'held protocol lock',
                              advisory='pg_advisory_xact_lock' in lock)
        worker = self.cluster.session('update_races', 'update_auth_worker')
        worker.send(f"begin;select set_config('request.jwt.claim.sub','{actor['owner']}',true);" + rpc + 'commit;')
        wait_statement(self.cluster, 'update_races', worker.label, rpc.split('(')[0],
                       lambda row: row['wait_event_type'] == 'Lock' and held['pid'] in row['blocking'],
                       'protocol wait after initial auth')
        self.cluster.sql('update_races', self.revoke(actor, kind))
        blocker.send('rollback;')
        blocker.finish()
        code, out, error = worker.finish()
        self.assertNotEqual(code, 0, 'completed revocation must deny the waiting RPC: ' + out)
        self.assertRegex(error, 'agent_denied|membership_required|session_binding_denied')
        self.assertEqual(self.snapshot(), before, 'denied RPC must leave no fanout/report effects')

    def test_cached_request_rechecks_agent_after_request_lock_wait(self):
        actor = self.actor
        key = f"hashtextextended('update-request:{actor['agent']}:first',0)"
        self.denied_after_wait(actor, f'select pg_advisory_xact_lock({key});', self.request(actor, 'first'), 'agent')

    def test_new_target_rechecks_device_after_target_lock_wait(self):
        actor = self.actor
        key = f"hashtextextended('update-target:{actor['quorum']}:{'d'*64}',0)"
        self.denied_after_wait(actor, f'select pg_advisory_xact_lock({key});',
                               self.request(actor, 'target-wait', 'd'*64), 'device')

    def test_notice_report_rechecks_member_after_notice_row_lock_wait(self):
        actor = self.recipient
        rpc = (f"select public.session_update_report('{actor['agent']}','{actor['session']}','{actor['quorum']}',"
               f"'{actor['notice']}','refreshed',jsonb_build_object('protocol_version',2,'skill_version','0.3.0',"
               "'content_sha256',repeat('a',64)),'wait-report');")
        self.denied_after_wait(actor, f"select id from quorum_private.update_notices where id='{actor['notice']}' for update;",
                               rpc, 'member')

    def test_maintenance_receipt_rechecks_after_legacy_health_write_wait(self):
        actor = json.loads(self.cluster.sql('update_races', "select json_build_object('agent',a.id,'quorum',a.quorum_id,"
            "'session',a.session_ref,'owner',a.owner_id,'notice',n.id,'device',a.device_id,'message',n.message_id) "
            "from quorum_private.update_notices n join quorum_private.agents a on a.id=n.recipient_id "
            "where n.received_at is null limit 1").strip())
        self.cluster.sql('update_races', f"insert into quorum_private.session_health(agent_id,last_seen) "
                         f"values('{actor['agent']}',now()) on conflict do nothing;")
        rpc = f"select public.session_ack('{actor['agent']}','{actor['session']}','{actor['message']}');"
        self.denied_after_wait(actor, f"select agent_id from quorum_private.session_health "
                               f"where agent_id='{actor['agent']}' for update;", rpc, 'agent')

    def test_completed_rpc_serializes_all_current_authority_rows_until_commit(self):
        for index, kind in enumerate(('agent', 'device', 'member')):
            with self.subTest(kind=kind):
                self.setUp()
                actor = self.actor
                worker = self.cluster.session('update_races', 'update_auth_effect')
                worker.send(f"begin;select set_config('request.jwt.claim.sub','{actor['owner']}',true);" +
                            self.request(actor, 'fenced-' + kind, f'{index+100:064x}'))
                wait_statement(self.cluster, 'update_races', worker.label, 'public.session_update_request',
                               lambda row: row['state'] == 'idle in transaction', 'actual effect ready to commit')
                revoker = self.cluster.session('update_races', 'update_auth_revoke')
                revoker.send('begin;' + self.revoke(actor, kind))
                observed = wait_statement(self.cluster, 'update_races', revoker.label, 'update quorum_private.',
                                          lambda row: row['state'] == 'idle in transaction' or row['wait_event_type'] == 'Lock',
                                          'actual revoke serialized with effect')
                worker.send('commit;')
                worker_code, _, worker_error = worker.finish()
                revoker.send('commit;')
                revoke_code, _, revoke_error = revoker.finish()
                self.assertEqual((worker_code, revoke_code), (0, 0), worker_error + revoke_error)
                self.assertEqual(observed['wait_event_type'], 'Lock', 'revocation completed before the operation committed')


if __name__ == '__main__': unittest.main()
