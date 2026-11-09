"""One SQL baseline for the Quorum server (#165, review step 6): generate it, describe a catalog, compare two.

The history (references/server-bootstrap.sql and supabase/migrations) stays the record that existing servers
were built from. skills/quorum/supabase/baseline/ holds a snapshot of what that history installs through one
migration (COVERS). It is generated here from disposable PostgreSQL 17, never written by hand:

    QUORUM_BASELINE_DOCKER=1 uv run --frozen python tests/quorum_baseline_support.py --write

Both paths run in a database that emulates Supabase: the Auth/Realtime fixture plus Supabase's default
privileges in schema public. The image is the digest-pinned pgvector image the knowledge suites use, so the
dump is byte-for-byte reproducible. Local image only, no network, no published port, container removed after.
"""
import hashlib
import json
import os
from pathlib import Path
import re
import sys
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
SKILL = ROOT / 'skills/quorum'
MIGRATIONS = SKILL / 'supabase/migrations'
BOOTSTRAP = SKILL / 'references/server-bootstrap.sql'
FIXTURE = ROOT / 'tests/quorum_backend_fixture.sql'
BASELINE = SKILL / 'supabase/baseline'
IMAGE = 'pgvector/pgvector@sha256:ac08538c6f8b9904c33c8224c5e5706dbe760aca29db1d096972b4052c22a75d'
# The optional pgvector extension migrations; everything else is the core.
KNOWLEDGE = ('_project_knowledge.sql', '_llm_wiki.sql', '_knowledge_member_default_role.sql')
# The last history file the baseline covers. Re-baselining is a reviewed change of this one constant.
COVERS = '20261108140000'
CORE_FILE = BASELINE / f'{COVERS}_baseline.sql'
KNOWLEDGE_FILE = BASELINE / f'{COVERS}_baseline_knowledge.sql'
ADOPT_FILE = BASELINE / f'{COVERS}_adopt.sql'

# Supabase grants every new object in schema public to its API roles. The history revokes what it must; the
# baseline revokes from all four and grants back exactly what the history left on Supabase.
SUPABASE_DEFAULTS = '\n'.join(
    f'alter default privileges in schema public grant all on {kind} to anon, authenticated, service_role;'
    for kind in ('tables', 'functions', 'sequences')) + '\n'
API_ROLES = 'PUBLIC, anon, authenticated, service_role'

# The bookkeeping row the baseline writes on install and the adopt script writes on an existing server.
MARKER = """create table quorum_private.schema_baseline (
 part text primary key check (part in ('core','knowledge')),
 covers_through text not null,
 installed_by text not null check (installed_by in ('baseline','adopt')),
 recorded_at timestamp with time zone not null default now()
);
alter table quorum_private.schema_baseline enable row level security;
revoke all on quorum_private.schema_baseline from public,anon,authenticated,service_role;
"""

# One row per Quorum-owned object: (kind, identity, definition, prosrc sha256 for functions). Everything a
# client can observe is in the definition: columns in order, constraints, indexes, triggers, policies, row
# security, grants other than the owner's own, and for functions the signature, defaults, result, security,
# settings and the SHA-256 of prosrc. Run with an empty search_path so every deparsed name is qualified.
# Scope: schema quorum_private, the public functions whose body names quorum_private, pgcrypto, vector only where
# Quorum knowledge is deployed (another application may own it) and policies named quorum_* on other tables.
# Objects outside that scope are not Quorum's.
# Grants other than the owner's own; a NULL ACL is read as the built-in default it stands for.
ACL = ("coalesce((select pg_catalog.string_agg(g,',' order by g) from (select case when x.grantee=0 then 'PUBLIC' "
       "else pg_catalog.pg_get_userbyid(x.grantee) end||'='||x.privilege_type as g from pg_catalog.aclexplode("
       "coalesce({acl},pg_catalog.acldefault({kind},{owner}))) x where x.grantee<>{owner}) y),'')")
POLICY = ("p.polname::text||' '||p.polcmd::text||' '||p.polpermissive::text||' to '||(select pg_catalog.string_agg(g,',' "
          "order by g) from (select case when r=0 then 'PUBLIC' else pg_catalog.pg_get_userbyid(r) end as g "
          "from pg_catalog.unnest(p.polroles) r) y)"
          "||coalesce(' using '||pg_catalog.pg_get_expr(p.polqual,p.polrelid),'')"
          "||coalesce(' check '||pg_catalog.pg_get_expr(p.polwithcheck,p.polrelid),'')")
