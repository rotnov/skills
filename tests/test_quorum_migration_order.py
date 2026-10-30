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

    def test_close_serialization_precedes_member_authority_and_pins_the_priority_release(self):
        names = [path.name for path in MIGRATIONS]
        member = next(name for name in names if name.endswith('_member_task_authority.sql'))
        self.assertLess(SERIALIZATION_PATH.name, member)
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


if __name__ == '__main__':
    unittest.main()
