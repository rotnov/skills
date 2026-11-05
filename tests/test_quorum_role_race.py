"""Concurrent role claims on an opt-in, network-none PostgreSQL 17 container (#105).

QUORUM_ROLE_DOCKER=1 uses only the existing local image and removes its own
container. No real service configuration, credentials or client process is read.
"""
import os
from pathlib import Path
import unittest

from test_quorum_close_race import Cluster
from quorum_update_sql_support import wait_statement

ROOT = Path(__file__).resolve().parents[1]
MIGRATION = '20261105140000_role_assignment_and_owner_retire.sql'


def uid(number):
    return f'{number:08x}-2222-4222-8222-222222222222'


OWNER, ALICE, QUORUM, OWNER_DEVICE, ALICE_DEVICE = uid(1), uid(2), uid(10), uid(30), uid(31)


@unittest.skipUnless(os.environ.get('QUORUM_ROLE_DOCKER') == '1', 'opt-in disposable role-claim races')
class RoleClaimRaceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.cluster = Cluster().__enter__()
        cls.addClassCleanup(cls.cluster.__exit__)
        cls.cluster.sql('postgres', 'create database role_races')
        setup = (ROOT / 'tests/quorum_backend_fixture.sql').read_text()
        setup += (ROOT / 'skills/quorum/references/server-bootstrap.sql').read_text()
        names = sorted((ROOT / 'skills/quorum/supabase/migrations').glob('*.sql'))
        # pgvector extensions are optional and need another image; the role file does not depend on them.
        setup += ''.join(path.read_text() for path in names if path.name <= MIGRATION
                         and not path.name.endswith(('_project_knowledge.sql', '_llm_wiki.sql')))
        cls.cluster.sql('role_races', setup, single=True)
        cls.cluster.sql('role_races', f"""
          insert into auth.users(id,raw_user_meta_data) values('{OWNER}','{{}}'),('{ALICE}','{{}}');
          insert into auth.identities(user_id,provider,provider_id,identity_data)
            values('{OWNER}','github','9000000000201','{{}}'),('{ALICE}','github','9000000000202','{{}}');
          insert into quorum_private.quorums(id,slug,owner_id) values('{QUORUM}','role-race','{OWNER}');
          insert into quorum_private.memberships(quorum_id,user_id,role)
            values('{QUORUM}','{OWNER}','owner'),('{QUORUM}','{ALICE}','member');
          insert into quorum_private.devices(id,owner_id,label)
            values('{OWNER_DEVICE}','{OWNER}','owner'),('{ALICE_DEVICE}','{ALICE}','alice');
        """, single=True)

    def join(self, who, device, role, session, request, supersede=False):
        return (f"select set_config('request.jwt.claim.sub','{who}',true);"
                f"select public.agent_join('{QUORUM}','{device}','p','{role}','claude','{session}','{request}',"
                f"{'true' if supersede else 'false'});")

    def live(self, role):
        return int(self.cluster.sql('role_races', f"select count(*) from quorum_private.agents "
                                                  f"where quorum_id='{QUORUM}' and role='{role}' and not revoked").strip())

    def race(self, first, second, statement='agent_join'):
        """`first` takes its lock and holds its transaction open; `second` must wait, then see it."""
        blocker = self.cluster.session('role_races', 'role_blocker')
        blocker.send('begin;' + first)
        wait_statement(self.cluster, 'role_races', blocker.label, statement,
                       lambda row: row['state'] == 'idle in transaction', 'held role claim', advisory=True)
        worker = self.cluster.session('role_races', 'role_worker')
        worker.send('begin;' + second + 'commit;')
        held = self.cluster.sql('role_races', "select pid from pg_stat_activity where application_name="
                                              f"'{blocker.label}'").strip()
        wait_statement(self.cluster, 'role_races', worker.label, statement,
                       lambda row: row['wait_event_type'] == 'Lock' and int(held) in row['blocking'],
                       'second claim waits for the first')
        blocker.send('commit;')
        self.assertEqual(blocker.finish()[0], 0)
        return worker.finish()

    def test_concurrent_own_claim_waits_then_refuses(self):
        code, out, error = self.race(self.join(ALICE, ALICE_DEVICE, 'reviewer', 's1', 'r1'),
                                     self.join(ALICE, ALICE_DEVICE, 'reviewer', 's2', 'r2'))
        self.assertNotEqual(code, 0, out)
        self.assertIn('role_held_by_own_agent', error)
        self.assertEqual(self.live('reviewer'), 1)

    def test_concurrent_other_identity_claim_waits_then_refuses(self):
        code, out, error = self.race(self.join(OWNER, OWNER_DEVICE, 'builder', 's3', 'r3'),
                                     self.join(ALICE, ALICE_DEVICE, 'builder', 's4', 'r4', supersede=True))
        self.assertNotEqual(code, 0, out)
        self.assertIn('role_held_by_other_identity', error)
        self.assertEqual(self.live('builder'), 1)

    def test_concurrent_supersede_waits_then_retires_the_committed_holder(self):
        code, out, error = self.race(self.join(ALICE, ALICE_DEVICE, 'tester', 's5', 'r5'),
                                     self.join(ALICE, ALICE_DEVICE, 'tester', 's6', 'r6', supersede=True))
        self.assertEqual(code, 0, error)
        self.assertIn('"superseded"', out)
        self.assertEqual(self.live('tester'), 1)
        self.assertEqual(self.cluster.sql('role_races', "select session_ref from quorum_private.agents "
                                                        "where role='tester' and not revoked").strip(), 's6')

    def invite(self, request, role=None):
        extra = f",'{role}'" if role else ''
        return (f"select set_config('request.jwt.claim.sub','{OWNER}',true);"
                f"select public.invite_create_id('{QUORUM}','9000000000203','{request}'{extra});")

    def test_concurrent_identical_invite_waits_then_refuses_a_different_assignment(self):
        code, out, error = self.race(self.invite('same-issue'), self.invite('same-issue', 'reviewer'),
                                     statement='invite_create_id')
        self.assertNotEqual(code, 0, out)
        self.assertIn('idempotency_conflict', error)
        # The issued invitation keeps the first request's (empty) assignment.
        self.assertEqual(self.cluster.sql('role_races', "select count(*)||'|'||count(assigned_role) from "
                                                        "quorum_private.id_invitations where target_github_id='9000000000203' and assigned_role is distinct from 'writer'"
                                                        ).strip(), '1|0')

    def test_concurrent_identical_invite_with_the_same_assignment_replays(self):
        code, out, error = self.race(self.invite('same-role', 'writer'), self.invite('same-role', 'writer'),
                                     statement='invite_create_id')
        self.assertEqual(code, 0, error)
        self.assertEqual(self.cluster.sql('role_races', "select count(*) from quorum_private.id_invitations "
                                                        "where assigned_role='writer'").strip(), '1')


if __name__ == '__main__':
    unittest.main()
