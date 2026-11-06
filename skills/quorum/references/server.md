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
   A server that skipped step 4 must not apply it now, because that would undo
   the open-access gate; apply
   [native session engine convergence](../supabase/migrations/20261022165000_native_session_engine_convergence.sql)
   instead. It gives such a server the bodies this order installs. On a server
   that applied step 4, live included, it changes nothing and can be skipped.
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

## Add server behaviour

For migration authors; every new migration also gets a section in this file.

Do not copy and rename `quorum_private.call`, `public.session_heartbeat` or a
function in their chains (`call_*_core`, `session_heartbeat_before_status`,
`session_heartbeat_before_hello`). Copy and rename means moving a function aside
and pasting a near-copy of its body as the replacement, so the moved ancestor stops
running. `20261106120000` and `20261106150000` did this for the heartbeat. They stay
as applied, because later pins hash what they installed. Do not repeat them.

Ship new behaviour in one of these ways, in this order:

1. A new RPC. The template is
   [`20261106160000_session_message.sql`](../supabase/migrations/20261106160000_session_message.sql):
   - a `SOURCE ONLY` header naming its predecessor and what it does not change;
   - a guard block that refuses reapplication, refuses a missing predecessor and
     compares the `prosrc` SHA-256 of each function it relies on, plus
     `public.session_requirements()` for the release;
   - one `create function` with `security definer` and `search_path=pg_catalog`;
   - `revoke all` from `public,anon,authenticated,service_role`, then
     `grant execute` to `authenticated`.
2. A thin wrapper, when an existing RPC must change its answer. The wrapper adds
   only its own lines and calls the moved function, whose reviewed body keeps
   running unchanged. See `public.session_heartbeat` in
   [`20261105160000_peer_status_visibility.sql`](../supabase/migrations/20261105160000_peer_status_visibility.sql).
   Around `call`, this adds another dispatcher layer, so prefer a new RPC there.