MANIFEST = f"""
select 'schema'::text as kind, n.nspname::text as identity,
  {ACL.format(acl='n.nspacl', kind="'n'::\"char\"", owner='n.nspowner')} as definition, null::text as prosrc
 from pg_catalog.pg_namespace n where n.nspname='quorum_private'
union all
select 'extension', e.extname::text, n.nspname::text, null
 from pg_catalog.pg_extension e join pg_catalog.pg_namespace n on n.oid=e.extnamespace
 where e.extname='pgcrypto'
  or (e.extname='vector' and pg_catalog.to_regclass('quorum_private.knowledge_spaces') is not null)
union all
select 'relation', n.nspname||'.'||c.relname, pg_catalog.concat_ws(E'\\n',
  c.relkind::text||' rls='||c.relrowsecurity::text||' force='||c.relforcerowsecurity::text
   ||' acl='||{ACL.format(acl='c.relacl', kind="case c.relkind when 'S' then 's'::\"char\" else 'r'::\"char\" end", owner='c.relowner')},
  (select pg_catalog.string_agg(a.attname||' '||pg_catalog.format_type(a.atttypid,a.atttypmod)
     ||case when a.attnotnull then ' not null' else '' end
     ||coalesce(' default '||pg_catalog.pg_get_expr(d.adbin,d.adrelid),'')
     ||case when a.attidentity<>'' then ' identity '||a.attidentity::text else '' end
     ||case when a.attgenerated<>'' then ' generated '||a.attgenerated::text else '' end
     ||coalesce(' acl='||nullif({ACL.format(acl='a.attacl', kind="'c'::\"char\"", owner='c.relowner')},''),''),
     ', ' order by a.attnum)
    from pg_catalog.pg_attribute a left join pg_catalog.pg_attrdef d on d.adrelid=a.attrelid and d.adnum=a.attnum
    where a.attrelid=c.oid and a.attnum>0 and not a.attisdropped),
  (select pg_catalog.string_agg(o.conname||' '||pg_catalog.pg_get_constraintdef(o.oid),E'\\n' order by o.conname)
    from pg_catalog.pg_constraint o where o.conrelid=c.oid),
  (select pg_catalog.string_agg(pg_catalog.pg_get_indexdef(i.indexrelid),E'\\n'
     order by pg_catalog.pg_get_indexdef(i.indexrelid))
    from pg_catalog.pg_index i where i.indrelid=c.oid),
  (select pg_catalog.string_agg(pg_catalog.pg_get_triggerdef(t.oid),E'\\n' order by t.tgname)
    from pg_catalog.pg_trigger t where t.tgrelid=c.oid and not t.tgisinternal),
  (select pg_catalog.string_agg({POLICY},E'\\n' order by p.polname) from pg_catalog.pg_policy p where p.polrelid=c.oid),
  case when c.relkind in ('v','m') then pg_catalog.pg_get_viewdef(c.oid) end,
  (select pg_catalog.concat_ws(' ',pg_catalog.format_type(s.seqtypid,null),s.seqstart,s.seqincrement,s.seqmin,
     s.seqmax,s.seqcache,s.seqcycle) from pg_catalog.pg_sequence s where s.seqrelid=c.oid)), null
 from pg_catalog.pg_class c join pg_catalog.pg_namespace n on n.oid=c.relnamespace
 where n.nspname='quorum_private' and c.relkind in ('r','p','v','m','S','f')
union all
select 'policy', n.nspname||'.'||c.relname||'.'||p.polname, {POLICY}, null
 from pg_catalog.pg_policy p join pg_catalog.pg_class c on c.oid=p.polrelid
 join pg_catalog.pg_namespace n on n.oid=c.relnamespace
 where n.nspname<>'quorum_private' and p.polname like 'quorum\\_%'
union all
select 'type', n.nspname||'.'||t.typname, t.typtype::text||' '||coalesce((select pg_catalog.string_agg(e.enumlabel,','
   order by e.enumsortorder) from pg_catalog.pg_enum e where e.enumtypid=t.oid),'')
   ||coalesce(' '||pg_catalog.format_type(t.typbasetype,t.typtypmod),''), null
 from pg_catalog.pg_type t join pg_catalog.pg_namespace n on n.oid=t.typnamespace
 where n.nspname='quorum_private' and t.typtype in ('e','d','r','m')
union all
select 'function', n.nspname||'.'||p.proname||'('||pg_catalog.oidvectortypes(p.proargtypes)||')',
  pg_catalog.concat_ws(' | ',p.prokind::text,l.lanname::text,'definer='||p.prosecdef::text,p.provolatile::text,
   'strict='||p.proisstrict::text,p.proparallel::text,pg_catalog.pg_get_function_arguments(p.oid),
   pg_catalog.pg_get_function_result(p.oid),'config='||coalesce(pg_catalog.array_to_string(p.proconfig,','),''),
   'acl='||{ACL.format(acl='p.proacl', kind="'f'::\"char\"", owner='p.proowner')}),
  pg_catalog.encode(pg_catalog.sha256(pg_catalog.convert_to(p.prosrc,'UTF8')),'hex')
 from pg_catalog.pg_proc p join pg_catalog.pg_namespace n on n.oid=p.pronamespace
 join pg_catalog.pg_language l on l.oid=p.prolang
 where (n.nspname='quorum_private' or (n.nspname='public' and p.prosrc like '%quorum\\_private%'))
  and not exists(select 1 from pg_catalog.pg_depend x where x.classid='pg_catalog.pg_proc'::pg_catalog.regclass
                 and x.objid=p.oid and x.deptype='e')
"""
ROWS = ("select pg_catalog.set_config('search_path','',true);\n"
        "select pg_catalog.json_agg(pg_catalog.json_build_array(kind,identity,definition,prosrc) order by kind,identity)"
        f" from ({MANIFEST}) m where not (kind='relation' and identity='quorum_private.schema_baseline');\n")
