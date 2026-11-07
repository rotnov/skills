"""session-transport.sql must not downgrade a migrated server (#145).

QUORUM_TRANSPORT_DOCKER=1 uses only the existing local PostgreSQL 17 image, with no network, and removes its
own container. No real service configuration or credentials are read.
"""
import hang_guard  # noqa: F401
import os
from pathlib import Path
import unittest

from test_quorum_close_race import Cluster, docker

ROOT = Path(__file__).resolve().parents[1]
SKILL = ROOT / 'skills/quorum'
SCRIPT = SKILL / 'references/session-transport.sql'
GROK = '20261022094041_native_grok_session.sql'
PRIORITY = '20261022170000_urgent_message_priority.sql'
FUNCTIONS = """select string_agg(p.oid::regprocedure::text||md5(pg_get_functiondef(p.oid)),',' order by p.oid::regprocedure::text)
  from pg_proc p join pg_namespace n on n.oid=p.pronamespace
  where n.nspname in ('public','quorum_private') and p.proname like '%session%' and p.prokind='f'"""


class ScriptShapeTests(unittest.TestCase):
    def test_refusal_runs_first_and_the_whole_file_is_one_transaction(self):
        statements = [line for line in SCRIPT.read_text(encoding='utf-8').splitlines()
                      if line and not line.startswith('--')]
        self.assertEqual((statements[0], statements[1], statements[-1]), ('begin;', 'do $$', 'commit;'))
        text = SCRIPT.read_text(encoding='utf-8')
        self.assertLess(text.index('session_transport_superseded'), text.index('create table'))

    def test_the_guard_names_what_the_migrations_add(self):
        text = SCRIPT.read_text(encoding='utf-8')
        migrations = SKILL / 'supabase/migrations'
        self.assertIn("public.session_reply(uuid,text,uuid,text,text)", text)
        self.assertIn('session_reply(uuid,text,uuid,text,text)', (migrations / PRIORITY).read_text(encoding='utf-8'))
        self.assertIn("like '%grok%'", text)
        self.assertIn("'grok'", (migrations / GROK).read_text(encoding='utf-8'))


@unittest.skipUnless(os.environ.get('QUORUM_TRANSPORT_DOCKER') == '1', 'opt-in disposable PostgreSQL test')
class ReapplyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.cluster = Cluster().__enter__()
        cls.addClassCleanup(cls.cluster.__exit__)
        baseline = (ROOT / 'tests/quorum_backend_fixture.sql').read_text(encoding='utf-8')
        baseline += (SKILL / 'references/server-bootstrap.sql').read_text(encoding='utf-8')
        cls.cluster.sql('postgres', 'create database legacy')
        cls.cluster.sql('legacy', baseline, single=True)
        names = sorted(path.name for path in (SKILL / 'supabase/migrations').glob('*.sql'))
        for database, last in (('grok', GROK), ('priority', PRIORITY)):
            script = ''.join((SKILL / 'supabase/migrations' / name).read_text(encoding='utf-8')
                             for name in names if name <= last)
            cls.cluster.sql('postgres', f'create database {database} template legacy')
            cls.cluster.sql(database, script, single=True)

    def apply_like_a_client_that_continues_after_errors(self, database):
        """No ON_ERROR_STOP and no -1: the file's own transaction is the only protection."""
        return docker('exec', '-i', self.cluster.name, 'psql', '-X', '-q', '-At', '-U', 'postgres', '-d', database,
                      stdin=SCRIPT.read_text(encoding='utf-8'), check=False)

    def test_a_migrated_server_refuses_and_nothing_changes(self):
        for database in ('grok', 'priority'):
            with self.subTest(database=database):
                before = self.cluster.sql(database, FUNCTIONS)
                result = self.apply_like_a_client_that_continues_after_errors(database)
                self.assertIn('session_transport_superseded', result.stderr)
                self.assertEqual(self.cluster.sql(database, FUNCTIONS), before)

    def test_an_older_server_still_applies_it(self):
        result = self.apply_like_a_client_that_continues_after_errors('legacy')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotIn('ERROR', result.stderr)
        self.assertEqual(self.cluster.sql('legacy', "select to_regprocedure('public.session_inbox(uuid,text)') is not null")
                         .strip(), 't')


if __name__ == '__main__':
    unittest.main()
