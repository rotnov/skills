"""The SQL baseline is tied to the history it covers (#165, review step 6). No database, no Docker.

test_quorum_baseline_sql.py proves on PostgreSQL that the baseline installs the same catalog as the history.
This file keeps that proof honest without a database: the history the baseline covers is unchanged, and every
function prosrc hash the adopt check expects is the one the reviewed history sources install.
"""
import hang_guard  # noqa: F401
import re
import unittest

import quorum_baseline_support as baseline
from test_quorum_migration_order import SKILL, apply_source, BOOTSTRAP, prosrc, sources

DROP = re.compile(r'^\s*drop function (?:if exists )?([a-z_]+\.[a-z_0-9]+\([^)]*\))', re.I | re.M)
EXPECTED = re.compile(r"^\('(core|knowledge)','function','([^']*)','[0-9a-f]{64}','([0-9a-f]{64})'\)", re.M)
FILES = (baseline.CORE_FILE, baseline.KNOWLEDGE_FILE, baseline.ADOPT_FILE)


def installed():
    """Every function the history through the baseline leaves in the database: replayed, minus dropped ones."""
    functions = {}
    apply_source(functions, BOOTSTRAP)
    for name, text in sources():
        if name[:14] > baseline.COVERS: continue
        # A drop is a statement boundary: replay up to it, drop, go on.
        position = 0
        for found in DROP.finditer(text):
            apply_source(functions, text[position:found.start()])
            functions.pop(found[1].replace(' ', '').lower(), None)
            position = found.end()
        apply_source(functions, text[position:])
    return {signature: prosrc(functions, signature) for signature in functions if not signature.startswith('pg_temp.')}


def signature(identity):
    return identity.replace(', ', ',').replace('timestamp with time zone', 'timestamptz')


class BaselineSourcesTests(unittest.TestCase):
    def test_the_baseline_names_the_history_it_covers_and_that_history_is_unchanged(self):
        self.assertIn(baseline.COVERS, [path.name[:14] for path in baseline.MIGRATIONS.glob('*.sql')])
        for path in FILES:
            text = path.read_text(encoding='utf-8')
            self.assertTrue(text.startswith('-- GENERATED. Do not edit.'), path.name)
            self.assertIn(f'through {baseline.COVERS}.', text.splitlines()[1], path.name)
            self.assertEqual(baseline.header_sources(path), baseline.sources_sha256(), path.name)
        self.assertEqual(sorted(path.name for path in baseline.BASELINE.iterdir()), sorted(path.name for path in FILES))

    def test_every_expected_prosrc_is_the_one_the_history_installs(self):
        expected = {signature(identity): digest for _, identity, digest in EXPECTED.findall(
            baseline.ADOPT_FILE.read_text(encoding='utf-8'))}
        self.assertGreater(len(expected), 100)
        self.assertEqual(expected, installed())

    def test_core_and_knowledge_split_the_functions_without_overlap(self):
        parts = {}
        for part, identity, _ in EXPECTED.findall(baseline.ADOPT_FILE.read_text(encoding='utf-8')):
            parts.setdefault(part, set()).add(identity)
        self.assertFalse(parts['core'] & parts['knowledge'])
        self.assertTrue(all('knowledge' in name for name in parts['knowledge']), parts['knowledge'])
        core = baseline.CORE_FILE.read_text(encoding='utf-8')
        knowledge = baseline.KNOWLEDGE_FILE.read_text(encoding='utf-8')
        self.assertEqual(core.count('\nCREATE FUNCTION '), len(parts['core']))
        self.assertEqual(knowledge.count('\nCREATE FUNCTION '), len(parts['knowledge']))
        self.assertNotIn('CREATE EXTENSION IF NOT EXISTS vector', core)
        self.assertIn('CREATE EXTENSION IF NOT EXISTS vector WITH SCHEMA extensions;', knowledge)

    def test_install_files_refuse_an_existing_schema_and_adopt_writes_only_its_record(self):
        core = baseline.CORE_FILE.read_text(encoding='utf-8')
        self.assertIn("RAISE EXCEPTION 'quorum_schema_present'", core.split('CREATE SCHEMA', 1)[0])
        knowledge = baseline.KNOWLEDGE_FILE.read_text(encoding='utf-8')
        self.assertIn("RAISE EXCEPTION 'quorum_core_baseline_required'", knowledge.split('CREATE ', 1)[0])
        adopt = baseline.ADOPT_FILE.read_text(encoding='utf-8')
        check, record = adopt.split('END $adopt$;', 1)
        for change in ('create ', 'alter ', 'drop ', 'insert ', 'update ', 'delete ', 'grant ', 'revoke '):
            self.assertNotIn(change, check.lower().split('do $adopt$', 1)[1], change)
        self.assertEqual(record.strip(), (baseline.MARKER + record.split(baseline.MARKER, 1)[1]).strip())
        self.assertEqual(re.findall(r'^(create|alter|revoke|insert)\b', record, re.M),
                         ['create', 'alter', 'revoke', 'insert'])
        # Every ACL a baseline file sets starts from nothing for each Supabase API role.
        for text in (core, knowledge):
            for line in text.splitlines():
                if line.startswith('REVOKE '): self.assertTrue(line.endswith(f' FROM {baseline.API_ROLES};'), line)

    def test_the_server_guide_routes_fresh_installs_to_the_baseline_and_existing_ones_to_adopt(self):
        guide = (SKILL / 'references/server.md').read_text(encoding='utf-8')
        fresh = guide.split('## Fresh project', 1)[1].split('\n## ', 1)[0]
        for path in (baseline.CORE_FILE, baseline.KNOWLEDGE_FILE):
            self.assertIn(path.name, fresh)
        window = guide.split('## Baseline maintenance window', 1)[1].split('\n## ', 1)[0]
        self.assertIn(baseline.ADOPT_FILE.name, window)
        for step in ('quorum_baseline_mismatch', 'sha256', 'rollback', 'drop table quorum_private.schema_baseline'):
            self.assertIn(step, window.lower() if step == 'rollback' else window)


if __name__ == '__main__':
    unittest.main()
