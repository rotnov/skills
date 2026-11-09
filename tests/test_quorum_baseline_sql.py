"""The SQL baseline installs what the history installs (#165, review step 6), on real PostgreSQL 17.

QUORUM_BASELINE_DOCKER=1 uses the already fetched pgvector image pinned by OCI digest, with no network and no
published port, and removes its container. Every database starts from the synthetic Auth/Realtime fixture plus
Supabase's default privileges in schema public, so grants are compared as a Supabase project would hold them.

Equivalence is shown three ways on fresh databases: every pg_dump catalog entry, the adopt manifest (functions
with their prosrc SHA-256, relations with columns, constraints, indexes, triggers, policies and grants), and the
behaviour suites that hold on the current release. A history server that runs the adopt script matches a baseline
install entry for entry; before it, the bookkeeping table is the only difference.
"""
import hang_guard  # noqa: F401
import json
import os
from pathlib import Path
import unittest

import quorum_baseline_support as baseline

ROOT = Path(__file__).resolve().parents[1]
CURRENT = json.loads((ROOT / 'skills/quorum/assets/release.json').read_text())
# The SQL suites that hold on the current release (tests/quorum-backend/run.mjs: PRESENCE_COMPATIBLE).
SUITES = ('quorum_invite_id_backend.sql', 'quorum_session_backend.sql', 'quorum_realtime_backend.sql',
          'quorum_open_invite_ttl_backend.sql', 'quorum_delivery_status_backend.sql',
          'quorum_session_message_backend.sql', 'quorum_release_reports_backend.sql', 'quorum_presence_backend.sql')
# What the bookkeeping adds: the table, its key and its row security.
MARKER_ENTRIES = {('TABLE', 'quorum_private', 'schema_baseline'),
                  ('CONSTRAINT', 'quorum_private', 'schema_baseline schema_baseline_pkey'),
                  ('ROW SECURITY', 'quorum_private', 'schema_baseline')}


