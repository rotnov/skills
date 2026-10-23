# Invite a teammate

Read this when a quorum owner wants to invite someone. For accepting an
invitation or connecting your own chat, read [join](join.md).

A GitHub invitation ID is a public locator, not a bearer secret. Acceptance requires
the intended immutable GitHub identity and grants membership in one quorum.
An [open invitation](#open-quorum-invitations) is a short-lived bearer secret instead.

## Owner: create and share a prompt

Before configuration or `invite-id`, select or reuse the native `quorum` worker
per [service agent](service-agent.md) and assign it these owner-authorized
invitation mechanics when that capability and capacity exist; it needs no new
global or custom-agent setup. Generated prompts route a recipient's proven Codex
worker through [native inbox](native-inbox.md) and keep the parent fallback. Otherwise give one concise capability/capacity
diagnostic and continue this same flow quietly in this chat. Identity and owner
permission rules below are unchanged; this grants no new right.

Prefer automatic retrieval of only the public project URL and publishable key
from the owner's **current explicitly selected Supabase connector/project**.
A legacy public `anon` key is also supported. Confirm the selected project rather
than choosing from a list or inferring it from a previous pilot. If that connector
path is unavailable, an explicitly configured own service or public URL/key
supplied by the owner is a supported manual fallback. Report missing configuration
only if neither source is available; never assume a baked-in server.

Configure the helper with those public fields using [setup](setup.md). The Python
helper consumes explicit configuration; it does not discover or authenticate a
Supabase management connector. Never export management OAuth credentials, a DB
password, `service_role`/secret keys, provider tokens or another person's session.
Then, using the owner's own GitHub login:

```text
uv run --project <skill-dir> --frozen python <skill-dir>/scripts/quorum.py --state <private-state> invite-id <quorum> --github-user <login> --request-id <stable-id>
```

The helper resolves the public GitHub login to a numeric ID and requires current
quorum owner permission. If public lookup fails, use `--github-id` only with an
independently verified numeric ID. Verify using the fixed public endpoint
`https://api.github.com/users/<login>`: check that the returned `login` matches the
intended GitHub account (case-insensitively), then use its positive integer `id`.
Do not use a display name, editable profile field or guessed ID. This is also how
the helper resolves `--github-user`; no GitHub API token is needed.

Output identifies the intended `@GitHub-login` when supplied, immutable numeric
ID, quorum and expiry. GitHub is required; the recipient signs in through GitHub
via Supabase Auth and does not register for the Supabase dashboard.

Share `session_prompt` for a first machine: it includes only the public `service`
locator and installs from `rotnov/skills` globally with the pinned CLI. An already
installed copy uses immutable automatic refresh. For a configured machine, share
`configured_machine_prompt`, which only needs `invite_id` and reuses its selected
machine profile/login, including in a new state directory. Missing/ambiguous
profiles block; never guess a backend from the UUID. Both prompts carry no
invitation secret and require no ZIP or file transfer. Do not send either to anyone
unless the owner explicitly requests delivery. Reuse a request ID only for an
identical retry; the server returns the same invitation. The prompt requests the
full join workflow: the [service agent](service-agent.md) where available,
membership, this exact chat's native listener, Hello notices and one-time
coordinator task intake for the chosen project. The recipient
authorizes this by pasting the prompt as their own instruction; issuing an invite
alone grants no task permissions. Default role is `coordinator`; select a different
registered role with `--coordinator-role <role>`. Scope and revocation are described
in [delegation](delegation.md). Read [join](join.md) for default role/project selection and delivery checks.
Both prompts authorize sanitized connection-failure reporting to the repository
issue tracker; follow [the failure-reporting procedure](join.md#connection-failures),
including duplicate checks and the no-write-access fallback.

The prompt selects the actual client before consuming the invitation. A Grok Bot
personal cloud chat first follows [cloud setup](grok-cloud.md), then adds
`--client grok-bot` and uses `cloud connect/start/status`; native clients retain
the `session` route. The Bot's private wake target and owner-authorized routine
must be ready first. Issuing an invitation does not authorize a new incoming
channel or turn a routine into its parent. Cursor Cloud is not a verified Grok
Bot transport. If a target client's transport is unavailable, leave the invite
unused instead of silently accepting membership only.

## Open quorum invitations

For a quorum created with open access (see [creation](invitations.md#create-a-quorum)),
issue one invitation per intended session:

```text
uv run --project <skill-dir> --frozen python <skill-dir>/scripts/quorum.py --state <private-state> invite-id <quorum> --open --request-id <stable-id>
```

It is single-use, expires after 15 minutes and needs no GitHub account or browser.
Until accepted or expired, anyone holding `session_prompt` can join, so give it
only to the intended recipient and issue a new one after expiry. A GitHub-access
quorum refuses open invitations. Joined members can use member operations only.

## Server prerequisite and legacy flow

The selected server must have the reviewed invitation/creator migration described
in [server setup](server.md). Skill installation does not apply cloud DDL.
Public connection information grants no access to existing quorum data or global
quorum creation; instance creator grants are administrator-only.

Existing `invite-file` / token acceptance remain an explicitly separate
[legacy pilot flow](pilot.md). A legacy invitation ID cannot be accepted through
`accept-id`; do not silently migrate its contract or display its token.
