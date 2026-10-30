"""Actual member-authority SQL on an opt-in, network-none PostgreSQL 17 container.

QUORUM_MEMBER_DOCKER=1 uses only the existing local image and removes its own
container. No real service configuration, credentials or client process is read.
"""
import json
import os
from pathlib import Path
import unittest

from test_quorum_close_race import Cluster

ROOT = Path(__file__).resolve().parents[1]
SKILL = ROOT / 'skills/quorum'


def uid(number):
    return f'{number:08x}-1111-4111-8111-111111111111'


OWNER, ANONYMOUS = uid(1), uid(2)
QUORUM, OTHER_QUORUM = uid(10), uid(11)
RECEIVER, SENDER, DUPLICATE, GH_SENDER = map(uid, (20, 21, 22, 23))
DEVICE, SENDER_DEVICE = uid(30), uid(31)
SESSION, MESSAGE = uid(40), uid(50)
MIGRATION = '20261030141000_member_task_authority.sql'


@unittest.skipUnless(os.environ.get('QUORUM_MEMBER_DOCKER') == '1', 'opt-in disposable PostgreSQL authority test')
class MemberAuthoritySqlTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.cluster = Cluster().__enter__()
        cls.addClassCleanup(cls.cluster.__exit__)
        cls.cluster.sql('postgres', 'create database member_authority')
        script = (ROOT / 'tests/quorum_backend_fixture.sql').read_text()
        script += (SKILL / 'references/server-bootstrap.sql').read_text()
        for path in sorted((SKILL / 'supabase/migrations').glob('*.sql')):
            if path.name <= MIGRATION: script += path.read_text()
        cls.cluster.sql('member_authority', script, single=True)
        cls.cluster.sql('member_authority', f"""
          insert into auth.users(id,raw_user_meta_data) values('{OWNER}','{{}}'),('{ANONYMOUS}','{{}}');
          insert into auth.identities(user_id,provider,provider_id,identity_data)
            values('{OWNER}','github','9000000000052','{{}}');
          insert into quorum_private.quorums(id,slug,owner_id,access)
            values('{QUORUM}','member-test','{OWNER}','open'),('{OTHER_QUORUM}','other','{OWNER}','open');
          insert into quorum_private.memberships(quorum_id,user_id,role)
            values('{QUORUM}','{OWNER}','owner'),('{QUORUM}','{ANONYMOUS}','member');
          insert into quorum_private.devices(id,owner_id,label)
            values('{DEVICE}','{OWNER}','test'),('{SENDER_DEVICE}','{ANONYMOUS}','test');
          insert into quorum_private.agents(id,quorum_id,device_id,owner_id,project_ref,role,engine,session_ref)
            values('{RECEIVER}','{QUORUM}','{DEVICE}','{OWNER}','project','receiver','codex','{SESSION}'),
                  ('{SENDER}','{QUORUM}','{SENDER_DEVICE}','{ANONYMOUS}','project','same-role','claude','{uid(41)}'),
                  ('{DUPLICATE}','{QUORUM}','{SENDER_DEVICE}','{ANONYMOUS}','project','same-role','grok','{uid(42)}'),
                  ('{GH_SENDER}','{QUORUM}','{DEVICE}','{OWNER}','project','github','codex','{uid(43)}');
          insert into quorum_private.conversations(id,quorum_id,sender_id,recipient_id)
            values('{uid(60)}','{QUORUM}','{SENDER}','{RECEIVER}'),
                  ('{uid(61)}','{QUORUM}','{DUPLICATE}','{RECEIVER}'),
                  ('{uid(62)}','{QUORUM}','{GH_SENDER}','{RECEIVER}');
          insert into quorum_private.messages(id,quorum_id,conversation_id,sender_id,recipient_id,sequence,body)
            values('{MESSAGE}','{QUORUM}','{uid(60)}','{SENDER}','{RECEIVER}',1,'private-body-marker'),
                  ('{uid(51)}','{QUORUM}','{uid(61)}','{DUPLICATE}','{RECEIVER}',1,'duplicate role task'),
                  ('{uid(52)}','{QUORUM}','{uid(62)}','{GH_SENDER}','{RECEIVER}',1,'github task');
        """)

    def query(self, before='', *, message=MESSAGE, receiver=RECEIVER, session=SESSION,
              user=OWNER, role='authenticated', read_only=False):
        raw = self.cluster.sql('member_authority', f"""
          begin {'read only' if read_only else ''};
          {before}
          select set_config('request.jwt.claim.sub','{user}',true);
          set local role {role};
          select public.session_task_authority('{receiver}','{session}','{message}');
          rollback;
        """)
        return json.loads(raw.strip().splitlines()[-1])

    def test_current_anonymous_sender_with_duplicate_role_is_authorized_read_only(self):
        # No identity-provider check or role resolution may exclude this sender.
        result = self.query(read_only=True)
        self.assertEqual(result, {'authorized': True, 'reason': 'current_member', 'message_id': MESSAGE,
                                 'agent_id': RECEIVER, 'session_ref': SESSION, 'sender_id': SENDER,
                                 'quorum_id': QUORUM, 'project_ref': 'project'})
        self.assertTrue(self.query(message=uid(51), read_only=True)['authorized'])
        self.assertTrue(self.query(message=uid(52), read_only=True)['authorized'])
        self.assertNotIn('private-body-marker', json.dumps(result))

    def test_sender_revocation_after_a_message_was_queued_or_receipted_denies_it(self):
        mutations = [f"update quorum_private.agents set revoked=true where id='{SENDER}';",
                     f"update quorum_private.devices set revoked=true where id='{SENDER_DEVICE}';",
                     f"update quorum_private.memberships set revoked=true where user_id='{ANONYMOUS}';",
                     f"update quorum_private.agents set project_ref='other' where id='{SENDER}';",
                     f"update quorum_private.agents set quorum_id='{OTHER_QUORUM}' where id='{SENDER}';"]
        for mutation in mutations:
            with self.subTest(mutation=mutation):
                result = self.query(f"insert into quorum_private.session_receipts(agent_id,message_id) "
                                    f"values('{RECEIVER}','{MESSAGE}'); {mutation}")
                self.assertEqual((result['authorized'], result['reason']), (False, 'sender_unavailable'))

    def test_receiver_binding_and_current_membership_are_required(self):
        for mutation in (f"update quorum_private.agents set revoked=true where id='{RECEIVER}';",
                         f"update quorum_private.devices set revoked=true where id='{DEVICE}';",
                         f"update quorum_private.memberships set revoked=true where user_id='{OWNER}';"):
            with self.subTest(mutation=mutation), self.assertRaisesRegex(RuntimeError, 'agent_denied|membership_required'):
                self.query(mutation)
        with self.assertRaisesRegex(RuntimeError, 'session_binding_denied'):
            self.query(session=uid(99))
        with self.assertRaisesRegex(RuntimeError, 'task_message_denied'):
            self.query(receiver=GH_SENDER, session=uid(43))
        with self.assertRaisesRegex(RuntimeError, 'task_message_denied'):
            self.query(message=uid(99))

    def test_reply_or_transport_notice_is_not_a_task(self):
        for mutation in (f"update quorum_private.messages set reply_to='{uid(51)}' where id='{MESSAGE}';",
                         f"insert into quorum_private.session_notices(message_id,agent_id) "
                         f"values('{MESSAGE}','{SENDER}');"):
            with self.subTest(mutation=mutation):
                result = self.query(mutation)
                self.assertEqual((result['authorized'], result['reason']), (False, 'not_task'))

    def test_typed_maintenance_is_not_a_task_but_body_claims_are_not_authority(self):
        # Before #69 there is no typed maintenance table. The sole optional schema
        # case is its absence; once present its authoritative message IDs exclude tasks.
        self.assertTrue(self.query(f"update quorum_private.messages set body='quorum.update maintenance' "
                                   f"where id='{MESSAGE}';")['authorized'])
        typed = f"""
          do $fixture$ begin
            if to_regclass('quorum_private.update_notices') is null then
              create table quorum_private.update_notices(message_id uuid unique not null);
              insert into quorum_private.update_notices values('{MESSAGE}');
            else
              insert into quorum_private.update_targets(id,quorum_id,protocol_version,skill_version,content_sha256)
                values('{uid(70)}','{QUORUM}',2,'0.3.0',repeat('a',64));
              insert into quorum_private.update_notices(target_id,recipient_id,message_id)
                values('{uid(70)}','{RECEIVER}','{MESSAGE}');
            end if;
          end $fixture$;
        """
        result = self.query(typed)
        self.assertEqual((result['authorized'], result['reason']), (False, 'not_task'))

    def test_current_anonymous_receiver_is_allowed_with_its_own_exact_binding(self):
        mutation = (f"update quorum_private.agents set owner_id='{ANONYMOUS}',device_id='{SENDER_DEVICE}' "
                    f"where id='{RECEIVER}';")
        self.assertTrue(self.query(mutation, user=ANONYMOUS)['authorized'])
        with self.assertRaisesRegex(RuntimeError, 'agent_denied'):
            self.query(mutation)

    def test_anonymous_and_service_role_cannot_execute_the_rpc(self):
        for role in ('anon', 'service_role'):
            with self.subTest(role=role), self.assertRaisesRegex(RuntimeError, 'permission denied'):
                self.query(role=role)

    def test_migration_installs_the_same_authority_on_an_existing_backend(self):
        # Drop only the new RPC in this synthetic database, then exercise its
        # actual migration rather than relying on a copied client-side predicate.
        self.cluster.sql('member_authority', 'drop function public.session_task_authority(uuid,text,uuid)')
        self.cluster.sql('member_authority', (SKILL / 'supabase/migrations' / MIGRATION).read_text(), single=True)
        self.assertTrue(self.query(read_only=True)['authorized'])
