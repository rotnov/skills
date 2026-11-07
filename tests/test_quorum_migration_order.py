"""Static migration-order invariants; the disposable SQL harness executes them."""
import hang_guard  # noqa: F401
from pathlib import Path
import hashlib
import re
import unittest

SKILL = Path(__file__).resolve().parents[1] / 'skills/quorum'
MIGRATIONS = sorted((SKILL / 'supabase/migrations').glob('*.sql'))
BOOTSTRAP = (SKILL / 'references/server-bootstrap.sql').read_text(encoding='utf-8')
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
DEFAULT_ROLE = '20261106100000_knowledge_member_default_role.sql'
DEDUPE = '20261106120000_hello_reconnect_dedupe.sql'
AUTHORITY = '20261106130000_task_authority_project_bound.sql'
UNRECEIPTED = '20261106140000_session_message_unreceipted.sql'
SPLIT = '20261106150000_hello_sent_skipped.sql'
MESSAGE = '20261106160000_session_message.sql'
STATUSES = '20261106170000_delivery_statuses.sql'
REPORTS = '20261106180000_release_reports.sql'
CONVERGENCE = '20261022165000_native_session_engine_convergence.sql'
GROK = '20261022094041_native_grok_session.sql'
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


PIN = re.compile(r"'([a-z_.]+\([a-z,\[\]]*\))','([0-9a-f]{64})'")
STATEMENT = re.compile(
    r"^\s*create (?:or replace )?function\s+(?P<name>[a-z_]+\.[a-z_0-9]+)\s*\("
    r"|^\s*alter function\s+(?P<moved>[a-z_]+\.[a-z_0-9]+\([^)]*\))\s+"
    r"(?:rename to\s+(?P<renamed>[a-z_0-9]+)|set schema\s+(?P<schema>[a-z_]+))"
    r"|perform pg_temp\.quorum_gate\('(?P<gated>[^']+)',\s*\$g\$(?P<gate>.*?)\$g\$,\s*'(?P<replacement>[^']*)'",
    re.I | re.M | re.S)
TYPE_NAMES = {'timestamp with time zone': 'timestamptz'}


def argument_types(parameters):
    """The regprocedure argument list of a function header, without names or defaults."""
    types = []
    for parameter in filter(None, (item.strip() for item in re.split(r',(?![^(]*\))', parameters))):
        words = re.split(r'\s+default\s+|\s*=\s*', parameter, flags=re.I)[0].split()
        name = ' '.join(words[1:] or words).lower()
        types.append(TYPE_NAMES.get(name, name))
    return ','.join(types)


def prosrc(functions, signature):
    return hashlib.sha256(functions[signature].encode()).hexdigest()


def apply_source(functions, text):
    """Replay one source's function definitions, renames, moves and gate rewrites onto `functions`."""
    position = 0
    while (found := STATEMENT.search(text, position)):
        position = found.end()
        if found['name']:
            close = position
            depth = 1
            while depth:
                depth += {'(': 1, ')': -1}.get(text[close], 0)
                close += 1
            tag = re.compile(r'\bas\s+(\$[a-z]*\$)', re.I).search(text, close)
            end = text.index(tag[1], tag.end())
            functions[found['name'].lower() + '(' + argument_types(text[position:close - 1]) + ')'] = text[tag.end():end]
            position = end + len(tag[1])
        elif found['moved']:
            signature = found['moved'].replace(' ', '')
            schema_name, arguments = signature.split('(', 1)
            schema, name = schema_name.split('.')
            target = f"{found['schema'] or schema}.{found['renamed'] or name}({arguments}"
            functions[target] = functions.pop(signature)
        else:
            functions[found['gated']] = functions[found['gated']].replace(found['gate'], found['replacement'])


def replay(sources):
    """Each reviewed pin compared with the prosrc the sources before it install; pin mismatches, in order."""
    functions, mismatches = {}, []
    apply_source(functions, BOOTSTRAP)
    for name, text in sources:
        for signature, pin in PIN.findall(text):
            if signature not in functions or prosrc(functions, signature) != pin: mismatches.append((name, signature))
        apply_source(functions, text)
    return functions, mismatches


