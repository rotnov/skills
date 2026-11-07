"""Delivery statuses on an opt-in, network-none PostgreSQL 17 container (#162).

QUORUM_DELIVERY_STATUS_DOCKER=1 uses only the existing local image and removes its own
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
MIGRATION = '20261106170000_delivery_statuses.sql'
RELEASE = json.loads((ROOT / 'skills/quorum/assets/release.json').read_text())
OPTIONAL = ('_project_knowledge.sql', '_llm_wiki.sql', '_knowledge_member_default_role.sql')


def uid(number):
    return f'{number:08x}-3333-4333-8333-333333333333'


OWNER, QUORUM, DEVICE, SENDER, RECIPIENT = uid(1), uid(10), uid(20), uid(30), uid(31)


@unittest.skipUnless(os.environ.get('QUORUM_DELIVERY_STATUS_DOCKER') == '1', 'opt-in disposable delivery status races')
class DeliveryStatusRaces(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.cluster = Cluster().__enter__()
        cls.addClassCleanup(cls.cluster.__exit__)
        cls.cluster.sql('postgres', 'create database delivery_status')
        setup = (ROOT / 'tests/quorum_backend_fixture.sql').read_text()
        setup += (ROOT / 'skills/quorum/references/server-bootstrap.sql').read_text()
        names = sorted((ROOT / 'skills/quorum/supabase/migrations').glob('*.sql'))
        # pgvector extensions are optional and need another image; the statuses do not depend on them.
        setup += ''.join(path.read_text() for path in names if path.name <= MIGRATION
                         and not path.name.endswith(OPTIONAL))
        cls.cluster.sql('delivery_status', setup, single=True)
        cls.cluster.sql('delivery_status', f"""
          insert into auth.users(id,raw_user_meta_data) values('{OWNER}','{{}}');
          insert into auth.identities(user_id,provider,provider_id,identity_data)
            values('{OWNER}','github','9000000000164','{{}}');
          insert into quorum_private.quorums(id,slug,owner_id) values('{QUORUM}','delivery-race','{OWNER}');
          insert into quorum_private.memberships(quorum_id,user_id,role) values('{QUORUM}','{OWNER}','owner');
          insert into quorum_private.devices(id,owner_id,label) values('{DEVICE}','{OWNER}','owner');
          insert into quorum_private.agents(id,quorum_id,device_id,owner_id,project_ref,role,engine,session_ref)
            values('{SENDER}','{QUORUM}','{DEVICE}','{OWNER}','p','asker','claude','sender-session'),
                  ('{RECIPIENT}','{QUORUM}','{DEVICE}','{OWNER}','p','doer','codex','recipient-session');
        """, single=True)

    def as_owner(self):
        return f"select set_config('request.jwt.claim.sub','{OWNER}',true);"

    def message(self, label):
        out = self.cluster.sql('delivery_status', 'begin;' + self.as_owner()
                               + f"select public.message_send('{QUORUM}','{SENDER}',gen_random_uuid(),'{RECIPIENT}',"
                                 f"null,'body','{label}','normal')->>'message_id'; commit;")
        # The first line is the set_config result; the message ID comes last.
        return out.strip().splitlines()[-1]

    def mark(self, message_id, status, proof='null'):
        return (self.as_owner() + f"select public.session_delivery_mark('{RECIPIENT}','recipient-session',"
                f"'{message_id}','{status}',null,{proof});")

    def feed(self, after=0):
        out = self.cluster.sql('delivery_status', 'begin;' + self.as_owner()
                               + f"select public.session_delivery_changes('{SENDER}','sender-session',{after}); commit;")
        return json.loads(next(line for line in out.splitlines() if line.startswith('{')))

    def hold_then_race(self, first, second, label, *, statement='session_delivery_mark'):
        """`first` commits only after `second` is seen waiting behind it; both then finish."""
        blocker = self.cluster.session('delivery_status', label + '_first')
        blocker.send('begin;' + first)
        wait_statement(self.cluster, 'delivery_status', blocker.label, 'session_delivery_mark',
                       lambda row: row['state'] == 'idle in transaction', 'held status')
        before = self.feed(self.after)
        follower = self.cluster.session('delivery_status', label + '_second')
        follower.send('begin;' + second + 'commit;')
        held = self.cluster.sql('delivery_status', 'select pid from pg_stat_activity where application_name='
                                                   f"'{blocker.label}'").strip()
        wait_statement(self.cluster, 'delivery_status', follower.label, statement,
                       lambda row: row['wait_event_type'] == 'Lock' and int(held) in row['blocking'],
                       'second status waits for the first')
        blocker.send('commit;')
        self.assertEqual(blocker.finish()[0], 0)
        code, _, error = follower.finish()
        self.assertEqual(code, 0, error)
        return before

    @property
    def after(self):
        return self.feed()['cursor']

    def test_suite_passes_on_real_postgresql(self):
        release = json.dumps({'protocol_version': RELEASE['protocol_version'], 'skill_version': RELEASE['skill_version']})
        self.cluster.sql('delivery_status', f"begin; select set_config('quorum_test.release','{release}',true);"
                         + (ROOT / 'tests/quorum_delivery_status_backend.sql').read_text() + 'rollback;')

    def test_a_cursor_cannot_pass_a_change_still_being_committed(self):
        first, second = self.message('race-order-1'), self.message('race-order-2')
        start = self.after
        before = self.hold_then_race(self.mark(first, 'queued'), self.mark(second, 'queued'), 'order')
        # A reader during the hold sees nothing, so no cursor moved past the open change.
        self.assertEqual((before['rows'], before['cursor']), ([], start))
        rows = self.feed(start)['rows']
        self.assertEqual([row['message_id'] for row in rows], [first, second])
        self.assertLess(rows[0]['seq'], rows[1]['seq'])

    def test_two_first_reports_of_one_message_both_stand(self):
        message_id = self.message('race-first')
        self.hold_then_race(self.mark(message_id, 'queued'), self.mark(message_id, 'read', "'received'"), 'first')
        out = self.cluster.sql('delivery_status', 'begin;' + self.as_owner()
                               + f"select public.session_delivery_status('{SENDER}','sender-session','{message_id}'); commit;")
        view = json.loads(next(line for line in out.splitlines() if line.startswith('{')))
        self.assertEqual(view['status'], 'read')
        self.assertIsNotNone(view['queued_at'])
        self.assertIsNotNone(view['read_at'])

    def test_a_reply_and_a_report_of_one_message_both_stand(self):
        message_id = self.message('race-reply')
        reply = (self.as_owner() + f"select public.session_reply('{RECIPIENT}','recipient-session','{message_id}',"
                 "'answer','normal');")
        self.hold_then_race(self.mark(message_id, 'read', "'reply'"), reply, 'reply', statement='session_reply')
        out = self.cluster.sql('delivery_status', 'begin;' + self.as_owner()
                               + f"select public.session_delivery_status('{SENDER}','sender-session','{message_id}'); commit;")
        view = json.loads(next(line for line in out.splitlines() if line.startswith('{')))
        self.assertEqual(view['status'], 'answered')
        self.assertIsNotNone(view['read_at'])


if __name__ == '__main__':
    unittest.main()
