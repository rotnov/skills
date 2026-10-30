// Disposable PostgreSQL; never reads config/credentials or contacts a service.
// Requires Node >=20 and npm ci in this directory (outbound npm access).
import { PGlite } from '@electric-sql/pglite';
import { pgcrypto } from '@electric-sql/pglite/contrib/pgcrypto';
import { readFileSync, readdirSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, resolve } from 'node:path';
const root = resolve(dirname(fileURLToPath(import.meta.url)), '../..');
const read = path => readFileSync(path, 'utf8');
const migrations = root + '/skills/quorum/supabase/migrations/';
const applied = ['_invite_id_and_creator_gate.sql', '_native_realtime_wakeups.sql', '_native_grok_session.sql',
  '_open_access_quorums.sql', '_native_session_coordination.sql', '_open_session_requirements.sql'];
const all = readdirSync(migrations).sort();
const fresh = all.filter(name => applied.some(suffix => name.endsWith(suffix)));
const priority = all.find(name => name.endsWith('_urgent_message_priority.sql'));
// Mandatory follow-on: close serializes with open invitation issue and acceptance.
const serialization = all.find(name => name.endsWith('_open_access_close_serialization.sql'));
const memberAuthority = '20261030141000_member_task_authority.sql';
const maintenance = all.find(name => name.endsWith('_owner_update_maintenance.sql'));
// Installations that deployed native coordination before open access apply the
// older-dated open access file late; the final session requirements must match.
const late = name => name.endsWith('_open_access_quorums.sql');
const opened = name => name.endsWith('_open_session_requirements.sql');
const coordination = name => name.endsWith('_native_session_coordination.sql');
const upgraded = [...fresh.filter(name => !late(name) && !opened(name)), ...fresh.filter(late), ...fresh.filter(opened)];
// The deployed coordination release, then the mandatory release this skill installs.
const historical = {protocol_version: 1, skill_version: '0.2.0'};
const current = JSON.parse(read(root + '/skills/quorum/assets/release.json'));
const suite = name => read(root + '/tests/' + name);

async function database(order) {
  const db = new PGlite({extensions: {pgcrypto}});
  await db.exec(read(root + '/tests/quorum_backend_fixture.sql'));
  await db.exec('begin');
  await db.exec(read(root + '/skills/quorum/references/server-bootstrap.sql'));
  for (const name of order) await db.exec(read(migrations + name));
  return db;
}

async function release(db, required) {
  await db.query(`select set_config('quorum_test.release',$1,true)`, [JSON.stringify(required)]);
}

async function expectError(db, sql, expected, label) {
  await db.exec('savepoint probe');
  try {
    await db.exec(sql);
  } catch (error) {
    await db.exec('rollback to savepoint probe');
    if (expected instanceof RegExp ? expected.test(error.message) : error.message === expected) return;
    throw new Error(`${label}: expected ${expected}, got ${error.message}`);
  }
  throw new Error(`${label}: expected ${expected}, but it succeeded`);
}

async function unserialized(db, label) {
  const result = await db.query(`select to_regprocedure('quorum_private.call_serial_core(text,jsonb,text)') core`);
  if (result.rows[0].core !== null) throw new Error(label + ': partial serialization apply');
}

async function untouched(db, label) {
  const result = await db.query(`select to_regprocedure('quorum_private.call_priority_core(text,jsonb,text)') core,
    exists(select 1 from information_schema.columns where table_schema='quorum_private'
     and table_name='messages' and column_name='priority') col`);
  if (result.rows[0].core !== null || result.rows[0].col) throw new Error(label + ': partial priority apply');
}