def sources(through=None, leaving_out=()):
    return [(path.name, path.read_text(encoding='utf-8')) for path in MIGRATIONS
            if path.name not in leaving_out and (through is None or path.name <= through)]


def defining(signature):
    pattern = re.compile(r'create (?:or replace )?function ' + re.escape(signature), re.I)
    return [path for path in MIGRATIONS if pattern.search(path.read_text(encoding='utf-8'))]


class PinRecomputationTests(unittest.TestCase):
    """Pins are recomputed from the source bodies, never compared with a copied hash."""

    def test_every_pin_is_the_prosrc_the_reviewed_chain_installs(self):
        functions, mismatches = replay(sources())
        self.assertEqual(mismatches, [])
        self.assertGreater(sum(len(PIN.findall(text)) for _, text in sources()), 60)
        self.assertIn('quorum_private.native_session(uuid,text)', functions)

    def test_the_tables_in_this_file_are_the_recomputed_prosrc(self):
        serial, _ = replay(sources(through='20261022170000_urgent_message_priority.sql'))
        for signature, pin in SERIALIZATION_PINS.items():
            self.assertEqual(prosrc(serial, signature), pin, signature)
        before_roles, _ = replay(s for s in sources() if s[0] < ROLES)
        for signature, pin in ROLE_PINS.items():
            self.assertEqual(prosrc(before_roles, signature), pin, signature)

    def test_a_changed_body_or_pin_is_caught(self):
        chain = sources()
        index = next(i for i, (name, _) in enumerate(chain) if name == GROK)
        name, text = chain[index]
        changed = text.replace("provider='github'", "provider='gitlab'", 1)
        self.assertNotEqual(text, changed)
        self.assertTrue(replay(chain[:index] + [(name, changed)] + chain[index + 1:])[1])
        last = len(chain) - 1
        name, text = chain[last]
        digest = PIN.findall(text)[0][1]
        flipped = text.replace(digest, digest[::-1], 1)
        self.assertEqual(replay(chain[:last] + [(name, flipped)])[1], [(name, PIN.findall(flipped)[0][0])])


class NativeSessionConvergenceTests(unittest.TestCase):
    PATH = SKILL / 'supabase/migrations' / CONVERGENCE

    def constant(self, name):
        body = self.PATH.read_text(encoding='utf-8')
        return re.search(name + r" constant text:=\$t\$(.*?)\$t\$", body)[1]

    def test_the_applied_chain_keeps_one_body_and_this_file_only_adds_to_it(self):
        names = [path.name for path in MIGRATIONS]
        self.assertEqual(names[names.index(CONVERGENCE) - 1], '20261022160000_open_session_requirements.sql')
        self.assertEqual(names[names.index(CONVERGENCE) + 1], PRIORITY_PATH.name)
        # Every release after the priority file pins the single reviewed body.
        for path in MIGRATIONS:
            pins = dict(PIN.findall(path.read_text(encoding='utf-8')))
            if 'quorum_private.native_session(uuid,text)' in pins:
                self.assertTrue(pins['quorum_private.native_session(uuid,text)'].startswith('60778eaf'), path.name)

    def test_a_server_without_the_grok_file_reaches_the_reviewed_bodies(self):
        reviewed, _ = replay(sources(through=CONVERGENCE))
        skipped, _ = replay(sources(through='20261022160000_open_session_requirements.sql', leaving_out=(GROK,)))
        text = self.PATH.read_text(encoding='utf-8')
        self.assertTrue(text.startswith('-- SOURCE ONLY.'))
        session, wakeup = 'quorum_private.native_session(uuid,text)', 'quorum_private.receive_wakeup(text)'
        self.assertNotEqual(skipped[session], reviewed[session])
        for signature in (session, wakeup):
            self.assertIn(prosrc(skipped, signature), text)
            self.assertIn(prosrc(reviewed, signature), text)
            self.assertEqual(skipped[signature].replace(self.constant('without_grok'), self.constant('with_grok')),
                             reviewed[signature], signature)
        # The two bodies before open access are refused by their exact hashes.
        before_open, _ = replay(sources(through=GROK))
        bootstrap, _ = replay([])
        for functions in (before_open, bootstrap):
            self.assertIn(prosrc(functions, session), text)

    def test_the_guard_changes_nothing_on_the_reviewed_body_and_fails_closed_otherwise(self):
        text = self.PATH.read_text(encoding='utf-8')
        code = text[text.index('begin\n'):]
        self.assertLess(code.index('then return; end if;'), code.index('alter table'))
        self.assertLess(code.index("raise exception 'open_access_required'"), code.index('alter table'))
        self.assertLess(code.index("raise exception 'reviewed_dispatcher_changed: quorum_private.native_session"),
                        code.index('alter table'))
        self.assertEqual(re.findall(r'^\s*(?:create|grant|revoke|drop function)\b', code, re.I | re.M), [])
        guide = (SKILL / 'references/server.md').read_text(encoding='utf-8')
        self.assertLess(guide.index(CONVERGENCE), guide.index(PRIORITY_PATH.name))