@unittest.skipUnless(os.environ.get('QUORUM_BASELINE_DOCKER') == '1', 'opt-in disposable PostgreSQL baseline')
class BaselineEquivalenceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server, patcher = baseline.cluster()
        cls.addClassCleanup(patcher.stop)
        cls.addClassCleanup(cls.server.__exit__)
        core, full = baseline.CORE_FILE, baseline.KNOWLEDGE_FILE
        for name, files in (('history_core', baseline.history(knowledge=False) + baseline.later(knowledge=False)),
                            ('history_full', baseline.history() + baseline.later()),
                            ('baseline_core', [core] + baseline.later(knowledge=False)),
                            ('baseline_full', [core, full] + baseline.later())):
            baseline.build(cls.server, name, files)
        cls.dumps = {name: baseline.entries(baseline.dump(cls.server, name)) for name in
                     ('history_core', 'history_full', 'baseline_core', 'baseline_full')}
        for part in ('core', 'full'):
            cls.server.sql('postgres', f'create database adopted_{part} template history_{part}')
            cls.server.sql(f'adopted_{part}', baseline.ADOPT_FILE.read_text(), single=True)

    def sql(self, db, script, single=True):
        return self.server.sql(db, script, single=single)

    def refused(self, db, script, code):
        with self.assertRaises(RuntimeError) as raised:
            self.sql(db, script)
        self.assertIn(code, str(raised.exception))
        return str(raised.exception)

    def copy(self, source, name):
        self.server.sql('postgres', f'create database {name} template {source}')
        return name

    def test_the_committed_files_are_what_the_history_generates(self):
        for path, text in baseline.generate(self.server).items():
            self.assertEqual(path.read_text(encoding='utf-8'), text, f'{path.name}: run quorum_baseline_support.py --write')

    def test_the_baseline_and_the_history_dump_the_same_catalog_except_the_bookkeeping(self):
        for part in ('core', 'full'):
            history, installed = self.dumps[f'history_{part}'], self.dumps[f'baseline_{part}']
            self.assertEqual(set(installed) - set(history), MARKER_ENTRIES)
            self.assertEqual(set(history) - set(installed), set())
            changed = [key for key in history if history[key] != installed[key]]
            self.assertEqual(changed, [])
            self.assertGreater(len(history), 300)

    def test_an_adopted_history_server_dumps_exactly_the_baseline(self):
        for part in ('core', 'full'):
            self.assertEqual(baseline.entries(baseline.dump(self.server, f'adopted_{part}')),
                             self.dumps[f'baseline_{part}'])
            records = "select string_agg(part||':'||covers_through||':'||installed_by,',' order by part) " \
                      "from quorum_private.schema_baseline"
            parts = ['core'] if part == 'core' else ['core', 'knowledge']
            self.assertEqual(self.sql(f'adopted_{part}', records).strip(),
                             ','.join(f'{name}:{baseline.COVERS}:adopt' for name in parts))
            self.assertEqual(self.sql(f'baseline_{part}', records).strip(),
                             ','.join(f'{name}:{baseline.COVERS}:baseline' for name in parts))

    def test_the_manifest_rows_match_and_every_table_starts_empty(self):
        for part in ('core', 'full'):
            self.assertEqual(baseline.rows(self.server, f'history_{part}'), baseline.rows(self.server, f'baseline_{part}'))
            for db in (f'history_{part}', f'baseline_{part}'):
                counts = self.sql(db, """select string_agg(c.relname||'='||(xpath('/row/n/text()',
                    query_to_xml(format('select count(*) n from %I.%I',n.nspname,c.relname),false,true,'')))[1]::text,',')
                    from pg_class c join pg_namespace n on n.oid=c.relnamespace
                    where n.nspname='quorum_private' and c.relkind='r' and c.relname<>'schema_baseline'""").strip()
                self.assertTrue(counts and all(item.endswith('=0') for item in counts.split(',')), counts)
                sequences = self.sql(db, "select string_agg(sequencename||':'||coalesce(last_value::text,'-'),',' "
                                         "order by sequencename) from pg_sequences where schemaname='quorum_private'").strip()
                self.assertEqual(sequences, 'audit_events_id_seq:-,context_versions_id_seq:-')

    def test_the_current_behaviour_suites_pass_on_the_baseline(self):
        release = f"select set_config('quorum_test.release','{json.dumps(CURRENT)}',true);"
        script = ''.join((ROOT / 'tests' / name).read_text() for name in SUITES)
        # Sequences do not roll back, so the suites run on copies.
        for db in ('baseline_core', 'history_core'):
            self.sql(self.copy(db, 'suites_' + db), 'begin;' + release + script + 'rollback;', single=False)
        # The knowledge suite commits its fixtures, so it runs on a copy.
        out = self.sql(self.copy('baseline_full', 'knowledge_suite'), (ROOT / 'tests/quorum_knowledge_backend.sql').read_text())
        self.assertIn('knowledge_contract_green', out)

    def test_install_and_adopt_refuse_the_wrong_server_and_change_nothing(self):
        self.refused('history_core', baseline.CORE_FILE.read_text(), 'quorum_schema_present')
        self.refused('adopted_core', baseline.ADOPT_FILE.read_text(), 'quorum_baseline_present')
        self.refused('baseline_core', baseline.ADOPT_FILE.read_text(), 'quorum_baseline_present')
        self.refused('baseline_full', baseline.KNOWLEDGE_FILE.read_text(), 'quorum_knowledge_baseline_present')
        baseline.build(self.server, 'fixture_only', [])
        self.refused('fixture_only', baseline.KNOWLEDGE_FILE.read_text(), 'quorum_core_baseline_required')
        self.assertEqual(self.sql('fixture_only', "select to_regnamespace('quorum_private') is null").strip(), 't')

    def test_adopt_names_each_drifted_object_and_records_nothing(self):
        db = self.copy('history_core', 'drifted')
        self.sql(db, r"""
          do $$ begin execute replace(pg_get_functiondef('public.session_requirements()'::regprocedure),
            '''protocol_version'',2','''protocol_version'',3'); end $$;
          create table quorum_private.hotfix(id integer);
          drop function public.session_message_unreceipted(uuid,text,uuid);
          alter table quorum_private.messages alter column body drop not null;
          grant execute on function public.session_peers(uuid,text) to anon;
        """)
        error = self.refused(db, baseline.ADOPT_FILE.read_text(), 'quorum_baseline_mismatch: 5 differences')
        for line in ('prosrc function public.session_requirements()', 'unexpected relation quorum_private.hotfix',
                     'missing function public.session_message_unreceipted(uuid, text, uuid)',
                     'definition relation quorum_private.messages', 'definition function public.session_peers(uuid, text)'):
            self.assertIn(line, error)
        self.assertEqual(self.sql(db, "select to_regclass('quorum_private.schema_baseline') is null").strip(), 't')

    def test_adopt_refuses_a_partial_knowledge_deployment(self):
        db = self.copy('history_core', 'partial_knowledge')
        knowledge = next(path for path in baseline.MIGRATIONS.glob('*.sql') if path.name.endswith('_project_knowledge.sql'))
        self.sql(db, knowledge.read_text())
        self.refused(db, baseline.ADOPT_FILE.read_text(), 'quorum_baseline_mismatch')

    def test_on_plain_postgresql_the_baseline_differs_only_by_service_role_grants(self):
        # Without Supabase's default privileges the history never grants service_role what it did not name; the
        # baseline grants what Supabase left. This is the one documented difference outside Supabase.
        baseline.build(self.server, 'plain_history', baseline.history() + baseline.later(), defaults=False)
        baseline.build(self.server, 'plain_baseline', [baseline.CORE_FILE, baseline.KNOWLEDGE_FILE] + baseline.later(),
                       defaults=False)
        history = baseline.entries(baseline.dump(self.server, 'plain_history'))
        installed = baseline.entries(baseline.dump(self.server, 'plain_baseline'))
        self.assertEqual(set(installed) - set(history), MARKER_ENTRIES)
        extra = set()
        for key in history:
            if history[key] == installed[key]: continue
            self.assertEqual(key[0], 'ACL', key)
            added = set(installed[key].splitlines()) - set(history[key].splitlines())
            self.assertEqual(set(history[key].splitlines()) - set(installed[key].splitlines()), set(), key)
            self.assertTrue(added and all(line.endswith(' TO service_role;') for line in added), key)
            extra |= added
        functions = sorted(line.split('FUNCTION ', 1)[1].split('(', 1)[0] for line in extra)
        self.assertEqual(functions, ['public.invite_accept_id', 'public.invite_revoke_id', 'public.message_send',
                                     'public.quorum_set_access', 'public.session_reply', 'public.session_requirements'])


if __name__ == '__main__':
    unittest.main()
