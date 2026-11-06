"""Static migration-order invariants; the disposable SQL harness executes them."""
from pathlib import Path
import hashlib
import re
import unittest

SKILL = Path(__file__).resolve().parents[1] / 'skills/quorum'
MIGRATIONS = sorted((SKILL / 'supabase/migrations').glob('*.sql'))
SERIALIZATION = '_open_access_close_serialization.sql'
PRIORITY_PATH = next(path for path in MIGRATIONS if path.name.endswith('_urgent_message_priority.sql'))
SERIALIZATION_PATH = next(path for path in MIGRATIONS if path.name.endswith(SERIALIZATION))
REVIEWED_SOURCES = {
    '20261022150000_open_access_quorums.sql': 'd9d1e1b3409da29ba59574e90bd5d96946d965ec4fa240d6bb7b8290d1a16bab',
    '20261022160000_open_session_requirements.sql': '360665bd4ee5b918e5685fa99f4c1b7241ca61fc16bc906808c9b582ebb62a13',
    '20261022170000_urgent_message_priority.sql': 'a382fb74abf40cee84ef15d28046230dcc06ffc97783cef11db28ae5b47dd9f2',
}
# prosrc hashes on a database after 170000; the disposable harnesses recompute them.
SERIALIZATION_PINS = {
    'quorum_private.call(text,jsonb,text)': 'ac169502144c0d19620bc8a18a9e89f5990367053b5157982252f53a550b8934',
    'quorum_private.call_priority_core(text,jsonb,text)': '01f0fb843a904ff3b615e432ab054c25e44d3c0494e5213526ddb232e04b512c',
    'quorum_private.call_invite_core(text,jsonb,text)': 'f6a10b1b7f476328eaa21e34bff6336bd734a1dd1205684bdd6f0d5ea814aadc',
    'quorum_private.call_legacy_core(text,jsonb,text)': '1aa452947be35e79c190d3f59baa8a3ffc8da8ffdb5eb36d07bb2fea23a4bebe',
    'public.quorum_set_access(uuid,text,text)': 'b4f74ea73985d7e2f03560ffdb57b3ef6f117375e3c0c2ce88b072ab0ed8b480',
    'public.invite_create_open(uuid,text)': 'b3f06f990f5523723b27eb36a585ffca801087239e6784166e940ad96ac47349',
    'public.invite_accept_id(uuid,text)': 'd61c6972108e7feafa0a77ab32c436c8ec4971cacedf2c219fd4f7d6dc9e0fa4',
    'public.session_requirements()': '6e9752bcaedbbdfc4b2b8197cb44598306ee8d79044d654836017aab86031eef',
}
ROLES = '20261105140000_role_assignment_and_owner_retire.sql'
STATUS = '20261105160000_peer_status_visibility.sql'
DEDUPE = '20261106120000_hello_reconnect_dedupe.sql'
# prosrc hashes after the reviewed tail through 190000; tests/quorum-backend recomputes them by drift injection.
ROLE_PINS = {
    'quorum_private.call(text,jsonb,text)': 'ca718afbcda8682ade19ea6b246ccc2f1bfd5523eb7d1076cf4e9583e24f3ab7',
    'public.invite_create_id(uuid,text,text)': '3879f923b48fda8b9c67a76b5774d6cc132a8f4736ad451c6811b2de0c3d436b',
    'public.invite_create_open(uuid,text)': SERIALIZATION_PINS['public.invite_create_open(uuid,text)'],
    'public.agent_join(uuid,uuid,text,text,text,text,text)':
        '732ca06801a141867e97e14e678df538a55eb3b346ec83ae85aed75c0832551d',
    'public.invite_accept_id(uuid,text)': SERIALIZATION_PINS['public.invite_accept_id(uuid,text)'],
    'public.resolve_role(uuid,text,text)': 'c75b3982fce17a7c32caa7d81f2e74d4bc3aa903b88a54f939ee6849ab68aea1',
    'quorum_private.member(uuid,uuid)': '155cf2dcc2b2d61c5ff55a7533b13eae931b9e077420f3aaf2408b7a22ca7435',
    'quorum_private.verified_actor(uuid)': 'f20291c082af61a232754c29480b65877d7994c177c06ce079b655af6b0f832b',
    'public.session_requirements()': SERIALIZATION_PINS['public.session_requirements()'],
}