# pg_dump TOC entry headers: one entry per catalog object.
ENTRY = re.compile(r'^--\n-- Name: (?P<name>.*?); Type: (?P<type>.*?); Schema: (?P<schema>.*?); Owner: .*?\n--\n',
                   re.M)


def history(through=COVERS, knowledge=True):
    """The history files through one migration, in the order a fresh server applied them."""
    return [BOOTSTRAP] + [path for path in sorted(MIGRATIONS.glob('*.sql')) if path.name[:14] <= through
                          and (knowledge or not path.name.endswith(KNOWLEDGE))]


def later(knowledge=True):
    """The history files after the baseline: a fresh server applies these on top of it."""
    return [path for path in sorted(MIGRATIONS.glob('*.sql')) if path.name[:14] > COVERS
            and (knowledge or not path.name.endswith(KNOWLEDGE))]


def sources_sha256(through=COVERS):
    """One digest of every history file the baseline covers: names and bytes, in order."""
    lines = ''.join(f'{path.name} {hashlib.sha256(path.read_bytes()).hexdigest()}\n' for path in history(through))
    return hashlib.sha256(lines.encode()).hexdigest()


def entries(dump):
    """pg_dump --schema-only output as {(type, schema, name): statement text}, in dump order."""
    found = list(ENTRY.finditer(dump))
    result = {}
    for index, match in enumerate(found):
        end = found[index + 1].start() if index + 1 < len(found) else len(dump)
        body = dump[match.end():end]
        body = body.split('\n--\n-- PostgreSQL database dump complete', 1)[0]
        # pg_dump states its defaults once, before the first table: session settings, not objects.
        body = re.sub(r"^SET default_(tablespace|table_access_method) = [^;\n]*;$", '', body, flags=re.M)
        key = (match['type'], match['schema'], match['name'])
        if key in result:
            raise RuntimeError(f'duplicate dump entry {key}')
        result[key] = body.strip('\n')
    return result


def added(before, after, label):
    """The entries `after` adds to `before`; an entry both have must be unchanged."""
    changed = [key for key in before if key in after and before[key] != after[key]]
    if changed:
        raise RuntimeError(f'{label} changes existing objects: {changed[:5]}')
    gone = [key for key in before if key not in after]
    if gone:
        raise RuntimeError(f'{label} removes existing objects: {gone[:5]}')
    return {key: body for key, body in after.items() if key not in before}


