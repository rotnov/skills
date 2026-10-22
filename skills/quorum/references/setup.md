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

For a first installation, use the owner's selected global default without another
global-versus-project question:

```text
npx --yes skills@1.5.20 add rotnov/skills --skill quorum -a claude-code codex --copy -g -y
```

This places the Codex skill at `~/.agents/skills/quorum` and the Claude Code
copy at `~/.claude/skills/quorum`. For an already installed skill, use immutable
[automatic refresh](update.md) rather than overwriting an active copy.
Ask the client to load `quorum` and resolve its actual loaded directory.
An explicitly requested project installation may use `.agents/skills/quorum` or
`.claude/skills/quorum` under that project. `<skill-dir>` is the absolute directory
containing `SKILL.md`, `pyproject.toml` and `scripts/quorum.py`.

`<private-state>` is an absolute writable directory you choose outside the
repository, such as `~/.local/share/quorum/my-instance` on macOS/Linux or
`$env:LOCALAPPDATA\Quorum\my-instance` on Windows. Expand it to an absolute
path and pass `--state` before the command. It holds an authoritative service
config and independent ledgers/session bindings. New state directories can reuse
the machine's selected service and login; another service needs its own state.
Credentials use the shared machine profile described below, except an
[open-invitation state](join.md#join-this-chat), which keeps its own anonymous login.

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

For provisioning a new Quorum service, read [server setup](server.md). This
section covers the client and OAuth configuration for that selected service.

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

### One machine login per service

Sign in with GitHub **via Supabase Auth**; a Supabase dashboard registration is
not needed. The GitHub account must match an invitation's immutable numeric ID.
After initial configuration/login, all of this OS user's new state directories,
sessions and quorums on that service reuse the same local login without another
browser. `login` reuses valid auth and is also the explicit way to reauthenticate
after missing auth; it must retain the established GitHub identity. Revoked
refresh tokens block rather than triggering a browser fallback; `logout`, then
explicit `login`, reauthenticates the pinned identity.

The private registry defaults to `~/.local/share/quorum/machine-profiles/` on every
host. `QUORUM_PROFILE_ROOT` selects an explicit absolute private alternative;
keep it stable for this OS user (tests use isolated temporary roots). Each service
origin has its own public key, credential path and pinned GitHub ID. With one
registered service, an unconfigured state can use `accept-id <invite-id>` alone.
With several services, configure the intended state explicitly first. A UUID
never identifies its backend. Existing state config remains authoritative;
conflicting flags/keys or a different existing login stop instead of overriding.

On first use of an existing authenticated state, the registry adopts that state's
`<state>/credentials/` **in place**. This retained legacy directory remains the
credential location for old listeners and new helpers; do not move/delete it or
copy its rotating tokens into the registry. No other state or owner's files are
scanned. New installations store credentials inside their machine profile directory.

An older state whose own `<state>/credentials/` differs from the established
machine login keeps that token family untouched. New helpers then use the machine
login without a browser only after the service's Auth server verifies both access
tokens as one user with the pinned GitHub ID. Local token or profile claims can
block this, never prove it. The helper refreshes the machine login under its
normal lock. It never refreshes the older login implicitly, because that state's
old listener may still rotate it. The proof is recorded in a private
`<state>/auth-binding.json`, so later helpers need no network check; ledgers,
sessions and grants stay in place. If the older access token has expired, stop
that state's old listener and run `login` once with that `--state`; only this
explicit command refreshes the older family for verification. The binding pins
the verified Auth user ID as well as the GitHub ID; it is published complete
(private, fsynced, never over another binding), so an interrupted write leaves no
partial file. If the machine login is later replaced by another Auth user, even
with the same GitHub ID, that state is re-verified at the Auth server and is
never routed through the new user; other states can use the new login. Other
outcomes remain `auth_profile_conflict`, and the token-free `data` gives a
`reason` (`different_principal`, `legacy_login_unverified`,
`machine_login_missing`, `machine_principal_changed` or `binding_mismatch`), an
`action` and both credential paths. Neither login is copied, replaced or deleted.

Missing/deleted credential directories, ambiguous services, identity/key conflicts
and expired/revoked authentication are explicit blockers. A selected dead profile
is never silently recreated or replaced by another state's credentials. New
helpers serialize first login and refresh at the canonical credential path,
re-read inside the lock, and persist each rotation atomically. Old running code
keeps its path and existing behavior; a code refresh alone does not upgrade it.
`logout` removes this service's shared local auth for all its new helpers, retains
the profile/identity selection, and clears capabilities in the selected ledger.

Quorum credentials are stored **unencrypted** for this owner-approved pilot.
Directories are mode 0700; each profile file is mode 0600 (owner-only ACLs on Windows).
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
copyable session prompt, and [join](join.md) for recipient acceptance.
Read [invitations](invitations.md) for quorum creation
and the explicitly separate legacy token-file flow. The raw owner
`invite` RPC command is for controlled integrations only: its plaintext response
must never be returned to an agent tool output. Use `invite-id` for ordinary
skill-driven onboarding. Model-facing MCP cannot create quorums or invitations.

For the explicit Claude messaging test, [pilot](pilot.md) describes recipient
connection and its additional pinned dependencies.

Client installation does not provision Supabase, create GitHub applications,
send an invitation to a person or install a background service. Server setup,
invitation delivery and native-session reception require their respective owner
requests. A separate model handler uses the explicit Claude pilot scope.