def defining(signature):
    pattern = re.compile(r'create (?:or replace )?function ' + re.escape(signature), re.I)
    return [path for path in MIGRATIONS if pattern.search(path.read_text(encoding='utf-8'))]


class MigrationOrderTests(unittest.TestCase):
    def test_final_session_requirements_admits_open_members_only_through_verified_actor(self):
        # Any later migration that redefines release metadata must keep the open-member gate.
        final = defining('public.session_requirements(')[-1]
        body = final.read_text(encoding='utf-8')
        opened = next(path for path in MIGRATIONS if path.name.endswith('_open_session_requirements.sql'))
        self.assertGreaterEqual(final.name, opened.name)
        self.assertIn('quorum_private.verified_actor(auth.uid())', body)
        self.assertNotIn("provider='github'", body)
        self.assertIn('from public,anon,authenticated', body)
        self.assertNotRegex(body, r'grant execute on function public\.session_requirements\(\) to [^;]*anon')

    def test_open_session_requirements_follows_both_dependencies_and_fails_closed(self):
        names = [path.name for path in MIGRATIONS]
        index = {suffix: next(i for i, name in enumerate(names) if name.endswith(suffix))
                 for suffix in ('_open_access_quorums.sql', '_native_session_coordination.sql',
                                '_open_session_requirements.sql')}
        self.assertGreater(index['_open_session_requirements.sql'], index['_open_access_quorums.sql'])
        self.assertGreater(index['_open_session_requirements.sql'], index['_native_session_coordination.sql'])
        body = MIGRATIONS[index['_open_session_requirements.sql']].read_text(encoding='utf-8')
        # Upgraded servers may apply the older-dated open access file late; the gate
        # refuses to run before it and refuses an unreviewed requirements body.
        self.assertIn("raise exception 'open_access_required'", body)
        self.assertIn('reviewed_function_changed', body)

    def test_server_guide_lists_open_session_requirements_after_open_access(self):
        guide = (SKILL / 'references/server.md').read_text(encoding='utf-8')
        name = next(path.name for path in MIGRATIONS if path.name.endswith('_open_session_requirements.sql'))
        self.assertIn(name, guide)
        self.assertLess(guide.index('_open_access_quorums.sql'), guide.index(name))

    def test_priority_release_precedes_close_serialization_and_pins_the_session_chain(self):
        names = [path.name for path in MIGRATIONS]
        self.assertLess(PRIORITY_PATH.name, SERIALIZATION_PATH.name)
        self.assertGreater(PRIORITY_PATH.name, next(name for name in names if name.endswith('_open_session_requirements.sql')))
        body = PRIORITY_PATH.read_text(encoding='utf-8')
        for signature in ('quorum_private.call(text,jsonb,text)', 'quorum_private.call_invite_core(text,jsonb,text)',
                          'quorum_private.call_legacy_core(text,jsonb,text)', 'quorum_private.verified_actor(uuid)',
                          'quorum_private.native_session(uuid,text)', 'quorum_private.owned_agent(uuid,uuid)',
                          'quorum_private.member(uuid,uuid)', 'public.session_inbox(uuid,text)'):
            self.assertRegex(body, re.escape(f"'{signature}','") + '[0-9a-f]{64}' + "'")
        self.assertIn("raise exception 'reviewed_release_changed'", body)
        guide = (SKILL / 'references/server.md').read_text(encoding='utf-8')
        self.assertLess(guide.index('_open_session_requirements.sql'), guide.index(PRIORITY_PATH.name))

    def test_reviewed_open_access_sources_stay_byte_identical(self):
        # Live servers applied these bytes; later pins hash the bodies they install.
        for name, digest in REVIEWED_SOURCES.items():
            data = (SKILL / 'supabase/migrations' / name).read_bytes()
            self.assertEqual(hashlib.sha256(data).hexdigest(), digest, name)

    def test_close_serialization_precedes_extensions_and_pins_the_priority_release(self):
        names = [path.name for path in MIGRATIONS]
        member = next(name for name in names if name.endswith('_member_task_authority.sql'))
        self.assertLess(SERIALIZATION_PATH.name, '20261030140000_owner_update_maintenance.sql')
        self.assertIn('20261030140000_owner_update_maintenance.sql', names)
        self.assertLess('20261030140000_owner_update_maintenance.sql', member)
        self.assertLess(member, '20261030142000_project_knowledge.sql')
        self.assertIn('20261030142000_project_knowledge.sql', names)
        body = SERIALIZATION_PATH.read_text(encoding='utf-8')
        pins = dict(re.findall(r"'([a-z_.]+\([a-z,]*\))','([0-9a-f]{64})'", body))
        self.assertEqual(pins, SERIALIZATION_PINS)
        # The open-access wrapper keeps the exact body the priority release pinned.
        priority = PRIORITY_PATH.read_text(encoding='utf-8')
        self.assertIn("'quorum_private.call_priority_core(text,jsonb,text)'", body)
        self.assertIn("'quorum_private.call(text,jsonb,text)','" + SERIALIZATION_PINS[
            'quorum_private.call_priority_core(text,jsonb,text)'] + "'", priority)
        self.assertIn("raise exception 'reviewed_dispatcher_changed: %', f;", body)
        self.assertIn("raise exception 'serial_core_present'", body)
        guide = (SKILL / 'references/server.md').read_text(encoding='utf-8')
        self.assertLess(guide.index(PRIORITY_PATH.name), guide.index(SERIALIZATION_PATH.name))

    def test_close_serialization_locks_the_quorum_row_before_any_other_row(self):
        body = SERIALIZATION_PATH.read_text(encoding='utf-8')
        wrapper = body.split('create function quorum_private.call(', 1)[1]
        # Close conflicts with issue and accept, but not with foreign-key KEY SHARE checks.
        self.assertIn('for no key update', wrapper)
        self.assertEqual(wrapper.count('for share'), 2)
        self.assertNotIn('for update', wrapper.replace('for no key update', ''))
        self.assertNotIn('for key share', wrapper)
        # The wrapper only reads before the core; every write and check stays in the core.
        for write in ('insert ', 'update ', 'delete ', 'pg_advisory'):
            self.assertNotIn(write, wrapper.lower())
        self.assertEqual(wrapper.count('return quorum_private.call_serial_core(op,p,req);'), 2)
        for forbidden in ('grant ', 'to anon', 'security invoker', 'to public'):
            self.assertNotIn(forbidden, body.lower())

    def test_close_serialization_refuses_other_isolation_before_any_lock(self):
        # Stale REPEATABLE READ/SERIALIZABLE snapshots skip the core's rereads.
        wrapper = SERIALIZATION_PATH.read_text(encoding='utf-8').split('create function quorum_private.call(', 1)[1]
        guard = wrapper.index("if current_setting('transaction_isolation')<>'read committed' then")
        self.assertLess(wrapper.index('return quorum_private.call_serial_core(op,p,req);'), guard)
        self.assertLess(guard, wrapper.index("raise exception 'open_access_requires_read_committed';"))
        self.assertLess(guard, min(wrapper.index('for share'), wrapper.index('for no key update')))


    def test_hello_visibility_follows_the_reviewed_tail_and_pins_the_heartbeat(self):
        names = [path.name for path in MIGRATIONS]
        hello = next(name for name in names if name.endswith('_hello_roster_visibility.sql'))
        self.assertGreater(hello, '20261103075741_llm_wiki.sql')
        self.assertGreater(hello, SERIALIZATION_PATH.name)
        body = (SKILL / 'supabase/migrations' / hello).read_text(encoding='utf-8')
        pins = dict(re.findall(r"'([a-z_.]+\([a-z,]*\))','([0-9a-f]{64})'", body))
        self.assertEqual(pins['public.session_requirements()'], SERIALIZATION_PINS['public.session_requirements()'])
        self.assertIn('public.session_heartbeat(uuid,text,uuid,integer,text)', pins)
        self.assertIn("raise exception 'hello_extension_present'", body)
        self.assertIn("raise exception 'reviewed_dispatcher_changed: %',f;", body)
        # Additive only: the release, older heartbeat body and table access stay unchanged.
        self.assertNotIn('function public.session_requirements', body)
        # Only the peer status release (#113) replaces the heartbeat after it, by wrapping this body.
        self.assertEqual([path.name for path in defining('public.session_heartbeat(')][-2:], [hello, STATUS])
        self.assertNotRegex(body.lower(), r'grant [^;]* to [^;]*(anon|public|service_role)')
        self.assertNotRegex(body.lower(), r'grant [^;]* on (table )?quorum_private\.')
        self.assertIn('requires_handler=false', body)
        guide = (SKILL / 'references/server.md').read_text(encoding='utf-8')
        section = guide.split('## Hello reporting extension', 1)[1].split('\n## ', 1)[0]
        self.assertIn(hello, section)
        self.assertIn('after `20261022180000_open_access_close_serialization.sql`', section)

    def test_open_invite_ttl_follows_hello_and_changes_only_new_open_expiry(self):
        names = [path.name for path in MIGRATIONS]
        ttl = next(name for name in names if name.endswith('_open_invite_ttl.sql'))
        self.assertGreater(ttl, '20261103180000_hello_roster_visibility.sql')
        # Only the reviewed role-assignment (#105), peer status (#113) and Hello dedupe (#126) releases follow it.
        self.assertEqual(names[names.index(ttl) + 1:], [ROLES, STATUS, DEDUPE])
        body = (SKILL / 'supabase/migrations' / ttl).read_text(encoding='utf-8')
        pins = dict(re.findall(r"'([a-z_.]+\([a-z,]*\))','([0-9a-f]{64})'", body))
        self.assertEqual(pins, {'quorum_private.call_priority_core(text,jsonb,text)':
                                SERIALIZATION_PINS['quorum_private.call_priority_core(text,jsonb,text)']})
        self.assertIn("raise exception 'open_invite_ttl_present'", body)
        self.assertIn("raise exception 'reviewed_dispatcher_changed: %',f;", body)
        self.assertIn("now()+interval '60 minutes'", body)
        self.assertIn('before insert on quorum_private.id_invitations', body)
        self.assertIn('when (new.open)', body)
        # No reviewed body, update of existing rows or new grant.
        self.assertNotRegex(body.lower(), r'create (or replace )?function (public|quorum_private)\.(call|invite)')
        self.assertNotRegex(body.lower(), r'\bupdate quorum_private\.')
        self.assertNotRegex(body.lower(), r'\bgrant\b')
        guide = (SKILL / 'references/server.md').read_text(encoding='utf-8')
        section = guide.split('## Open invitation lifetime', 1)[1].split('\n## ', 1)[0]
        self.assertIn(ttl, section)
        self.assertIn('after `20261103180000_hello_roster_visibility.sql`', section)
        for doc in ('references/join.md', 'references/invite.md', 'references/invitations.md', 'README.md'):
            text = ' '.join((SKILL / doc).read_text(encoding='utf-8').split())
            self.assertIn('60 minutes', text, doc)

    def test_role_assignment_follows_the_tail_pins_reviewed_bodies_and_grants_only_authenticated(self):
        names = [path.name for path in MIGRATIONS]
        self.assertEqual(names[names.index(ROLES) + 1:], [STATUS, DEDUPE])
        self.assertGreater(ROLES, '20261103190000_open_invite_ttl.sql')
        body = (SKILL / 'supabase/migrations' / ROLES).read_text(encoding='utf-8')
        pins = dict(re.findall(r"'([a-z_.]+\([a-z,]*\))','([0-9a-f]{64})'", body))
        self.assertEqual(pins, ROLE_PINS)
        # The dispatcher pin is the one the maintenance extension (140000) applied against.
        maintenance = (SKILL / 'supabase/migrations/20261030140000_owner_update_maintenance.sql').read_text()
        self.assertIn("'quorum_private.call(text,jsonb,text)','" + ROLE_PINS['quorum_private.call(text,jsonb,text)'] + "'",
                      maintenance)
        guard = body.split('end $migration$;', 1)[0]
        self.assertIn("raise exception 'role_assignment_present'", guard)
        self.assertIn("raise exception 'owner_update_maintenance_required'", guard)
        self.assertIn("raise exception 'reviewed_dispatcher_changed: %',f;", guard)
        # Every check runs before the only rename; nothing else changes inside the guard.
        self.assertLess(guard.index("reviewed_dispatcher_changed"), guard.index('rename to call_role_core'))
        self.assertEqual(guard.lower().count('alter function'), 1)
        # Every new function is a pinned-search-path security definer.
        created = re.findall(r'create function ([a-z_.]+)\(', body)
        self.assertEqual(created, ['quorum_private.call', 'public.invite_create_id', 'public.invite_create_open',
                                   'public.agent_join', 'public.agent_retire', 'public.quorum_agents'])
        for chunk in body.split('create function ')[1:]:
            header = chunk.split(' as $', 1)[0]
            self.assertIn('security definer set search_path=pg_catalog', ' '.join(header.split()), header[:60])
        self.assertEqual(defining('public.agent_join(')[-1].name, ROLES)
        # Replaced entry points are dropped, never overloaded.
        for old in ('public.invite_create_id(uuid,text,text)', 'public.invite_create_open(uuid,text)',
                    'public.agent_join(uuid,uuid,text,text,text,text,text)'):
            self.assertIn(f'drop function {old};', body)
        lowered = ' '.join(body.lower().split())
        self.assertIn('revoke all on function quorum_private.call_role_core(text,jsonb,text) '
                      'from public,anon,authenticated,service_role;', lowered)
        self.assertIn('revoke all on function quorum_private.call(text,jsonb,text) '
                      'from public,anon,authenticated,service_role;', lowered)
        self.assertIn('revoke all on quorum_private.agent_supersessions from public,anon,authenticated,service_role;',
                      lowered)
        grants = re.findall(r'grant [^;]*;', lowered)
        self.assertEqual(len(grants), 1)
        self.assertTrue(grants[0].endswith(' to authenticated;'))
        self.assertNotRegex(grants[0], r'quorum_private\.')
        # No required-release change and no table exposure.
        self.assertNotIn('function public.session_requirements', body)
        self.assertNotRegex(lowered, r'grant [^;]* on (table )?quorum_private\.')
        # The owner roster returns binding metadata only.
        roster = body.split('create function public.quorum_agents(', 1)[1]
        for secret in ('session_ref', 'token', 'label', 'device_id', 'request_id'):
            self.assertNotIn(secret + ' ', roster.split('from quorum_private.agents a', 1)[0].replace('owner_label', ''))
        guide = (SKILL / 'references/server.md').read_text(encoding='utf-8')
        section = guide.split('## Invitation roles and owner retire', 1)[1].split('\n## ', 1)[0]
        self.assertIn(ROLES, section)
        self.assertIn('after `20261103190000_open_invite_ttl.sql`', section)


    def test_peer_status_follows_roles_pins_reviewed_bodies_and_grants_only_authenticated(self):
        names = [path.name for path in MIGRATIONS]
        self.assertEqual(names[names.index(STATUS) + 1:], [DEDUPE])
        body = (SKILL / 'supabase/migrations' / STATUS).read_text(encoding='utf-8')
        self.assertTrue(body.startswith('-- SOURCE ONLY.'))
        pins = dict(re.findall(r"'([a-z_.]+\([a-z,]*\))','([0-9a-f]{64})'", body))
        self.assertEqual(set(pins), {'public.session_heartbeat(uuid,text,uuid,integer,text)',
                                     'quorum_private.session_heartbeat_before_hello(uuid,text,uuid,integer,text)',
                                     'quorum_private.call(text,jsonb,text)', 'public.session_peers(uuid,text)',
                                     'quorum_private.native_session(uuid,text)',
                                     'public.session_update_report(uuid,text,uuid,uuid,text,jsonb,text)',
                                     'public.session_requirements()'})
        self.assertEqual(pins['public.session_requirements()'], SERIALIZATION_PINS['public.session_requirements()'])
        hello = (SKILL / 'supabase/migrations/20261103180000_hello_roster_visibility.sql').read_text(encoding='utf-8')
        core = 'quorum_private.session_heartbeat_before_hello(uuid,text,uuid,integer,text)'
        self.assertIn("'" + core.replace('quorum_private.session_heartbeat_before_hello', 'public.session_heartbeat')
                      + "','" + pins[core] + "'", hello)
        # The role-assignment wrapper replaced the dispatcher the maintenance release pinned.
        self.assertNotEqual(pins['quorum_private.call(text,jsonb,text)'], ROLE_PINS['quorum_private.call(text,jsonb,text)'])
        guard = body.split('end $migration$;', 1)[0]
        for code in ('peer_status_present', 'hello_extension_required', 'owner_update_maintenance_required',
                     'role_assignment_required'):
            self.assertIn(f"raise exception '{code}'", guard)
        self.assertIn("raise exception 'reviewed_dispatcher_changed: %',f;", guard)
        # The guard only reads; every change follows it.
        for change in ('alter ', 'create ', 'drop ', 'insert ', 'update ', 'grant '):
            self.assertNotIn(change, guard.split('begin', 1)[1].lower())
        created = re.findall(r'create function ([a-z_.]+)\(', body)
        self.assertEqual(created, ['public.session_heartbeat', 'public.session_peers'])
        for chunk in body.split('create function ')[1:]:
            header = chunk.split(' as $', 1)[0]
            self.assertIn('security definer set search_path=pg_catalog', ' '.join(header.split()), header[:60])
        self.assertEqual(defining('public.session_peers(')[-1].name, STATUS)
        # Old bodies move to private and are revoked; the old public heartbeat signature is gone.
        lowered = ' '.join(body.lower().split())
        for moved in ('session_heartbeat_before_status(uuid,text,uuid,integer,text)', 'session_peers_before_status(uuid,text)'):
            self.assertIn(f'revoke all on function quorum_private.{moved} from public,anon,authenticated,service_role;', lowered)
        self.assertIn('revoke all on quorum_private.session_status from public,anon,authenticated,service_role;', lowered)
        grants = re.findall(r'grant [^;]*;', lowered)
        self.assertEqual(len(grants), 1)
        self.assertTrue(grants[0].endswith(' to authenticated;'))
        self.assertNotRegex(grants[0], r'quorum_private\.')
        self.assertNotIn('function public.session_requirements', body)
        self.assertNotRegex(lowered, r'grant [^;]* on (table )?quorum_private\.')
        # Peers see counts, timestamps and a boolean: never the run ID or the stored start time.
        peers = body.split('create function public.session_peers(', 1)[1].split(' from quorum_private.agents a', 1)[0]
        self.assertNotRegex(peers, r's\.client_run_id( as|,)')
        self.assertNotIn('runtime_started_at', peers)
        self.assertNotIn('message_id', peers)
        guide = (SKILL / 'references/server.md').read_text(encoding='utf-8')
        section = guide.split('## Peer status extension', 1)[1].split('\n## ', 1)[0]
        self.assertIn(STATUS, section)
        self.assertIn('after `20261105140000_role_assignment_and_owner_retire.sql`', section)


    def test_hello_dedupe_follows_status_pins_reviewed_bodies_and_grants_nothing(self):
        names = [path.name for path in MIGRATIONS]
        self.assertEqual(names[-1], DEDUPE)
        body = (SKILL / 'supabase/migrations' / DEDUPE).read_text(encoding='utf-8')
        self.assertTrue(body.startswith('-- SOURCE ONLY.'))
        pins = dict(re.findall(r"'([a-z_.]+\([a-z,]*\))','([0-9a-f]{64})'", body))
        status = (SKILL / 'supabase/migrations' / STATUS).read_text(encoding='utf-8')
        status_pins = dict(re.findall(r"'([a-z_.]+\([a-z,]*\))','([0-9a-f]{64})'", status))
        core = 'quorum_private.session_heartbeat_before_hello(uuid,text,uuid,integer,text)'
        self.assertEqual(set(pins), {'public.session_heartbeat(uuid,text,uuid,integer,text,integer,timestamptz,uuid,timestamptz)',
                                     'quorum_private.session_heartbeat_before_status(uuid,text,uuid,integer,text)', core,
                                     'quorum_private.call(text,jsonb,text)', 'quorum_private.native_session(uuid,text)',
                                     'public.session_ack(uuid,text,uuid)', 'public.session_requirements()'})
        # The moved Hello wrapper, its core, the dispatcher and the binding check are the bodies 160000 pinned.
        self.assertEqual(pins['quorum_private.session_heartbeat_before_status(uuid,text,uuid,integer,text)'],
                         status_pins['public.session_heartbeat(uuid,text,uuid,integer,text)'])
        for name in (core, 'quorum_private.call(text,jsonb,text)', 'quorum_private.native_session(uuid,text)',
                     'public.session_requirements()'):
            self.assertEqual(pins[name], status_pins[name], name)
        guard = body.split('end $migration$;', 1)[0]
        for code in ('hello_dedupe_present', 'peer_status_required', 'hello_extension_required'):
            self.assertIn(f"raise exception '{code}'", guard)
        self.assertIn("raise exception 'reviewed_dispatcher_changed: %',f;", guard)
        for change in ('alter ', 'create ', 'drop ', 'insert ', 'update ', 'grant '):
            self.assertNotIn(change, guard.split('begin', 1)[1].lower())
        created = re.findall(r'create function ([a-z_.]+)\(', body)
        self.assertEqual(created, ['quorum_private.session_health_streak', 'quorum_private.session_heartbeat_before_hello',
                                   'quorum_private.session_heartbeat_before_status'])
        for chunk in body.split('create function ')[1:]:
            header = chunk.split(' as $', 1)[0]
            self.assertIn('security definer set search_path=pg_catalog', ' '.join(header.split()), header[:60])
        lowered = ' '.join(body.lower().split())
        for fn in ('session_heartbeat_core_before_dedupe(uuid,text,uuid,integer,text)',
                   'session_heartbeat_hello_before_dedupe(uuid,text,uuid,integer,text)',
                   'session_heartbeat_before_hello(uuid,text,uuid,integer,text)',
                   'session_heartbeat_before_status(uuid,text,uuid,integer,text)', 'session_health_streak()'):
            self.assertIn(f'revoke all on function quorum_private.{fn} from public,anon,authenticated,service_role;', lowered)
        for table in ('session_streaks', 'session_hello_sent'):
            self.assertIn(f'revoke all on quorum_private.{table} from public,anon,authenticated,service_role;', lowered)
        code = ' '.join(line.split('--', 1)[0] for line in body.lower().splitlines())
        self.assertNotRegex(code, r'\bgrant\b')
        self.assertNotRegex(code, r'(create|alter|drop) (or replace )?function public\.')
        self.assertIn("interval '30 minutes'", body)
        self.assertIn("interval '60 seconds'", body)
        # The internal skip list never reaches a client.
        self.assertIn("result:=result-'hello_skipped';", body)
        guide = (SKILL / 'references/server.md').read_text(encoding='utf-8')
        section = guide.split('## Reconnect Hello dedupe', 1)[1].split('\n## ', 1)[0]
        self.assertIn(DEDUPE, section)
        self.assertIn('after `20261105160000_peer_status_visibility.sql`', section)


if __name__ == '__main__':
    unittest.main()