If neither fits, raise it with the owner instead of pasting a body; the baseline
question is tracked in [#156](https://github.com/rotnov/skills/issues/156).

A new RPC also needs its name in `OPERATIONS` in `quorum_api.py`, any new exception
name in that file's error table, a client fallback for a server without it, and
pins in `tests/test_quorum_migration_order.py`.

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

## Open invitation lifetime

After reviewing the source and obtaining administrator deployment authorization,
apply
[`20261103190000_open_invite_ttl.sql`](../supabase/migrations/20261103190000_open_invite_ttl.sql)
in one transaction after `20261103180000_hello_roster_visibility.sql`. New open
invitations then expire 60 minutes after issue instead of 15. A row trigger sets
only that expiry; it pins the reviewed open-access core, refuses drift or
reapplication, and leaves GitHub invitations, existing rows, acceptance and
grants unchanged.

## Invitation roles and owner retire

After reviewing the source and obtaining administrator deployment authorization,
apply
[`20261105140000_role_assignment_and_owner_retire.sql`](../supabase/migrations/20261105140000_role_assignment_and_owner_retire.sql)
in one transaction after `20261103190000_open_invite_ttl.sql`. It refuses to run
without `20261030140000_owner_update_maintenance.sql`, whose dispatcher pin it
wraps. It pins the reviewed dispatcher, the invitation, acceptance and join entry
points, role resolution, membership and release bodies, and refuses drift or
reapplication before any change. It does not change the required release.

- `invite_create_id` and `invite_create_open` accept an optional role and
  project reference. Acceptance records them on the membership, and `agent_join`
  from that membership refuses any other role or project with
  `role_assignment_mismatch`. Unassigned invitations behave as before.
- `agent_join` refuses a role and project held by another identity's live agent
  (`role_held_by_other_identity`) or by the caller's own live agent on another
  device or session (`role_held_by_own_agent`). `p_supersede` retires the
  caller's own holders in the same transaction; `p_keep_agent_id` lets one named
  own agent coexist, as an adopting Claude worker's parent does. A same-session
  reconnect and an idempotent retry return the original agent. Joins require
  PostgreSQL `READ COMMITTED` (`role_exclusivity_requires_read_committed`).
  Accepting any invitation that reactivates a revoked membership first retires
  all of that membership's old agents, like `agent_leave`: they are stale and
  must not reappear next to a holder that claimed their role meanwhile.
- `agent_retire` lets the quorum owner revoke one agent of the quorum, exactly
  like `agent_leave`, keeping the membership; `quorum_agents` is the owner's
  roster of agent IDs, roles, projects, engines, owner labels, last heartbeat and
  revocation, without tokens, devices or session references.

Existing entry points gain only trailing defaulted arguments, so current clients
keep calling them unchanged. Clients that predate the adopt `p_keep_agent_id`
argument cannot start an adopting Claude worker while its parent agent is live;
they report `role_held_by_own_agent` until refreshed. Refresh PostgREST's schema
cache after applying. To roll back before any assignment or supersession is
recorded, restore the previous entry points and dispatcher from the reviewed
sources in one transaction; afterwards, keep the columns and table and only
restore the functions.

## Peer status extension

After reviewing the source and obtaining administrator deployment authorization,
apply
[`20261105160000_peer_status_visibility.sql`](../supabase/migrations/20261105160000_peer_status_visibility.sql)
in one transaction after `20261105140000_role_assignment_and_owner_retire.sql`.
It refuses to run without the Hello reporting, owner-maintenance and role
assignment extensions. It pins the Hello heartbeat and its core, the dispatcher,
the peer view, the binding check, the runtime-report writer and the release,
and refuses drift or reapplication before any change. It does not change the
required release, Hello recipients or task authority.

- `session_heartbeat` takes four optional trailing arguments: the awaiting-owner
  count (0..32, otherwise `invalid_peer_status` before anything is written), a
  reception hold deadline (kept only while in the future, clamped to 30 minutes
  from now), a client run generation UUID and that run's start (clamped to now).
  Each accepted heartbeat overwrites one private `session_status` row for its
  agent, so it always describes the latest heartbeat. Current clients keep
  calling with five arguments and get the same response; they store NULLs.
- `session_peers` adds `awaiting_owner` and `reception_hold_until` (both only
  from a status heartbeat under 60 seconds old) and `client_run_reported`. A live
  peer with a count above zero shows availability `awaiting_owner`. An overdue
  receipt behind a running hold keeps `unavailable` with reason `reception_hold`.
  A peer shows `client_restarted_unrecovered` (reason
  `run_changed_without_heartbeat`) only when its health is at least 60 seconds
  stale, its last heartbeat named a run, and its newest owner-update runtime
  report names a different run recorded after that heartbeat. Peers never see a
  run ID, a message ID or a body.

The previous heartbeat and peer view stay as private functions. Refresh
PostgREST's schema cache after applying. To roll back, in one transaction drop
the new `session_heartbeat` and `session_peers`, move
`quorum_private.session_heartbeat_before_status` and
`quorum_private.session_peers_before_status` back to `public` under their old
names, grant both to `authenticated` again and drop `session_status`. Clients
then fall back to five-argument heartbeats on their own.

## Reconnect Hello dedupe

After reviewing the source and obtaining administrator deployment authorization,
apply
[`20261106120000_hello_reconnect_dedupe.sql`](../supabase/migrations/20261106120000_hello_reconnect_dedupe.sql)
in one transaction after `20261105160000_peer_status_visibility.sql`. It refuses
to run without the peer status and Hello reporting extensions. It pins the
peer-status heartbeat, the Hello wrapper and core it replaces, the dispatcher,
the binding check, the receipt writer and the release, and refuses drift or
reapplication before any change. It does not change the required release, the
heartbeat response, table access or task authority.

A new listener connection still sends a Hello to each same-project peer, except
to a peer that already got a Hello from the same sender within the last 30
minutes of the sender's unbroken presence. The first connection, and a
reconnect after the sender's health was at least 60 seconds stale, announce to
every peer. A skipped peer still counts in the `hello` result and the roster;
the [Hello sent and skipped split](#hello-sent-and-skipped-split) reports how many.
An agent seen only before this release announces once more, then is deduplicated.
Clients need no change; current clients also receipt transport notices quietly.

The previous Hello wrapper and core stay as private functions. To roll back, in
one transaction drop the new `session_heartbeat_before_status` and
`session_heartbeat_before_hello`, rename
`session_heartbeat_hello_before_dedupe` and
`session_heartbeat_core_before_dedupe` back to those names, drop the
`session_health_streak` trigger and function, and drop `session_streaks` and
`session_hello_sent`.

## Task authority project bound

After reviewing the source and obtaining administrator deployment authorization,
apply
[`20261106130000_task_authority_project_bound.sql`](../supabase/migrations/20261106130000_task_authority_project_bound.sql)
in one transaction after `20261106120000_hello_reconnect_dedupe.sql`. It raises
the `session_task_authority` project-ref bound from 256 to the 500 octets join
admits; before it, an agent with a longer ref gets `session_binding_denied` there.
It pins the reviewed body, the binding check and the release, and refuses drift,
reapplication or a server without the Hello dedupe release before any change. It
does not change the required release, the response, table access or any grant.

The reviewed body stays private as
`quorum_private.session_task_authority_before_project_bound`. To roll back, in one
transaction drop the new `public.session_task_authority`, move that function back
to `public` under its old name and grant it to `authenticated` again.

## Exact unreceipted lookup

After reviewing the source and obtaining administrator deployment authorization,
apply
[`20261106140000_session_message_unreceipted.sql`](../supabase/migrations/20261106140000_session_message_unreceipted.sql)
in one transaction after `20261106130000_task_authority_project_bound.sql`. It pins
the reviewed inbox predicate, the binding check, the project-bound task authority and
the release, and refuses drift, reapplication or a server without that predecessor
before any change. It does not change the required release, table access or task
authority.

`session_message_unreceipted` tells the caller's own native session whether one
exact message is still unreceipted, so `session reconcile --confirmed-absent`
works behind a newer urgent head. It returns the message UUID and one boolean,
never a body or sender, and writes nothing. A client on a server without it falls
back to the inbox head and fails closed. To roll back, in one transaction drop
`public.session_message_unreceipted(uuid,text,uuid)`.

## Hello sent and skipped split

After reviewing the source and obtaining administrator deployment authorization,
apply
[`20261106150000_hello_sent_skipped.sql`](../supabase/migrations/20261106150000_hello_sent_skipped.sql)
in one transaction after `20261106140000_session_message_unreceipted.sql`. The
heartbeat `hello` object gains `skipped`, how many of `peers` the reconnect dedupe did not greet
again on this connection, so clients can report sent and skipped apart. `peers`
and the roster text keep their meaning. It pins the peer-status heartbeat, the
Hello wrapper it replaces, the core, the dispatcher, the binding check and the
release, and refuses drift, reapplication or a server without the task authority
bound before any change. It does not change the required release, Hello
recipients, table access or task authority.

Clients need no change. A client on a server without this file, or reading a
connection recorded before it, gets no split and reports `Hello reached N
peers`, because N can then include peers that were not greeted again.

The previous Hello wrapper stays private as
`quorum_private.session_heartbeat_hello_before_split`. To roll back, in one
transaction drop the new `session_heartbeat_before_status`, rename that function
back to it and drop the `skipped` column of `session_hellos`.

## Single-message read

After reviewing the source and obtaining administrator deployment authorization,
apply
[`20261106160000_session_message.sql`](../supabase/migrations/20261106160000_session_message.sql)
in one transaction after `20261106150000_hello_sent_skipped.sql`. It pins the binding
check and the release, and refuses drift, reapplication or a server without that
predecessor before any change. It does not change the required release, table access
or task authority.

`session_message` returns one message addressed to the caller's own native session,
so a Codex chat can read a queued body in a conversation whose 16-row page would pass
the 262,144-byte response limit. Another agent's message and an unknown one both
return null. It reads no receipt and writes nothing; the client calls `session_ack`
itself for a queued pointer. A client on a server without it
falls back to `conversation_read`. To roll back, in one transaction drop
`public.session_message(uuid,text,uuid)`.

## Delivery statuses

After reviewing the source and obtaining administrator deployment authorization,
apply
[`20261106170000_delivery_statuses.sql`](../supabase/migrations/20261106170000_delivery_statuses.sql)
in one transaction after `20261106160000_session_message.sql`. It pins the binding
check, `session_reply` and the release, and refuses drift, reapplication or a server
without that predecessor before any change. It does not change the required release,
any existing function body, table access or task authority. Refresh PostgREST's
schema cache after applying.

It adds the private `delivery_statuses` and `delivery_status_streams` tables, an
index on recent deliveries, one trigger after a delivery becomes `answered`, and the
RPCs `session_delivery_mark`, `session_delivery_status`, `session_delivery_changes`
and `session_delivery_stuck`. [Delivery statuses](protocol.md#delivery-statuses) has
the contract. Heartbeats and Hello are untouched. A client without this ignores the
new Realtime event. A new client on a server without this keeps delivering, and
`session mark` and `session delivery` return `delivery_status_unavailable`.

To roll back, in one transaction drop the trigger `delivery_status_answered` on
`quorum_private.deliveries`, the four public RPCs, the private functions
`delivery_status_answered`, `delivery_status_apply`, `delivery_status_view` and
`delivery_status_applies`, the tables `delivery_statuses` and
`delivery_status_streams`, and the index `deliveries_quorum_recent`.

## Hello reporting extension

After reviewing the source and obtaining administrator deployment authorization,
apply
[`20261103180000_hello_roster_visibility.sql`](../supabase/migrations/20261103180000_hello_roster_visibility.sql)
in one transaction after `20261022180000_open_access_close_serialization.sql`
and, where deployed, after `20261103075741_llm_wiki.sql`. It pins the reviewed
heartbeat and release bodies and refuses drift or reapplication. It does not
change the required release, Hello recipients or task authority.

The reviewed heartbeat moves to a private function. The new
`session_heartbeat` adds a `hello` object: Hello recipient count and up to 16
roles, the same-project peers with a fresh heartbeat, and a roster message ID.
A retry with the same connection returns the same object. A new connection also
gets one self-addressed roster notice, idempotent per connection ID. It is a
notification with `requires_handler=false`, never a task. Like a Hello, it needs
a receipt and counts toward `receipt_timeout` until receipted. Clients without this
extension report the Hello result as not reported by the server.

## Optional durable project knowledge

For an explicit administrator request to deploy project knowledge, review
[project knowledge](../supabase/migrations/20261030142000_project_knowledge.sql)
after the native/open-access and required release migrations above. This source
extension creates pgvector in `extensions`, private RLS spaces/grants/attachments,
immutable revisions and optional derived vectors. It changes no required release,
quorum permission or existing context record. Confirm the deployed extension
version; hosted Supabase controls available versions. No provider is selected.
For retained-source wiki support, review and apply
[retained-source wiki](../supabase/migrations/20261103075741_llm_wiki.sql) after
project knowledge. It adds immutable private sources, revision evidence/link
arrays, source lifecycle operations and bounded catalog. Existing read callers
retain compatible defaults; `include_history` only permits superseded current
revisions for scoped readers. Both extensions remain optional and separately
deployed; absent or rejected RPCs use the fixed unavailable diagnostic.
Installation, joining and knowledge commands never apply DDL or create a model.

The actual extension tests use a public pgvector `0.8.7-pg17-bookworm` image pinned
to OCI digest `sha256:ac08538c6f8b9904c33c8224c5e5706dbe760aca29db1d096972b4052c22a75d`.
After explicitly fetching that public test dependency, run
`QUORUM_KNOWLEDGE_DOCKER=1 uv run --frozen python -m unittest discover -s tests -p test_quorum_knowledge_backend.py -v`.
Also run
`QUORUM_KNOWLEDGE_DOCKER=1 uv run --frozen python -m unittest discover -s tests -p test_quorum_wiki_backend.py -v`
for retained-source immutability, permission/output suppression, lifecycle,
concurrent CAS and source-withdrawal races. Fixtures apply both additive migrations.
These tests use the local immutable image with `--pull never --network none`, no published
ports and a uniquely named disposable container that is removed after the run.
Auth fixtures and vectors are synthetic; no cloud configuration is read. The
PGlite legacy backend suite does not substitute for actual pgvector tests.

## Knowledge member default role

Optional, for servers that deployed project knowledge and the retained-source
wiki. After reviewing the source and obtaining administrator deployment
authorization, apply
[`20261106100000_knowledge_member_default_role.sql`](../supabase/migrations/20261106100000_knowledge_member_default_role.sql)
in one transaction after `20261103075741_llm_wiki.sql`. It refuses to run
without both (`knowledge_wiki_required`), pins the scope check, the
administration entry point, the binding check, the request-journal helpers
and every caller of the scope check, and refuses drift or reapplication
before any change. It does not change the required release, quorum
permissions or existing grants.

- `knowledge_admin` gains the owner operation `default_role` (`space_id`,
  `permission`: `reader`, `writer` or `none`). One private
  `knowledge_member_defaults` row per space holds it; `none` removes it.
- The scope check uses that default only when the caller has no explicit
  grant, and only after the reviewed checks admitted the caller: a live agent,
  device and non-revoked membership of a quorum attached to the space for the
  agent's project. Owner-only operations stay with the owner.

The previous scope check and administration body stay as private functions.
Refresh PostgREST's schema cache after applying. To roll back, in one
transaction drop the new `quorum_private.knowledge_scope` and
`public.knowledge_admin`, rename `knowledge_scope_before_default_role` back to
`knowledge_scope`, move `knowledge_admin_before_default_role` back to `public`
as `knowledge_admin`, grant it to `authenticated` again and drop
`knowledge_member_defaults`. Explicit grants are unaffected. The pgvector
PostgreSQL suite `test_quorum_knowledge_default_role_backend.py` (same image and
`QUORUM_KNOWLEDGE_DOCKER=1` as above) covers the guards, the contract and the
default-change races.
