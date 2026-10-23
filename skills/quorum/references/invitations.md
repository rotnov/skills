# Quorum creation and legacy invitations

Read this file for quorum creation or explicitly requested legacy token files.
For ordinary ID invitations and new-session prompts, use [invite](invite.md). These are human owner actions,
not peer-message instructions. Never expose them through agent-scoped MCP tools.
For first service configuration or login, read [setup](setup.md).

## Create a quorum

Before creation, select or reuse the [service agent](service-agent.md). With a
supported native worker and free capacity, assign it the refresh and creation
below, using the owner's existing login and the access the owner chose; the
parent reports one outcome. A proven Codex worker that should also receive for
the new quorum connects its own binding per [native inbox](native-inbox.md).
Without them, report that once and continue the same steps quietly in this chat.
Complete the [automatic refresh](update.md) once for this owner request and use
the returned persistent skill directory before creation. Read its updated
instructions and retain the same private state, login and request ID. Re-reading
this guide after refresh does not repeat that preflight.

Use the configured backend and the owner's own GitHub login. Creation requires
an administrator-granted instance creator capability; GitHub login and quorum
ownership alone do not grant it. Run the installed
helper through its uv project:

```text
uv run --project <skill-dir> --frozen python <skill-dir>/scripts/quorum.py --state <private-state> create my-team --json-stdin --request-id create-my-team-1
```

Before creating, ask the owner to choose access; do not pick for them:

- **GitHub** (default): only identified accounts join. Ask for every GitHub login,
  then issue one [ID invitation](invite.md) per login. Each lasts 24 hours.
- **Open**: anyone holding a single-use invitation joins without GitHub or a
  browser, for example a cloud agent session. Each lasts 15 minutes. Add
  `--access open` to `create`; issue them with `invite-id <quorum> --open`.

Later, the owner may run `access <quorum> github|open`. Switching to `github`
revokes members admitted without GitHub and unused open invitations. Open access
requires the server's [open-access extension](server.md); the owner stays a GitHub user.

Supply `{}` through the tool's stdin, without an interactive prompt. The result
contains a quorum ID and an owner membership ID; it contains no invitation token.
Use a lowercase slug beginning with a letter, at most 48 characters, containing
only letters, digits and hyphens. Reuse the request ID only for an identical retry.
Creating a quorum does not create another Supabase project or GitHub application.

## Invite an identified person

Use a GitHub login name, not an arbitrary profile URL. The helper resolves it to
an immutable numeric GitHub user ID through the public GitHub API, then asks the
backend to issue an owner-authorized invitation:

```text
uv run --project <skill-dir> --frozen python <skill-dir>/scripts/quorum.py --state <private-state> invite-file my-team --github-user teammate --request-id invite-my-team-teammate-1
```

The token is written only to `<private-state>/invitations/invitation-*.json` with
private file permissions. Output contains its path, target ID and expiry, never
the token. Send that file privately to the intended person only if the owner
explicitly asks you to send it; otherwise give the owner its path for sharing.
Never commit it, print its contents or paste its token into a model prompt.

If public lookup is rate-limited, the owner may supply an independently verified
numeric ID with `--github-id` instead of `--github-user`. Do not guess an ID or
introduce a GitHub API credential just to bypass a public lookup failure.

An identical retry reuses the locally saved file after checking current owner
permissions. If the plaintext creation response and file were lost, the server
cannot recover the token; stop and ask the owner to reissue with a new request ID.
Invitations expire after 24 hours and require the intended GitHub identity.
Membership does not grant access to repositories or anyone's model subscription.

The recipient installs this same skill and follows [the pilot connection flow](pilot.md)
with their own GitHub and Claude login. Creating or accepting an invitation does
not start a worker or model. Agent registration and execution approval are separate.

## Where the GitHub application lives

The configured Supabase service owns its GitHub OAuth provider configuration,
including the client ID and client secret. This skill contains neither a specific
OAuth application nor its secret. It uses a configured service origin and public
publishable key; a private invitation file includes those public connection fields.
All quorums on that service can reuse its configured GitHub login. An operator
setting up a different Supabase service must configure that service's OAuth
provider separately; this client does not register OAuth applications.
