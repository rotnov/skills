# Set up a Quorum server

Read this when an owner explicitly asks to prepare their own Supabase service.
Client installation, quorum creation and accepting an invitation do not provision
a server. Use the owner's selected project; do not infer a previous pilot service
or embed its configuration in the published skill.

## Prerequisites

The owner needs a Supabase project they can administer and a GitHub OAuth
application for its sign-in provider. A connected Supabase plugin can perform
authorized database setup and retrieve the project's public URL and publishable
key. Without it, the administrator can apply the reviewed SQL and provide those
public client settings manually.

Follow [owner setup](setup.md#owner-setup) to configure GitHub OAuth and the
helper's separate loopback redirect. Keep OAuth secrets, database passwords and
management credentials out of prompts, invitations and source control.

## Fresh project

Review and apply the following in one administrator-controlled transaction:

1. Load [server-bootstrap.sql](server-bootstrap.sql). It supplies the private
   tables, RLS, grants and RPCs, including native-session RPCs. Supabase Auth,
   standard Supabase roles and PostgreSQL pgcrypto are prerequisites.
   This schema-only pilot baseline contains no production rows or config.
   Never apply it over an existing Quorum schema.
2. In that same transaction, apply
   [the invitation and creator gate](../supabase/migrations/20261022064814_invite_id_and_creator_gate.sql).
   The baseline includes legacy creation behavior; do not commit or expose it
   without the gate. The extension verifies the reviewed dispatcher fingerprint.
   Review a modified server rather than bypassing that check.
3. Apply [native Realtime wakeups](../supabase/migrations/20261022084959_native_realtime_wakeups.sql).
   This adds receive-only private-channel authorization and empty inbox hints.
   Read [Realtime setup](realtime.md) for prerequisites, policy review and recovery.
4. Explicitly grant creation to the intended instance owner's verified immutable
   numeric GitHub ID. Look up `https://api.github.com/users/<login>`, check the
   returned `login` matches, and use the positive integer `id`.
   Never use an editable profile field.

For Grok sessions only, also apply [the native Grok engine extension](../supabase/migrations/20261022094041_native_grok_session.sql) after Realtime.
It adds the engine to the existing exact-session and private-notification checks;
it grants no new creation, membership or table rights.

After loading the bootstrap and required migrations within `BEGIN`, substitute the verified ID:

```sql
INSERT INTO quorum_private.instance_creators(github_id)
VALUES ('<verified-owner-github-id>');
-- COMMIT only after the reviewed bootstrap and required migrations succeeded.
```

The published allowlist starts empty. Repository authors, quorum owners and
members receive no implicit server-wide creation permission. Only a database
administrator manages this list; revocation sets the row's `revoked=true`.
The server checks current identity and permission before creation, including
cached retries.

Existing servers apply only the missing reviewed migrations, with a separate
explicit administrator creator grant when needed. Skill installation never runs cloud DDL or grants.

## Check readiness

Configure the owner's local client and complete GitHub login using
[setup](setup.md). Verify that the intended creator has the explicit grant and
unlisted accounts cannot create quorums, using transactional checks that roll
back temporary fixtures. Create the owner's real quorum only when requested.

Check the actual project's Auth configuration, RPC exposure and access controls
before inviting participants. Report the selected project, configuration result,
allowed creator and any remaining browser or access blocker. Do not claim
OAuth login or cross-chat delivery from SQL success alone.

The supplied SQL is tested in disposable PostgreSQL with synthetic Auth fixtures.
Repository developers can run `npm ci --prefix tests/quorum-backend` followed by
`npm test --prefix tests/quorum-backend`; these tests use no cloud credentials.