# pg_dump writes `x BETWEEN a AND b` as `(x >= a) AND (x <= b)` inside an outer AND; parsed back, those ANDs flatten
# into one, an equal but different expression tree. These keep the reviewed source form, so the baseline stores
# the tree the history stored. generate() fails on any other expression that does not survive the round trip.
SOURCE_TEXT = {
    'CONSTRAINT knowledge_spaces_check CHECK ((((vector_model IS NULL) AND (vector_dimensions IS NULL)) OR '
    '(((length(vector_model) >= 1) AND (length(vector_model) <= 200)) AND ((vector_dimensions >= 1) AND '
    '(vector_dimensions <= 2000)))))':
    'CONSTRAINT knowledge_spaces_check CHECK (((vector_model IS NULL) AND (vector_dimensions IS NULL)) OR '
    '((length(vector_model) BETWEEN 1 AND 200) AND (vector_dimensions BETWEEN 1 AND 2000)))',
}


def statement(key, body):
    """One dump entry as baseline text. Environment-owned entries are dropped; ACLs revoke from every API role."""
    kind, schema, name = key
    if kind in ('DEFAULT ACL',) or (kind == 'COMMENT' and name.startswith('EXTENSION ')):
        return None
    if kind == 'SCHEMA' and name == 'extensions':
        return 'CREATE SCHEMA IF NOT EXISTS extensions;'
    if kind == 'ACL':
        lines = body.splitlines()
        revoked = [line for line in lines if line.startswith('REVOKE ALL ON ') and line.endswith(' FROM PUBLIC;')]
        if len(revoked) != 1:
            raise RuntimeError(f'unsupported ACL entry {key}: {body}')
        target = revoked[0][len('REVOKE ALL ON '):-len(' FROM PUBLIC;')]
        rest = [line for line in lines if line != revoked[0]]
        if any(not line.startswith('GRANT ') for line in rest):
            raise RuntimeError(f'unsupported ACL entry {key}: {body}')
        return '\n'.join([f'REVOKE ALL ON {target} FROM {API_ROLES};', *rest])
    for deparsed, source in SOURCE_TEXT.items():
        body = body.replace(deparsed, source)
    return body


def header(part, apply):
    return (f'-- GENERATED. Do not edit. Quorum server baseline, {part}.\n'
            f'-- Covers references/server-bootstrap.sql and supabase/migrations through {COVERS}.\n'
            f'-- Sources sha256 {sources_sha256()} (tests/quorum_baseline_support.py --check).\n'
            f'-- Regenerate: QUORUM_BASELINE_DOCKER=1 uv run --frozen python tests/quorum_baseline_support.py --write\n'
            f'-- {apply}\n'
            '-- See references/server.md#fresh-project and #baseline-maintenance-window.\n')


PRELUDE = ("SET LOCAL check_function_bodies = off;\n"
           "SELECT pg_catalog.set_config('search_path', '', true);\n")


def render_core(parts):
    body = [statement(key, text) for key, text in parts.items()]
    return (header('core', 'Fresh Supabase project only, in one transaction, before any migration newer than '
                   f'{COVERS}.')
            + "DO $baseline$ BEGIN\n IF pg_catalog.to_regnamespace('quorum_private') IS NOT NULL THEN\n"
            "  RAISE EXCEPTION 'quorum_schema_present' USING HINT='An existing server adopts the baseline instead: "
            f"supabase/baseline/{ADOPT_FILE.name}';\n END IF;\nEND $baseline$;\n"
            + PRELUDE + '\n' + '\n\n'.join(text for text in body if text) + '\n\n'
            + MARKER + f"insert into quorum_private.schema_baseline(part,covers_through,installed_by) "
            f"values('core','{COVERS}','baseline');\n")


def render_knowledge(parts):
    body = [statement(key, text) for key, text in parts.items()]
    return (header('optional project knowledge (pgvector)',
                   f'Optional. Apply after {CORE_FILE.name}, in one transaction, before any newer migration.')
            + "DO $baseline$ BEGIN\n IF pg_catalog.to_regclass('quorum_private.schema_baseline') IS NULL THEN\n"
            "  RAISE EXCEPTION 'quorum_core_baseline_required';\n END IF;\n"
            " IF NOT EXISTS(SELECT 1 FROM quorum_private.schema_baseline WHERE part='core' "
            f"AND covers_through='{COVERS}') THEN\n"
            "  RAISE EXCEPTION 'quorum_core_baseline_required';\n END IF;\n"
            " IF EXISTS(SELECT 1 FROM quorum_private.schema_baseline WHERE part='knowledge') THEN\n"
            "  RAISE EXCEPTION 'quorum_knowledge_baseline_present';\n END IF;\nEND $baseline$;\n"
            + PRELUDE + '\n' + '\n\n'.join(text for text in body if text) + '\n\n'
            + "insert into quorum_private.schema_baseline(part,covers_through,installed_by) "
            f"values('knowledge','{COVERS}','baseline');\n")


