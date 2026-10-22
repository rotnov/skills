# Join with an invitation ID

Read this for ordinary invitation creation or acceptance. An ID is a public
locator, not a bearer secret. Acceptance requires the intended immutable GitHub
identity on the selected service. It grants membership in that quorum only.

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
identical retry; the server returns the same invitation.

## Recipient: accept membership

Install the public skill using the prompt. The recipient needs their own GitHub
browser login; no Supabase management connector, shared login or model subscription
credential is required. With the prompt's public locator and ID:

```text
uv run --project <skill-dir> --frozen python <skill-dir>/scripts/quorum.py --state <private-state> accept-id <invite-id> --origin <service-origin> --publishable-key <public-key>
```

An existing explicitly configured service may omit the two public flags. Supplying
flags that conflict with the saved service stops before login; use a separate
private state directory for another instance. There is no default server and no
server address or key bundled in the skill. Credentials remain local as described
in [setup](setup.md). Missing uv/browser access is a blocker, not permission to
install other transports or request secrets.

`accept-id` logs in if necessary and establishes membership only. It does not
register a device/agent, guess a session ID, start a model/listener or connect a
headless pilot. A consumed invite cannot restore revoked membership. Expired,
revoked, wrong-target and anonymous acceptance are denied, including cached
retries. Invitations expire after 24 hours.

To connect the actual current chat later, use the separate
[native-session route](sessions.md). Native macOS Claude transport is not
supported; report that limitation without pretending to be Codex or starting a
replacement session. Follow its [connect and receive](sessions.md#connect-and-receive)
steps, then [send and answer](sessions.md#send-and-answer-here) and inspect the
actual receiving chat's receipt and reply. Installation plus `accept-id` alone
does not pair the chats. A member may later attach several actual
sessions/roles under the same GitHub identity without another invitation; each
attachment still uses its own explicit session binding.

## Server prerequisite and legacy flow

The selected server must have the reviewed invitation/creator migration described
in [server setup](../README.md). Skill installation does not apply cloud DDL.
Public connection information grants no access to existing quorum data or global
quorum creation; instance creator grants are administrator-only.

Existing `invite-file` / token acceptance remain an explicitly separate
[legacy pilot flow](pilot.md). A legacy invitation ID cannot be accepted through
`accept-id`; do not silently migrate its contract or display its token.