class HelloSplitTests(unittest.TestCase):
    PATH = SKILL / 'supabase/migrations' / SPLIT

    def test_split_follows_the_tail_pins_the_chain_and_adds_only_the_count(self):
        names = [path.name for path in MIGRATIONS]
        self.assertEqual(names[names.index(SPLIT) + 1:], [MESSAGE, STATUSES, REPORTS])
        body = self.PATH.read_text(encoding='utf-8')
        self.assertTrue(body.startswith('-- SOURCE ONLY.'))
        wrapper = 'quorum_private.session_heartbeat_before_status(uuid,text,uuid,integer,text)'
        pins = dict(PIN.findall(body))
        self.assertEqual(set(pins), {'public.session_heartbeat(uuid,text,uuid,integer,text,integer,timestamptz,uuid,timestamptz)',
                                     wrapper, 'quorum_private.session_heartbeat_before_hello(uuid,text,uuid,integer,text)',
                                     'quorum_private.call(text,jsonb,text)', 'quorum_private.native_session(uuid,text)',
                                     'public.session_requirements()'})
        guard = body.split('end $migration$;', 1)[0]
        for code in ('hello_split_present', 'task_authority_bound_required'):
            self.assertIn(f"raise exception '{code}'", guard)
        self.assertIn("raise exception 'reviewed_dispatcher_changed: %',f;", guard)
        for change in ('alter ', 'create ', 'drop ', 'insert ', 'update ', 'grant '):
            self.assertNotIn(change, guard.split('begin', 1)[1].lower())
        # The new wrapper is the reviewed one plus exactly the lines marked #146.
        old = replay(sources(through=DEDUPE))[0][wrapper]
        new = body.split('$function$')[1]
        old_lines, new_lines = old.splitlines(), new.splitlines()
        changed = [line for line in new_lines if line not in old_lines]
        self.assertEqual(len(changed), 3)
        self.assertTrue(all(line.endswith('-- #146') for line in changed), changed)
        self.assertEqual(len([line for line in old_lines if line not in new_lines]), 3)
        # Peers, the roster text and the Hello recipients are unchanged, and so is every grant.
        self.assertIn('hello.peers', new)
        self.assertEqual(re.findall(r'quorum roster', old, re.I), re.findall(r'quorum roster', new, re.I))
        code = '\n'.join(line.split('--', 1)[0] for line in body.splitlines())
        self.assertEqual(re.findall(r'grant [^;]*;', code.lower()), [])
        self.assertEqual(re.findall(r'create function ([a-z_.]+)\(', body), ['quorum_private.session_heartbeat_before_status'])
        self.assertIn('revoke all on function quorum_private.session_heartbeat_hello_before_split(', ' '.join(code.split()))
        guide = (SKILL / 'references/server.md').read_text(encoding='utf-8')
        self.assertIn(SPLIT, guide.split('## Hello sent and skipped split', 1)[1].split('\n## ', 1)[0])


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
        # Only the reviewed role-assignment release (#105) follows it.
        self.assertEqual(names[names.index(ttl) + 1:], [ROLES, STATUS, DEFAULT_ROLE, DEDUPE, AUTHORITY, UNRECEIPTED, SPLIT, MESSAGE, STATUSES, REPORTS])
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
        self.assertEqual(names[names.index(ROLES) + 1:], [STATUS, DEFAULT_ROLE, DEDUPE, AUTHORITY, UNRECEIPTED, SPLIT, MESSAGE, STATUSES, REPORTS])
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
        self.assertEqual(names[names.index(STATUS) + 1:], [DEFAULT_ROLE, DEDUPE, AUTHORITY, UNRECEIPTED, SPLIT, MESSAGE, STATUSES, REPORTS])
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


    def test_knowledge_default_role_follows_the_wiki_pins_every_scope_caller_and_grants_only_authenticated(self):
        names = [path.name for path in MIGRATIONS]
        self.assertEqual(names[names.index(DEFAULT_ROLE) + 1:], [DEDUPE, AUTHORITY, UNRECEIPTED, SPLIT, MESSAGE, STATUSES, REPORTS])
        self.assertGreater(DEFAULT_ROLE, '20261103075741_llm_wiki.sql')
        body = (SKILL / 'supabase/migrations' / DEFAULT_ROLE).read_text(encoding='utf-8')
        self.assertTrue(body.startswith('-- SOURCE ONLY.'))
        pins = dict(re.findall(r"'([a-z_.]+\([a-z,\[\]]*\))','([0-9a-f]{64})'", body))
        # Every function that calls the scope check is pinned, so its writer/owner-only arguments are reviewed.
        callers = set()
        for path in MIGRATIONS:
            for chunk in path.read_text(encoding='utf-8').split('create ')[1:]:
                head = re.match(r'(?:or replace )?function (public\.[a-z_]+)\(', chunk)
                if head and 'quorum_private.knowledge_scope(' in chunk.split('$$;', 1)[0]:
                    callers.add(head.group(1))
        self.assertTrue(callers)
        self.assertEqual(callers - {name.split('(')[0] for name in pins}, set())
        for required in ('quorum_private.knowledge_scope(uuid,text,uuid,boolean,boolean)',
                         'public.knowledge_admin(uuid,text,text,jsonb,text)', 'quorum_private.knowledge_binding(uuid,text)',
                         'quorum_private.knowledge_mutation_lock(uuid)', 'quorum_private.knowledge_cached(text,jsonb,text)'):
            self.assertIn(required, pins)
        status = (SKILL / 'supabase/migrations' / STATUS).read_text(encoding='utf-8')
        self.assertIn("'quorum_private.native_session(uuid,text)','" + pins['quorum_private.native_session(uuid,text)'] + "'", status)
        guard = body.split('end $migration$;', 1)[0]
        for code in ('knowledge_default_role_present', 'knowledge_wiki_required'):
            self.assertIn(f"raise exception '{code}'", guard)
        self.assertIn("raise exception 'reviewed_dispatcher_changed: %',f;", guard)
        for change in ('alter ', 'create ', 'drop ', 'insert ', 'update ', 'grant '):
            self.assertNotIn(change, guard.split('begin', 1)[1].lower())
        created = re.findall(r'create function ([a-z_.]+)\(', body)
        self.assertEqual(created, ['quorum_private.knowledge_scope', 'public.knowledge_admin'])
        for chunk in body.split('create function ')[1:]:
            header = chunk.split(' as $', 1)[0]
            self.assertIn('security definer set search_path=pg_catalog', ' '.join(header.split()), header[:60])
        # The copied scope body differs from the reviewed one only by the default lookup.
        reviewed = (SKILL / 'supabase/migrations/20261030142000_project_knowledge.sql').read_text(encoding='utf-8')
        old = reviewed.split('create function quorum_private.knowledge_scope(', 1)[1].split('end $$;', 1)[0]
        new = body.split('create function quorum_private.knowledge_scope(', 1)[1].split('end $function$;', 1)[0]
        old_lines = old.replace('$$', '$function$').splitlines()
        added = [line.strip() for line in new.splitlines() if line not in old_lines]
        self.assertEqual([line for line in added if not line.startswith('--')], [
            'if not found then',
            'select permission into permitted from quorum_private.knowledge_member_defaults',
            'where space_id=s for share;',
            "if not found then raise insufficient_privilege using message='knowledge_denied'; end if;",
            'end if;',
            "if writer and permitted<>'writer' then"])
        # Only the explicit-grant denial moved inside the fallback; no reviewed check was dropped.
        removed = [line.strip() for line in old_lines if line not in new.splitlines()]
        self.assertEqual(removed, ["if not found or (writer and permitted<>'writer') then"])
        code = '\n'.join(line.split('--', 1)[0] for line in body.splitlines())
        lowered = ' '.join(code.lower().split())
        for moved in ('knowledge_scope_before_default_role(uuid,text,uuid,boolean,boolean)',
                      'knowledge_admin_before_default_role(uuid,text,text,jsonb,text)',
                      'knowledge_scope(uuid,text,uuid,boolean,boolean)'):
            self.assertIn(f'revoke all on function quorum_private.{moved} from public,anon,authenticated,service_role;', lowered)
        self.assertIn('revoke all on quorum_private.knowledge_member_defaults from public,anon,authenticated,service_role;', lowered)
        grants = re.findall(r'grant [^;]*;', lowered)
        self.assertEqual(grants, ['grant execute on function public.knowledge_admin(uuid,text,text,jsonb,text) to authenticated;'])
        self.assertNotIn('function public.session_requirements', body)
        guide = (SKILL / 'references/server.md').read_text(encoding='utf-8')
        section = guide.split('## Knowledge member default role', 1)[1].split('\n## ', 1)[0]
        self.assertIn(DEFAULT_ROLE, section)
        self.assertIn('after `20261103075741_llm_wiki.sql`', section)


    def test_task_authority_bound_follows_dedupe_pins_the_chain_and_changes_only_the_bound(self):
        names = [path.name for path in MIGRATIONS]
        self.assertEqual(names[names.index(AUTHORITY) + 1:], [UNRECEIPTED, SPLIT, MESSAGE, STATUSES, REPORTS])
        body = (SKILL / 'supabase/migrations' / AUTHORITY).read_text(encoding='utf-8')
        self.assertTrue(body.startswith('-- SOURCE ONLY.'))
        pins = dict(re.findall(r"'([a-z_.]+\([a-z,]*\))','([0-9a-f]{64})'", body))
        authority = 'public.session_task_authority(uuid,text,uuid)'
        self.assertEqual(set(pins), {authority, 'quorum_private.native_session(uuid,text)', 'public.session_requirements()'})
        # The pin is the prosrc the chain installs: nothing after 141000 replaces it, and bootstrap holds the same body.
        reviewed = SKILL / 'supabase/migrations/20261030141000_member_task_authority.sql'
        self.assertEqual(defining('public.session_task_authority('), [reviewed, SKILL / 'supabase/migrations' / AUTHORITY])
        old = reviewed.read_text(encoding='utf-8').split('$function$')[1]
        bootstrap = (SKILL / 'references/server-bootstrap.sql').read_text(encoding='utf-8')
        self.assertEqual(bootstrap.split('function public.session_task_authority(', 1)[1].split('$function$')[1], old)
        self.assertEqual(pins[authority], hashlib.sha256(old.encode()).hexdigest())
        dedupe = (SKILL / 'supabase/migrations' / DEDUPE).read_text(encoding='utf-8')
        dedupe_pins = dict(re.findall(r"'([a-z_.]+\([a-z,]*\))','([0-9a-f]{64})'", dedupe))
        for name in ('quorum_private.native_session(uuid,text)', 'public.session_requirements()'):
            self.assertEqual(pins[name], dedupe_pins[name], name)
        guard = body.split('end $migration$;', 1)[0]
        for code in ('task_authority_bound_present', 'hello_dedupe_required'):
            self.assertIn(f"raise exception '{code}'", guard)
        self.assertIn("raise exception 'reviewed_dispatcher_changed: %',f;", guard)
        for change in ('alter ', 'create ', 'drop ', 'insert ', 'update ', 'grant '):
            self.assertNotIn(change, guard.split('begin', 1)[1].lower())
        # The only difference from the reviewed body is the bound.
        new = body.split('$function$')[1]
        old_lines, new_lines = old.splitlines(), new.splitlines()
        self.assertEqual([line.strip() for line in new_lines if line not in old_lines],
                         ['if project is null or octet_length(project) not between 1 and 500 -- #143'])
        self.assertEqual([line.strip() for line in old_lines if line not in new_lines],
                         ['if project is null or octet_length(project) not between 1 and 256'])
        code = '\n'.join(line.split('--', 1)[0] for line in body.splitlines())
        lowered = ' '.join(code.lower().split())
        self.assertEqual(re.findall(r'create function ([a-z_.]+)\(', body), ['public.session_task_authority'])
        self.assertIn('revoke all on function quorum_private.session_task_authority_before_project_bound(uuid,text,uuid) '
                      'from public,anon,authenticated,service_role;', lowered)
        self.assertEqual(re.findall(r'grant [^;]*;', lowered),
                         ['grant execute on function public.session_task_authority(uuid,text,uuid) to authenticated;'])
        self.assertNotIn('function public.session_requirements', body)
        guide = (SKILL / 'references/server.md').read_text(encoding='utf-8')
        section = guide.split('## Task authority project bound', 1)[1].split('\n## ', 1)[0]
        self.assertIn(AUTHORITY, section)
        self.assertIn('after `20261106120000_hello_reconnect_dedupe.sql`', section)

    def test_hello_dedupe_follows_status_pins_reviewed_bodies_and_grants_nothing(self):
        names = [path.name for path in MIGRATIONS]
        self.assertEqual(names[names.index(DEDUPE) + 1:], [AUTHORITY, UNRECEIPTED, SPLIT, MESSAGE, STATUSES, REPORTS])
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

    def test_message_unreceipted_is_read_only_pinned_and_grants_only_authenticated(self):
        names = [path.name for path in MIGRATIONS]
        self.assertEqual(names[names.index(UNRECEIPTED) + 1:], [SPLIT, MESSAGE, STATUSES, REPORTS])
        body = (SKILL / 'supabase/migrations' / UNRECEIPTED).read_text(encoding='utf-8')
        self.assertIn('SOURCE ONLY', body)
        self.assertIn('No required-release change', body)
        self.assertEqual(defining('public.session_message_unreceipted('), [SKILL / 'supabase/migrations' / UNRECEIPTED])
        for pin in ('quorum_private.session_inbox_before_updates(uuid,text)', 'quorum_private.native_session(uuid,text)',
                    'public.session_task_authority(uuid,text,uuid)', 'public.session_requirements()'):
            self.assertIn(pin, body.split('begin', 1)[0])
        self.assertIn("raise exception 'message_unreceipted_present'", body)
        self.assertIn("raise exception 'task_authority_bound_required'", body)
        # The task authority pin is the 500-bound body the predecessor installs.
        predecessor = (SKILL / 'supabase/migrations' / AUTHORITY).read_text(encoding='utf-8')
        source = predecessor.split('$function$')[1]
        self.assertIn(hashlib.sha256(source.encode()).hexdigest(), body)
        self.assertIn("raise exception 'reviewed_dispatcher_changed: %',f", body)
        function = body.split('$function$')[1]
        self.assertIn('quorum_private.native_session(p_agent_id,p_session_ref)', function)
        self.assertNotRegex(function, r'(?i)\b(insert|update|delete)\b')
        self.assertNotIn('m.body', function)
        self.assertIn('from public,anon,authenticated,service_role', body)
        self.assertRegex(body, r'grant execute on function public\.session_message_unreceipted\(uuid,text,uuid\) to authenticated;')
        # The pin is the inbox body this predicate copies, as owner maintenance recorded it.
        maintenance = next(path for path in MIGRATIONS if path.name.endswith('_owner_update_maintenance.sql'))
        pinned = re.search(r"session_inbox_before_updates\(uuid,text\)','([0-9a-f]{64})'", body).group(1)
        self.assertIn("'public.session_inbox(uuid,text)','" + pinned + "'", maintenance.read_text(encoding='utf-8'))
        guide = (SKILL / 'references/server.md').read_text(encoding='utf-8')
        section = guide.split('## Exact unreceipted lookup', 1)[1].split('\n## ', 1)[0]
        self.assertIn(UNRECEIPTED, section)
        self.assertIn('after `20261106130000_task_authority_project_bound.sql`', section)

    def test_session_message_is_read_only_own_recipient_and_grants_only_authenticated(self):
        names = [path.name for path in MIGRATIONS]
        self.assertEqual(names[names.index(MESSAGE) + 1:], [STATUSES, REPORTS])
        body = (SKILL / 'supabase/migrations' / MESSAGE).read_text(encoding='utf-8')
        self.assertIn('SOURCE ONLY', body)
        self.assertIn('No required-release change', body)
        self.assertEqual(defining('public.session_message('), [SKILL / 'supabase/migrations' / MESSAGE])
        for pin in ('quorum_private.native_session(uuid,text)', 'public.session_requirements()'):
            self.assertIn(pin, body.split('begin', 1)[0])
        self.assertIn("raise exception 'session_message_present'", body)
        self.assertIn("raise exception 'hello_split_required'", body)
        function = body.split('$function$')[1]
        self.assertIn('quorum_private.native_session(p_agent_id,p_session_ref)', function)
        self.assertIn('m.quorum_id=q and m.recipient_id=p_agent_id', function)
        self.assertNotRegex(function, r'(?i)\b(insert|update|delete)\b')
        self.assertIn('from public,anon,authenticated,service_role', body)
        self.assertRegex(body, r'grant execute on function public\.session_message\(uuid,text,uuid\) to authenticated;')
        guide = (SKILL / 'references/server.md').read_text(encoding='utf-8')
        section = guide.split('## Single-message read', 1)[1].split('\n## ', 1)[0]
        self.assertIn(MESSAGE, section)
        self.assertIn('after `20261106150000_hello_sent_skipped.sql`', section)

    def test_delivery_statuses_follow_the_message_read_pin_their_bodies_and_replace_nothing(self):
        names = [path.name for path in MIGRATIONS]
        self.assertEqual(names[names.index(STATUSES) + 1:], [REPORTS])
        body = (SKILL / 'supabase/migrations' / STATUSES).read_text(encoding='utf-8')
        self.assertIn('SOURCE ONLY', body)
        self.assertIn('No required-release change', body)
        guard = body.split('create table', 1)[0]
        for pin in ('quorum_private.native_session(uuid,text)', 'public.session_reply(uuid,text,uuid,text,text)',
                    'public.session_requirements()'):
            self.assertIn(pin, guard)
        self.assertIn("raise exception 'delivery_status_present'", guard)
        self.assertIn("raise exception 'session_message_required'", guard)
        self.assertIn("raise exception 'reviewed_dispatcher_changed: %',f", guard)
        # The pin is the reply body the priority release installed.
        priority = PRIORITY_PATH.read_text(encoding='utf-8')
        reply = priority.split('create function public.session_reply(', 1)[1].split(' as $$', 1)[1].split('$$;', 1)[0]
        self.assertIn(hashlib.sha256(reply.encode()).hexdigest(), guard)
        # New RPCs only: no dispatcher, heartbeat, inbox or receipt body moves or is replaced.
        code = '\n'.join(line.split('--', 1)[0] for line in body.splitlines())
        for moved in ('rename to', 'set schema', 'create or replace', 'drop function', 'session_heartbeat',
                      'quorum_private.call', 'session_inbox', 'session_ack'):
            self.assertNotIn(moved, code)
        self.assertEqual(set(re.findall(r'create function (public\.\w+)', code)),
                         {'public.session_delivery_mark', 'public.session_delivery_status',
                          'public.session_delivery_changes', 'public.session_delivery_stuck'})
        self.assertEqual(defining('public.session_delivery_mark('), [SKILL / 'supabase/migrations' / STATUSES])
        for function in re.findall(r'create function public\..*?\$function\$(.*?)\$function\$', code, re.S):
            self.assertIn('quorum_private.native_session(p_agent_id,p_session_ref)', function)
        self.assertEqual(len(re.findall(r'security definer set search_path=pg_catalog', code)), 8)
        self.assertRegex(code, r'grant execute on function public\.session_delivery_mark')
        self.assertRegex(code, r'public,anon,authenticated,service_role')
        # Empty hint, like the inbox, and no table grant at all.
        self.assertIn("realtime.send('{}'::jsonb,'status_changed',", code)
        self.assertNotRegex(code, r'(?i)grant [^;]* on quorum_private\.delivery')
        guide = (SKILL / 'references/server.md').read_text(encoding='utf-8')
        section = guide.split('## Delivery statuses', 1)[1].split('\n## ', 1)[0]
        self.assertIn(STATUSES, section)
        self.assertIn('after `20261106160000_session_message.sql`', section)

    def test_release_reports_follow_the_statuses_pin_their_inputs_and_change_no_existing_function(self):
        names = [path.name for path in MIGRATIONS]
        self.assertEqual(names[-1], REPORTS)
        body = (SKILL / 'supabase/migrations' / REPORTS).read_text(encoding='utf-8')
        self.assertTrue(body.startswith('-- SOURCE ONLY.'))
        code = '\n'.join(line for line in body.splitlines() if not line.startswith('--'))
        self.assertEqual(re.findall(r'^alter function', code, re.M), [])
        self.assertEqual(set(re.findall(r'create function (public\.\w+)', code)),
                         {'public.session_release_report', 'public.session_release_status'})
        self.assertEqual(len(re.findall(r'^create (?:or replace )?function', code, re.M)), 2)
        pins = dict(PIN.findall(body))
        self.assertEqual(set(pins), {'quorum_private.native_session(uuid,text)', 'public.session_requirements()',
                                     'public.session_update_request(uuid,text,uuid,integer,text,text,text)',
                                     'public.session_update_report(uuid,text,uuid,uuid,text,jsonb,text)'})
        self.assertEqual(pins['public.session_requirements()'], SERIALIZATION_PINS['public.session_requirements()'])
        self.assertLess(code.index("raise exception 'release_reports_present'"), code.index('create table'))
        self.assertLess(code.index("raise exception 'delivery_statuses_required'"), code.index('create table'))
        # A hash is a fact: nothing here gates, and no heartbeat or requirement body is touched.
        for function in re.findall(r'create function public\..*?\$function\$(.*?)\$function\$', code, re.S):
            self.assertIn('quorum_private.native_session(p_agent_id,p_session_ref)', function)
            self.assertNotIn('skill_version', function)
            self.assertNotIn('skill_update_required', function)
        self.assertEqual(len(re.findall(r'security definer set search_path=pg_catalog', code)), 2)
        self.assertRegex(code, r'from public,anon,authenticated,service_role')
        self.assertRegex(code, r'grant execute on function public\.session_release_report')
        self.assertNotRegex(code, r'(?i)grant [^;]* on quorum_private\.session_release')
        guide = (SKILL / 'references/server.md').read_text(encoding='utf-8')
        section = guide.split('## Release reports', 1)[1].split('\n## ', 1)[0]
        self.assertIn(REPORTS, section)
        self.assertIn('after `20261106170000_delivery_statuses.sql`', section)


if __name__ == '__main__':
    unittest.main()
