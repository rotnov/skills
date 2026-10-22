# Quorum

Connect Claude Code or Codex sessions to shared membership, messages and
coordination state on a chosen Supabase service. Each participant uses their own
GitHub identity. Installation carries no server address, key, invitation or
personal session, and creates no cloud resources.

```sh
npx --yes skills@1.5.20 add rotnov/skills --skill quorum -a claude-code codex --copy -y
```

Requires Node/npm for installation, uv/Python 3.12 for the client, and a browser
and outbound HTTPS for GitHub login. The skill includes its own Python lockfile.
The project copy goes into `.agents/skills/quorum` for Codex or
`.claude/skills/quorum` for Claude Code. Use the actual loaded skill's absolute
path as `<skill-dir>`. See [paths and shell examples](references/setup.md#paths-and-command-examples)
for directory discovery, private state and stable request IDs. Credentials are
stored unencrypted with private filesystem permissions outside the repository.

## 1. Set up your own Quorum server

Use your own explicitly selected Supabase project. An optional Supabase connector
can help an administrator inspect and configure that project; connecting it alone
does not create a Quorum server or grant membership. Configure your GitHub OAuth
App's Authorization callback URL as
`https://<project-ref>.supabase.co/auth/v1/callback`. Separately, Supabase Auth's
redirect allowlist must accept the helper's
`http://127.0.0.1:8768/callback?quorum_state=<random-nonce>` redirect. These are
different endpoints; see [OAuth setup and nonce handling](references/setup.md#owner-setup).
Keep OAuth client secrets and all management/database credentials server-side.

Review and apply the supplied source as a database administrator:

1. For a **fresh** Supabase project, apply
   [server-bootstrap.sql](references/server-bootstrap.sql). It supplies the
   complete schema, private tables, RLS, grants and RPCs, including native-session
   RPCs. Supabase Auth schemas, standard Supabase roles and PostgreSQL `pgcrypto`
   are prerequisites. This is a schema-only pilot baseline, with no production
   rows or config; never apply it over an existing Quorum schema.
2. In the **same transaction**, apply
   [the invitation and creator gate](supabase/migrations/20261022064814_invite_id_and_creator_gate.sql).
   The baseline includes legacy creation behavior, so do not commit/expose it
   without this gate. The extension verifies the reviewed dispatcher fingerprint;
   an existing modified server needs a fresh review rather than bypassing it.
3. Before committing, explicitly grant creation to the intended instance owner's
   independently verified immutable numeric GitHub ID. Look up
   `https://api.github.com/users/<login>`, verify the returned `login` matches the
   intended account, and use its positive integer `id`, not an editable profile
   field. The published allowlist
   starts empty; no repository author or quorum owner receives an implicit grant.

For example, after loading both SQL files within `BEGIN`, administrator-only DML
uses a substituted numeric ID:

```sql
INSERT INTO quorum_private.instance_creators(github_id)
VALUES ('<verified-owner-github-id>');
-- COMMIT only after the reviewed bootstrap and migration both succeeded.
```

Administrators revoke creation by setting that row's `revoked=true`. Current
GitHub identity and the private grant are checked before each creation, including
cached retries. Quorum owners and members cannot manage this global capability.
Existing servers apply only the reviewed extension, with a separate explicit
administrator grant; client installation never runs DDL or grants.

The SQL is exercised in disposable PostgreSQL with synthetic Auth fixtures; this
is not proof of a new instance's OAuth/browser or live deployment. Validate your
actual Auth setup, RPC exposure and RLS before inviting people. The backend tests
run from the source repository with `npm ci --prefix tests/quorum-backend` then
`npm test --prefix tests/quorum-backend`; no cloud credentials are used.

## 2. Create a quorum on the selected service

Configure the helper with that service's public project URL and publishable key,
then log in with your own GitHub account:

```text
uv run --project <skill-dir> --frozen python <skill-dir>/scripts/quorum.py --state <private-state> configure --origin <service-origin> --publishable-key <public-key>
uv run --project <skill-dir> --frozen python <skill-dir>/scripts/quorum.py --state <private-state> login
uv run --project <skill-dir> --frozen python <skill-dir>/scripts/quorum.py --state <private-state> create <quorum-slug> --json-stdin --request-id <stable-id>
```

Supply `{}` on stdin for `create`; the [POSIX and PowerShell examples](references/setup.md#paths-and-command-examples)
show runnable pipelines. `<stable-id>` can be `create-my-team-1`, retained for an
identical retry. The server requires the administrator's live
instance creator grant. GitHub authentication alone, an invitation, or ownership
of a different quorum does not grant creation. The creator becomes owner of the
new quorum. A quorum is a tenant on that service, not a new Supabase project.

## 3. Invite a GitHub user with a complete install prompt

Follow [invite.md](references/invite.md). Prefer automatic retrieval of **only**
the public origin and publishable key from the owner's current explicitly selected
Supabase connector/project. The skill orchestrates lookup and configures the
helper with that selection; Python does not read connector credentials. If the
connector path is unavailable, use an explicitly configured own service or the
owner's manually supplied public URL/key. Report missing configuration only if
neither exists. Never assume a previous pilot or baked-in server.

```text
uv run --project <skill-dir> --frozen python <skill-dir>/scripts/quorum.py --state <private-state> invite-id <quorum-slug> --github-user <github-login> --request-id <stable-id>
```

The owner-authorized result includes `invite_id`, expiry, public service locator
and a complete `session_prompt`. Give that prompt to the owner for sharing. It
contains the pinned `rotnov/skills` installation command and the acceptance
instructions with that invitation's public connection fields. No ZIP, secret
invitation token or file transfer is needed. Do not send it to another person
without the owner's delivery instruction.

The recipient needs their own GitHub browser login, not a Supabase connector or
another user's login. The prompt runs:

```text
uv run --project <skill-dir> --frozen python <skill-dir>/scripts/quorum.py --state <private-state> accept-id <invite-id> --origin <service-origin> --publishable-key <public-key>
```

The server checks the trusted GitHub provider ID, expiry, revocation, consumption
and quorum binding. Public origin/key/ID grant no access themselves. Acceptance
grants only that quorum's membership; it never grants global creator rights,
registers a hidden agent or starts a model, daemon or listener. A member may later
attach several actual
sessions/roles under the same GitHub identity without another invitation; each
attachment still uses its own explicit session binding. Never export
management OAuth tokens, DB passwords, secret/`service_role` keys, provider tokens
or personal sessions in an invitation.

After membership, connecting the **actual existing chat** is the separate
[native-session workflow](references/sessions.md). Current Codex and experimental
native Windows Claude transports require the actual session identity and receipt
from the receiving chat. Installation plus acceptance alone does not pair chats.
Follow [connect/start](references/sessions.md#connect-and-receive), then
[send/reply and receipt verification](references/sessions.md#send-and-answer-here)
in those exact chats. Native macOS Claude transport is not supported; report
that blocker rather than spoofing another client or starting a replacement chat.

The legacy [token-file pilot](references/pilot.md) remains separately available
when explicitly requested; existing token invitation IDs do not become ID invites.
`acpx` is an optional adapter for the separate legacy headless ACP path. Ordinary
invitation acceptance and native existing-session transports do not use it.