async function regression(order) {
  const db = new PGlite({extensions: {pgcrypto}});
  try {
    await db.exec(read(root + '/tests/quorum_backend_fixture.sql'));
    await db.exec('begin');
    await db.exec(read(root + '/skills/quorum/references/server-bootstrap.sql'));
    // Populate the old dispatcher cache before its replacement is installed.
    await db.exec(`
      insert into auth.users(id) values('99999999-9999-4999-8999-999999999999');
      insert into auth.identities(user_id,provider,provider_id) values('99999999-9999-4999-8999-999999999999','github','9000000000099');
      select set_config('request.jwt.claim.sub','99999999-9999-4999-8999-999999999999',true);
      select public.quorum_create('before-gate','before-gate');
    `);
    for (const name of order) {
      if (opened(name) && !order.slice(0, order.indexOf(name)).some(late)) {
        throw new Error('open_session_requirements_before_open_access');
      }
      await db.exec(read(migrations + name));
    }
    await db.exec(`do $$ begin
      begin perform public.quorum_create('before-gate','before-gate');
        raise exception 'old_cached_outsider_creation_replayed';
      exception when insufficient_privilege then null; end;
    end $$;`);
    // The historical 0.2.0/1 server: every existing suite, then refusal of a 0.3.0/2 client.
    await release(db, historical);
    await db.exec(suite('quorum_invite_id_backend.sql'));
    await db.exec(suite('quorum_session_backend.sql'));
    await db.exec(suite('quorum_realtime_backend.sql'));
    await db.exec(suite('quorum_open_access_backend.sql'));
    await db.exec(suite('quorum_coordination_only_backend.sql'));
    await db.exec(suite('quorum_coordination_backend.sql'));
    await db.exec(suite('quorum_open_session_backend.sql'));
    // The mandatory 0.3.0/2 migrations upgrade this populated database.
    await db.exec(read(migrations + priority));
    await db.exec(read(migrations + serialization));
    await release(db, current);
    await db.exec(suite('quorum_priority_open_member_backend.sql'));
    await db.exec('rollback');
  } finally {
    await db.close();
  }
}

async function released(order) {
  // A server upgraded before its pilot members revoke anything: the release suites.
  const db = await database(order);
  try {
    await release(db, historical);
    await db.exec(suite('quorum_invite_id_backend.sql'));
    await db.exec(suite('quorum_session_backend.sql'));
    await db.exec(suite('quorum_realtime_backend.sql'));
    await db.exec(suite('quorum_coordination_only_backend.sql'));
    await db.exec(read(migrations + priority));
    await db.exec(read(migrations + serialization));
    await release(db, current);
    // Session and Realtime rerun on the upgraded schema.
    await db.exec(suite('quorum_session_backend.sql'));
    await db.exec(suite('quorum_realtime_backend.sql'));
    await db.exec(suite('quorum_open_access_backend.sql'));
    // Priority first: the coordination test ends by revoking a fixture membership.
    await db.exec(suite('quorum_priority_backend.sql'));
    await db.exec(suite('quorum_priority_open_member_backend.sql'));
    await db.exec(suite('quorum_coordination_backend.sql'));
    await db.exec(suite('quorum_open_session_backend.sql'));
    await db.exec('rollback');
  } finally {
    await db.close();
  }
}

async function gated(order, sql, expected, label, check) {
  const db = await database(order);
  try {
    await expectError(db, sql, expected, label);
    if (check) await check(db, label);
  } finally {
    await db.close();
  }
}

// Each body the priority migration pins; a drifted body fails closed before any change.
// Each body the close serialization pins on top of the priority release.
const serialized = ['quorum_private.call(text,jsonb,text)', 'quorum_private.call_priority_core(text,jsonb,text)',
  'quorum_private.call_invite_core(text,jsonb,text)', 'quorum_private.call_legacy_core(text,jsonb,text)',
  'public.quorum_set_access(uuid,text,text)', 'public.invite_create_open(uuid,text)',
  'public.invite_accept_id(uuid,text)', 'public.session_requirements()'];
const pinned = ['quorum_private.call(text,jsonb,text)', 'quorum_private.call_invite_core(text,jsonb,text)',
  'quorum_private.call_legacy_core(text,jsonb,text)', 'quorum_private.verified_actor(uuid)',
  'quorum_private.native_session(uuid,text)', 'quorum_private.owned_agent(uuid,uuid)',
  'quorum_private.member(uuid,uuid)', 'public.session_inbox(uuid,text)'];

