// Disposable PostgreSQL; never reads config/credentials or contacts a service.
// Requires Node >=20 and npm ci in this directory (outbound npm access).
import { PGlite } from '@electric-sql/pglite';
import { pgcrypto } from '@electric-sql/pglite/contrib/pgcrypto';
import { readFileSync, readdirSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, resolve } from 'node:path';
const root = resolve(dirname(fileURLToPath(import.meta.url)), '../..');
const db = new PGlite({extensions: {pgcrypto}});
try {
  await db.exec(readFileSync(root + '/tests/quorum_backend_fixture.sql', 'utf8'));
  await db.exec('begin');
  await db.exec(readFileSync(root + '/skills/quorum/references/server-bootstrap.sql', 'utf8'));
  // Populate the old dispatcher cache before its replacement is installed.
  await db.exec(`
    insert into auth.users(id) values('99999999-9999-4999-8999-999999999999');
    insert into auth.identities(user_id,provider,provider_id) values('99999999-9999-4999-8999-999999999999','github','9000000000099');
    select set_config('request.jwt.claim.sub','99999999-9999-4999-8999-999999999999',true);
    select public.quorum_create('before-gate','before-gate');
  `);
  const migrations = root + '/skills/quorum/supabase/migrations/';
  for (const name of readdirSync(migrations).sort()) {
    if (name.endsWith('_invite_id_and_creator_gate.sql') || name.endsWith('_native_realtime_wakeups.sql') || name.endsWith('_native_grok_session.sql') || name.endsWith('_native_session_coordination.sql')) {
      await db.exec(readFileSync(migrations + name, 'utf8'));
    }
  }
  await db.exec(`do $$ begin
    begin perform public.quorum_create('before-gate','before-gate');
      raise exception 'old_cached_outsider_creation_replayed';
    exception when insufficient_privilege then null; end;
  end $$;`);
  await db.exec(readFileSync(root + '/tests/quorum_invite_id_backend.sql', 'utf8'));
  await db.exec(readFileSync(root + '/tests/quorum_session_backend.sql', 'utf8'));
  await db.exec(readFileSync(root + '/tests/quorum_realtime_backend.sql', 'utf8'));
  await db.exec(readFileSync(root + '/tests/quorum_coordination_backend.sql', 'utf8'));
  await db.exec('rollback');
  console.log('Invitation, session and Realtime regression: PASS (disposable PostgreSQL)');
} catch (error) {
  console.error(error.message, error.where || '');
  process.exitCode = 1;
} finally {
  await db.close();
}