def digest(text):
    return hashlib.sha256(text.encode()).hexdigest()


def literal(text):
    return 'NULL' if text is None else "'" + text.replace("'", "''") + "'"


def render_adopt(core_rows, knowledge_rows):
    values = ',\n'.join(f"({literal(part)},{literal(kind)},{literal(identity)},{literal(digest(definition or ''))},"
                        f"{literal(prosrc)})"
                        for part, rows in (('core', core_rows), ('knowledge', knowledge_rows))
                        for kind, identity, definition, prosrc in rows)
    return (header('adoption by an existing server',
                   'Existing server only, in its own maintenance window, in one transaction. Changes no Quorum '
                   'object; only records the baseline after the catalog matches.')
            + "SELECT pg_catalog.set_config('search_path', '', true);\n"
            + "DO $adopt$\nDECLARE\n knowledge boolean := pg_catalog.to_regclass('quorum_private.knowledge_spaces')"
            " IS NOT NULL;\n problems text[];\nBEGIN\n"
            " IF pg_catalog.to_regclass('quorum_private.schema_baseline') IS NOT NULL THEN\n"
            "  RAISE EXCEPTION 'quorum_baseline_present';\n END IF;\n"
            " IF pg_catalog.current_setting('search_path') NOT IN ('','\"\"') THEN\n"
            "  RAISE EXCEPTION 'quorum_baseline_search_path';\n END IF;\n"
            " WITH expected(part,kind,identity,definition,prosrc) AS (VALUES\n" + values + "\n),\n"
            f" actual AS ({MANIFEST.strip()}\n ),\n"
            " wanted AS (SELECT * FROM expected WHERE part='core' OR knowledge)\n"
            " SELECT pg_catalog.array_agg(problem ORDER BY problem) INTO problems FROM (\n"
            "  SELECT CASE WHEN a.identity IS NULL THEN 'missing '\n"
            "   WHEN w.identity IS NULL THEN 'unexpected '\n"
            "   WHEN w.prosrc IS DISTINCT FROM a.prosrc THEN 'prosrc '\n"
            "   ELSE 'definition ' END||coalesce(w.kind,a.kind)||' '||coalesce(w.identity,a.identity) AS problem\n"
            "  FROM wanted w FULL JOIN (SELECT kind,identity,"
            "pg_catalog.encode(pg_catalog.sha256(pg_catalog.convert_to(coalesce(definition,''),'UTF8')),'hex') "
            "AS definition,prosrc FROM actual) a\n"
            "   ON a.kind=w.kind AND a.identity=w.identity\n"
            "  WHERE a.identity IS NULL OR w.identity IS NULL OR a.definition<>w.definition"
            " OR a.prosrc IS DISTINCT FROM w.prosrc\n ) p;\n"
            " IF problems IS NOT NULL THEN\n"
            "  RAISE EXCEPTION 'quorum_baseline_mismatch: % differences', pg_catalog.cardinality(problems)\n"
            "   USING DETAIL=pg_catalog.array_to_string(problems, E'\\n');\n END IF;\nEND $adopt$;\n\n"
            + MARKER + "insert into quorum_private.schema_baseline(part,covers_through,installed_by)\n"
            f" select 'core','{COVERS}','adopt'\n union all select 'knowledge','{COVERS}','adopt'\n"
            "  where pg_catalog.to_regclass('quorum_private.knowledge_spaces') is not null;\n")


def rows(cluster, db):
    out = cluster.sql(db, 'begin;' + ROWS + 'rollback;').strip().splitlines()
    return [tuple(row) for row in json.loads(out[-1])]


