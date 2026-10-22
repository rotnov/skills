# Existing Grok TUI on macOS

Read this only for an owner-authorized connection of the current interactive
Grok chat. This experimental route supports **Grok Build 1.0.67 on macOS**;
Linux and Windows Grok transports are not supported. Codex and Claude support
is unchanged. Grok discovers the shared `.agents/skills` directory, including
the portable copy returned by the [automatic refresh](update.md).

The intended TUI must already have been started by its owner with explicit
process-local flags, before asking that chat to join:

```text
grok --leader --leader-socket <private-dir>/leader.sock --cwd <project-dir> --session-id <new-UUID>
```

Use absolute paths without spaces for this experimental command. Supply no
initial prompt or positional subcommand; enter prompts after the TUI opens. The socket's
parent must be owned by the current OS user and private (0700); the native socket
may be 0755 but cannot be group/other writable. Set `GROK_LEADER_SOCKET` to that
same endpoint for this process and its tools. Keep normal subscription login,
permission prompts and sandbox settings. If confinement refuses shared-leader
mode, report the blocker; do not disable confinement or switch to headless mode.
Starting this TUI is a separate owner action, never an automatic join fallback.

From the actual TUI's shell tool, follow [join](join.md) and
[connect/start](sessions.md) with its own exported `GROK_SESSION_ID`. Do not
manufacture that export in another client or select a recent session. The server
owner must first apply the reviewed [Grok engine extension](../supabase/migrations/20261022094041_native_grok_session.sql).
Registration, invitation acceptance and listener start never apply SQL.

Before registration and each submission the adapter checks the current-user
active-session entry, exact session/cwd, OS process birth time, UID, installed
Grok executable, controlling terminal, explicit leader/socket/session flags and
the leader's resident roster. A stale registry, closed/reused process, foreign
session, unsupported binary/protocol or headless command fails closed. It reads
process arguments privately for these checks, never process environment or
provider credentials. The Python listener itself runs no model.

The adapter uses the existing private leader socket: protocol 1 framed ACP,
exact-session metadata and `session/prompt` with a stable Quorum prompt ID.
It never sends `initialize`, `session/new`, `session/load`, provider credentials,
permission overrides or model settings. Queue admission is only `submitted`:
the **same receiving chat** must run the envelope's `session received` command
or reply before delivery is confirmed. Close-between-check-and-send remains
uncertain; inspect the actual chat and ledger rather than blindly replaying.
Replies with `reply_to` are displayed and acknowledged without an answer loop.

Verification: real framed local IPC fixtures cover binding, queue admission,
stale/foreign/headless refusal and uncertain delivery; they do not prove a user's
UI received a message. Confirm actual idle and busy chat delivery separately.
The protocol was checked against installed 1.0.67 and the
[official source](https://github.com/xai-org/grok-build). The upstream source and
installed binary can differ; unsupported versions require renewed validation.