async function drifted(fn, expected, order = fresh, file = priority, check = untouched) {
  const db = await database(order);
  try {
    const hash = `select encode(sha256(convert_to(prosrc,'UTF8')),'hex') h from pg_proc where oid=to_regprocedure($1)`;
    const before = (await db.query(hash, [fn])).rows[0].h;
    // Keep the function's behaviour; change only its reviewed source text.
    await db.exec(`do $d$ declare def text:=pg_get_functiondef('${fn}'::regprocedure); begin
      execute regexp_replace(def, '\\$(function|\\$)?\\$', '$\\1$ /* drift */ ', ''); end $d$;`);
    if ((await db.query(hash, [fn])).rows[0].h === before) throw new Error('drift injection failed for ' + fn);
    await expectError(db, read(migrations + file), expected, 'drift ' + fn);
    await check(db, 'drift ' + fn);
  } finally {
    await db.close();
  }
}

try {
  for (const order of [fresh, upgraded]) {
    await regression(order);
    await released(order);
  }
  // Open session requirements must refuse a server without open access.
  const withoutOpen = upgraded.filter(name => !late(name) && !opened(name));
  await gated(withoutOpen, read(migrations + fresh.find(opened)), 'open_access_required', 'requirements without open access');
  // The priority migration accepts only the reviewed 150000 -> 152603 -> 160000 chain.
  await gated(fresh.filter(name => !opened(name)), read(migrations + priority), 'reviewed_release_changed',
    'priority without open session requirements', untouched);
  await gated(withoutOpen, read(migrations + priority), /^reviewed_dispatcher_changed: /,
    'priority without open access', untouched);
  await gated([...fresh, priority], read(migrations + priority), /^reviewed_dispatcher_changed: /, 'priority reapplied');
  for (const fn of pinned) await drifted(fn, 'reviewed_dispatcher_changed: ' + fn);
  await drifted('public.session_requirements()', 'reviewed_release_changed');
  // Close serialization accepts only the reviewed priority release, once.
  await gated(fresh, read(migrations + serialization), /^reviewed_dispatcher_changed: /,
    'serialization without priority', unserialized);
  await gated([...fresh, priority, serialization], read(migrations + serialization), /^reviewed_dispatcher_changed: /,
    'serialization reapplied');
  for (const fn of serialized) {
    await drifted(fn, 'reviewed_dispatcher_changed: ' + fn, [...fresh, priority], serialization, unserialized);
  }
  const reviewedTail = [priority, serialization, maintenance, memberAuthority];
  if (JSON.stringify(all.filter(name => name >= priority)) !== JSON.stringify(reviewedTail)) {
    throw new Error('unreviewed migration after the close serialization');
  }
  // Exercise the actual additive migration after the pinned historical chain.
  // Its sender/current-membership behavior also runs in real PostgreSQL CI.
  const memberDb = await database([...fresh, ...reviewedTail]);
  try {
    const result = await memberDb.query(`select
      to_regprocedure('public.session_task_authority(uuid,text,uuid)')::text signature,
      has_function_privilege('authenticated','public.session_task_authority(uuid,text,uuid)','execute') authenticated,
      has_function_privilege('anon','public.session_task_authority(uuid,text,uuid)','execute') anon,
      has_function_privilege('service_role','public.session_task_authority(uuid,text,uuid)','execute') service`);
    const row = result.rows[0];
    if (!row.signature || !row.authenticated || row.anon || row.service) {
      throw new Error('member authority migration privilege contract');
    }
    await memberDb.exec(suite('quorum_updates_backend.sql'));
  } finally {
    await memberDb.exec('rollback');
    await memberDb.close();
  }
  if (!coordination(upgraded[3]) || !late(upgraded[4])) throw new Error('upgraded order is not coordination first');
  console.log('Invitation, session, Realtime, open-access, coordination, open-session, coordination-only refusal, '
    + 'mandatory priority release, close serialization, member authority ACLs, open-member priority and pinned-chain drift regression '
    + '(fresh and coordination-first upgraded order): PASS (disposable PostgreSQL)');
} catch (error) {
  console.error(error.message, error.where || '');
  process.exitCode = 1;
}
