# Setup and identity

This directory is independently copy-installable for Claude Code and Codex. Load
its `quorum` skill using the client's normal skill invocation; arguments such as
`join amplifier --role backend` are a request interpreted by the shared skill,
not a guaranteed native slash-command grammar.

Use `uv run --project <skill-dir> --frozen python <skill-dir>/scripts/quorum.py`.
The skill carries its own pyproject and lockfile; uv selects Python 3.12 without
using the current repository environment. Direct Python 3.12 invocation remains
possible. The helper uses only the standard library and never installs adapters.

## Paths and command examples

The project-local copy-install above places Codex's skill at
`<project>/.agents/skills/quorum` and Claude Code's at
`<project>/.claude/skills/quorum`. Ask the client to load `quorum` and use the
actual loaded skill directory; for a global/custom installation, do not assume
these project paths. `<skill-dir>` in commands is that absolute directory,
containing `SKILL.md`, `pyproject.toml` and `scripts/quorum.py`.

`<private-state>` is an absolute writable directory you choose outside the
repository, such as `~/.local/share/quorum/my-instance` on macOS/Linux or
`$env:LOCALAPPDATA\Quorum\my-instance` on Windows. Expand it to an absolute
path and pass `--state` before the command. Reuse it for this identity/service;
use another directory for another service. It holds the config, ledger and
private login credentials, not another copy of the skill.

`<stable-id>` is a chosen request identifier, for example `create-my-team-1` or
`invite-my-team-teammate-1`. Keep it for an identical retry after an uncertain
response. Choose a new ID for a different intended operation or changed payload;
do not regenerate it merely because the first response was lost.

After configuring the service and logging in, these examples create `my-team`
and supply the required `{}` through stdin. POSIX shell, from the project root
with the Codex copy:

```sh
skill_dir="$(pwd)/.agents/skills/quorum"
private_state="$HOME/.local/share/quorum/my-instance"
printf '%s\n' '{}' | uv run --project "$skill_dir" --frozen python "$skill_dir/scripts/quorum.py" --state "$private_state" create my-team --json-stdin --request-id create-my-team-1
```

PowerShell, from the project root with the Claude Code copy:

```powershell
$skillDir = (Resolve-Path '.claude/skills/quorum').Path
$privateState = Join-Path $env:LOCALAPPDATA 'Quorum\my-instance'
'{}' | uv run --project "$skillDir" --frozen python "$skillDir/scripts/quorum.py" --state "$privateState" create my-team --json-stdin --request-id create-my-team-1
```

Use `.claude/skills/quorum` or `.agents/skills/quorum` according to the client
copy you installed. Both run the same helper and behavior.

## Owner setup

After separate cloud authorization, configure the hosted Supabase project and
GitHub OAuth provider. There are two different redirect destinations:

- **GitHub OAuth App → Supabase Auth:** set the GitHub application's
  Authorization callback URL to
  `https://<project-ref>.supabase.co/auth/v1/callback`, copied from that project's
  GitHub provider settings. Store its OAuth client ID/secret in Supabase Auth.
- **Supabase Auth → this helper:** in Supabase Auth URL Configuration, allow the
  loopback redirect `http://127.0.0.1:8768/callback?quorum_state=<random-nonce>`.
  The helper generates a fresh nonce for every login. Use a narrowly scoped
  redirect pattern for that exact host, port and path with the variable nonce,
  such as `http://127.0.0.1:8768/callback?quorum_state=*`; do not substitute the
  loopback address into the GitHub OAuth App callback field.

The helper supplies that redirect via `redirect_to`, opens GitHub through
Supabase Auth, and binds only `127.0.0.1:8768` for at most two minutes. Its callback
requires exactly one returned `code` and one `quorum_state` matching the generated
nonce, then exchanges the code using PKCE. The nonce is generated locally; nobody
needs to copy or provide it. See the official [GitHub provider setup](https://supabase.com/docs/guides/auth/social-login/auth-github)
and [redirect allowlist matching](https://supabase.com/docs/guides/auth/redirect-urls).

Only a publishable project key belongs in client config:

```text
python3.12 <skill-dir>/scripts/quorum.py configure --origin https://PROJECT.supabase.co --publishable-key PUBLIC_KEY
python3.12 <skill-dir>/scripts/quorum.py login
```

Quorum sessions are stored **unencrypted** in `<state>/credentials/` for this
owner-approved pilot. The directory is mode 0700; each profile file is mode 0600.
Writes are atomic and reject symlinks, shared files and unsafe permissions. The
store has no Keychain, encryption or external-service dependency. Never display
or copy the session JSON into model context, logs, source control or another
owner's machine. Do not commit or back up a populated state volume with the repo.

For the future Linux-container worker, mount a separate writable named volume at
`/var/lib/quorum` and pass `--state /var/lib/quorum` before the CLI subcommand.
Mount the installed skill read-only. Keep mutable sessions out of `/run/secrets`:
Docker secret mounts are read-only and cannot persist rotated OAuth tokens.
The current milestone has no runnable Docker worker or container browser-login
handoff; mounting a volume does not enable either. See [pilot](pilot.md) for the
verified native Windows scope. WSL and Docker are not prerequisites for host login
or messaging.

Encryption at rest and migration to a managed secret store (for example AWS
Secrets Manager) are open security debt, not prerequisites for this local pilot.
The backend boundary remains replaceable; no cloud secret store is contacted.

GitHub model subscriptions are unrelated: each owner authenticates their own
Claude/Codex locally. A quorum invitation grants no model-spending permission.

## Create and invite

Read [invite](invite.md) for ordinary identity-bound invitation IDs and their
copyable session prompt. Read [invitations](invitations.md) for quorum creation
and the explicitly separate legacy token-file flow. The raw owner
`invite` RPC command is for controlled integrations only: its plaintext response
must never be returned to an agent tool output. Use `invite-id` for ordinary
skill-driven onboarding. Model-facing MCP cannot create quorums or invitations.

For the explicit Claude messaging test, [pilot](pilot.md) describes recipient
connection and its additional pinned dependencies.

This release does not provision Supabase, create GitHub applications, send an
invitation to a person or install a background service. Live execution is limited
to the explicit Claude pilot and its documented acceptance scope.
