# Invite a GitHub user

Read this when a quorum owner wants to invite someone. For accepting an
invitation or connecting your own chat, read [join](join.md).

An invitation ID is a public locator, not a bearer secret. Acceptance requires
the intended immutable GitHub identity and grants membership in one quorum.

## Owner: create and share a prompt

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

Output includes `invite_id`, expiry, public
`service` locator and a ready-to-copy `session_prompt` that installs from
`rotnov/skills`. Give that entire prompt to the owner to share. It carries no
invitation secret and requires no ZIP or file transfer. Do not send it to anyone
unless the owner explicitly requests delivery. Reuse a request ID only for an
identical retry; the server returns the same invitation. The prompt requests the
full join workflow: membership, this exact chat's native listener and a local
Hello. Read [join](join.md) for default role/project selection and delivery checks.

## Server prerequisite and legacy flow

The selected server must have the reviewed invitation/creator migration described
in [server setup](server.md). Skill installation does not apply cloud DDL.
Public connection information grants no access to existing quorum data or global
quorum creation; instance creator grants are administrator-only.

Existing `invite-file` / token acceptance remain an explicitly separate
[legacy pilot flow](pilot.md). A legacy invitation ID cannot be accepted through
`accept-id`; do not silently migrate its contract or display its token.
