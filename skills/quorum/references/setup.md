# Setup and identity

This directory is independently copy-installable for Claude Code and Codex. Load
its `quorum` skill using the client's normal skill invocation; arguments such as
`join amplifier --role backend` are a request interpreted by the shared skill,
not a guaranteed native slash-command grammar.

Use `uv run --project <skill-dir> --frozen python <skill-dir>/scripts/quorum.py`.
The skill carries its own pyproject and lockfile; uv selects Python 3.12 without
using the current repository environment. Direct Python 3.12 invocation remains
possible. The helper uses only the standard library and never installs adapters.

## Owner setup

After separate cloud authorization, configure the hosted Supabase project and
GitHub OAuth provider. Only a publishable project key belongs in client config:

```text
python3.12 <skill-dir>/scripts/quorum.py configure --origin https://PROJECT.supabase.co --publishable-key PUBLIC_KEY
python3.12 <skill-dir>/scripts/quorum.py login
```

Login opens Anthropic/OpenAI neither: it opens GitHub through Supabase Auth. The
local callback binds only `127.0.0.1:8768`, uses PKCE and a random callback nonce,
and times out after two minutes. Keep the exact callback URL allowlisted.

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

Read [invitations](invitations.md) for owner-authorized quorum creation,
GitHub-bound invitation files and the OAuth application boundary. The raw owner
`invite` RPC command is for controlled integrations only: its plaintext response
must never be returned to an agent tool output. Use `invite-file` for skill-driven
onboarding. Model-facing MCP cannot create quorums or invitations.

For the explicit Claude messaging test, [pilot](pilot.md) describes recipient
connection and its additional pinned dependencies.

This release does not provision Supabase, create GitHub applications, send an
invitation to a person or install a background service. Live execution is limited
to the explicit Claude pilot and its documented acceptance scope.
