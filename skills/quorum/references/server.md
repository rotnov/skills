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
4. Apply [the native Grok engine extension](../supabase/migrations/20261022094041_native_grok_session.sql).
   It adds the engine to the existing exact-session and private-notification
   checks; it grants no new creation, membership or table rights.
5. Apply [open access](../supabase/migrations/20261022150000_open_access_quorums.sql),
   [native coordination](../supabase/migrations/20261022152603_native_session_coordination.sql)
   and [open session requirements](../supabase/migrations/20261022160000_open_session_requirements.sql),
   in that order. Native coordination adds private listener heartbeats,
   same-project availability and idempotent Hello notices; it adds no client
   table access and never revokes users on timeout. A server that already
   deployed native coordination applies open access next, then open session
   requirements; that file refuses to run before open access.
   Then apply the mandatory
   [urgent message priority](../supabase/migrations/20261022170000_urgent_message_priority.sql).
   It pins the exact reviewed dispatcher, session authorization, inbox and
   requirements bodies from these files, and fails closed before any change on
   any other body, a missing step or a second application.
   In the same transaction it advances the required release from skill `0.2.0` /
   protocol `1` to skill `0.3.0` / protocol `2`. Without it, every `0.3.0`
   client stops with `skill_update_required` before any send or reply.
   Then apply the mandatory
   [open access close serialization](../supabase/migrations/20261022180000_open_access_close_serialization.sql).
   It makes closing a quorum wait for, or be waited on by, any in-flight open
   invitation issue or acceptance, so no anonymous member or unused open
   invitation survives a close. It pins the priority release's bodies and fails
   closed the same way; it changes no grant or release requirement.
   Open access changes, open invite creation and open invite acceptance require
   PostgreSQL `READ COMMITTED` and fail closed with
   `open_access_requires_read_committed` at any other isolation level.
6. Apply [member task authority](../supabase/migrations/20261030141000_member_task_authority.sql).
   It adds a read-only check for one addressed message, its current sender and
   exact receiving binding. It grants no task permission; the receiving owner
   must explicitly enable member intake locally. It changes no release requirement.
7. Explicitly grant creation to the intended instance owner's verified immutable
   numeric GitHub ID. Look up `https://api.github.com/users/<login>`, check the
   returned `login` matches, and use the positive integer `id`.
   Never use an editable profile field.

Open access keeps every existing quorum GitHub-only. To allow open quorums,
enable **Allow anonymous sign-ins** in the project's Auth settings. Leave CAPTCHA
for anonymous sign-ins off: the helper cannot solve it and reports
`anonymous_login_unavailable`. Supabase's sign-in rate limits still apply;
anonymous users get no access without an open invitation.

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

Existing servers apply only the missing reviewed migrations in the order above,
with a separate
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

## Owner maintenance extension

Only after reviewing the concrete source and obtaining administrator deployment
authorization, apply
[`20261030140000_owner_update_maintenance.sql`](../supabase/migrations/20261030140000_owner_update_maintenance.sql)
in one transaction after `20261022180000_open_access_close_serialization.sql`.
The migration pins the reviewed inbox, receipt, dispatcher and required-release
bodies and refuses drift or reapplication. It does not change the required release.
Refresh PostgREST's schema cache after changing functions, using the service's
supported administrator workflow.

It adds private target/operation/notice/phase records and three authenticated,
exact-binding RPCs: `session_update_request`, `session_update_status` and
`session_update_report`. Inbox metadata is projected from private typed notices,
never recognized from message-body claims. Receipt records consumption only;
refresh and timestamped runtime self-reports are distinct. No table is exposed,
no updater runs server-side and no receiving-owner installation or lifecycle
permission is created. Deployment and a later change of required release are
separate operations from [owner updates](update.md#explicit-owner-update).

An offline, suspended or release-stopped client cannot be forced to consume a
notice. The receiving owner's supported interaction/recovery path remains
necessary. SQL tests and source preparation do not qualify real native clients
or prove owner-chat quietness; perform that bounded acceptance separately.

Maintenance fan-out, recipient reports and typed maintenance receipts require
`READ COMMITTED`. They preserve the existing quorum-first close order, recheck
exact session authority after protocol lock waits, and hold the current owned
agent/device/membership rows through commit so revocation serializes with the
operation. Ordinary receipt behavior and existing reviewed RPC bodies remain
unchanged. Status reads remain point-in-time snapshots.

## Optional durable project knowledge

For an explicit administrator request to deploy project knowledge, review
[project knowledge](../supabase/migrations/20261030142000_project_knowledge.sql)
after the native/open-access and required release migrations above. This source
extension creates pgvector in `extensions`, private RLS spaces/grants/attachments,
immutable revisions and optional derived vectors. It changes no required release,
quorum permission or existing context record. Confirm the deployed extension
version; hosted Supabase controls available versions. No provider is selected.
Installation, joining and knowledge commands never apply DDL or create a model.

The actual extension tests use a public pgvector `0.8.7-pg17-bookworm` image pinned
to OCI digest `sha256:ac08538c6f8b9904c33c8224c5e5706dbe760aca29db1d096972b4052c22a75d`.
After explicitly fetching that public test dependency, run
`QUORUM_KNOWLEDGE_DOCKER=1 uv run --frozen python -m unittest discover -s tests -p test_quorum_knowledge_backend.py -v`.
It uses the local immutable image with `--pull never --network none`, no published
ports and a uniquely named disposable container that is removed after the run.
Auth fixtures and vectors are synthetic; no cloud configuration is read. The
PGlite legacy backend suite does not substitute for actual pgvector tests.