def dump(cluster, db):
    import test_quorum_close_race as pg
    return pg.docker('exec', cluster.name, 'pg_dump', '-U', 'postgres', '-d', db, '--schema-only', '--no-owner').stdout


def build(cluster, db, files, defaults=True):
    """A fresh database: the fixture, Supabase's default privileges unless `defaults` is false, then `files`."""
    fixture = FIXTURE.read_text()
    if cluster.sql('postgres', "select count(*) from pg_roles where rolname='anon'").strip() == '1':
        # Roles are cluster-wide: only the first database creates them.
        fixture = '\n'.join(line for line in fixture.splitlines() if not line.startswith('create role ')) + '\n'
    cluster.sql('postgres', f'create database {db}')
    cluster.sql(db, fixture + (SUPABASE_DEFAULTS if defaults else '')
                + ''.join(path if isinstance(path, str) else path.read_text() for path in files), single=True)


def cluster():
    import test_quorum_close_race as pg
    patcher = patch.object(pg, 'IMAGE', IMAGE)
    patcher.start()
    try:
        return pg.Cluster().__enter__(), patcher
    except BaseException:
        patcher.stop()
        raise


def generate(server):
    """The three baseline files, from the history applied to fresh databases on `server`."""
    build(server, 'gen_fixture', [])
    build(server, 'gen_core', history(knowledge=False))
    build(server, 'gen_full', history())
    fixture, core, full = (entries(dump(server, db)) for db in ('gen_fixture', 'gen_core', 'gen_full'))
    core_part = added(fixture, core, 'the core history')
    knowledge_part = added(core, full, 'the knowledge history')
    core_rows, full_rows = rows(server, 'gen_core'), rows(server, 'gen_full')
    if not set(core_rows) <= set(full_rows):
        raise RuntimeError('the knowledge history changes core objects')
    knowledge_rows = [row for row in full_rows if row not in set(core_rows)]
    public = server.sql('gen_core', "select count(*) from pg_proc p join pg_namespace n on n.oid=p.pronamespace "
                                    "where n.nspname='public' and p.prosrc not like '%quorum\\_private%'").strip()
    if public != '0':
        raise RuntimeError('a public function outside the manifest scope')
    files = {CORE_FILE: render_core(core_part), KNOWLEDGE_FILE: render_knowledge(knowledge_part),
             ADOPT_FILE: render_adopt(core_rows, knowledge_rows)}
    used = [deparsed for deparsed in SOURCE_TEXT if any(deparsed in text for text in (core, full) for text in text.values())]
    if used != list(SOURCE_TEXT):
        raise RuntimeError(f'unused source text overrides: {set(SOURCE_TEXT) - set(used)}')
    # The round trip: the rendered files must install exactly what the history installed.
    build(server, 'gen_check', [files[CORE_FILE], files[KNOWLEDGE_FILE]])
    check = entries(dump(server, 'gen_check'))
    drift = sorted(key for key in set(full) | set(check) if full.get(key) != check.get(key)
                   and key[2] not in ('schema_baseline', 'schema_baseline schema_baseline_pkey'))
    if drift:
        raise RuntimeError(f'the baseline does not reproduce the history: {drift[:10]}')
    return files


def header_sources(path):
    match = re.search(r'^-- Sources sha256 ([0-9a-f]{64}) ', path.read_text(encoding='utf-8'), re.M)
    return match and match[1]


def main(argv):
    if argv[1:] == ['--check']:
        stale = [path.name for path in (CORE_FILE, KNOWLEDGE_FILE, ADOPT_FILE)
                 if not path.is_file() or header_sources(path) != sources_sha256()]
        print('stale: ' + ', '.join(stale) if stale else 'baseline sources match')
        return 1 if stale else 0
    if argv[1:] != ['--write'] or os.environ.get('QUORUM_BASELINE_DOCKER') != '1':
        print('usage: QUORUM_BASELINE_DOCKER=1 quorum_baseline_support.py --write | --check', file=sys.stderr)
        return 2
    sys.path.insert(0, str(ROOT / 'tests'))
    server, patcher = cluster()
    try:
        files = generate(server)
    finally:
        server.__exit__()
        patcher.stop()
    BASELINE.mkdir(exist_ok=True)
    for path, text in files.items():
        path.write_text(text, encoding='utf-8')
        print(f'{path.relative_to(ROOT)}: {len(text.splitlines())} lines')
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv))
