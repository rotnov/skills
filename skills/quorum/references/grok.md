# Existing Grok TUI on macOS

Read this only for an owner-authorized connection of the current interactive
Grok chat. This experimental route supports **Grok Build 1.0.67 on macOS**;
Linux and Windows Grok transports are not supported. Codex and Claude support
is unchanged. Grok discovers the shared `.agents/skills` directory, including
the portable copy returned by the [automatic refresh](update.md).

An already-running standalone chat started as plain `grok`, with no leader
socket, cannot connect through this transport. Build 1.0.67 has no verified
in-place attach or promotion command. A `GROK_SESSION_ID` export proves which
chat invoked the tool; it does not create a leader socket. Keep that chat open
and report the limitation. Membership acceptance can succeed while the native
session remains unregistered and its listener stays offline.

`session connect` now reports `grok_transport_unavailable` with
`data.reason: leader_socket_missing` when the selected socket is absent.
An existing endpoint that fails validation reports `grok_transport_unavailable`;
an identity mismatch reports `session_binding_denied`. These refusals do not
register an agent. Do not select another TUI's socket, manufacture exports,
change permissions or confinement, or start a headless/new session to work
around the refusal.

The intended TUI must already have been started by its owner with explicit
process-local flags, before asking that chat to join. This example creates a
**new conversation**; it does not resume an existing chat:

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

Grok's supported existing-history startup option is `--resume <exact-session-UUID>`.
Installed 1.0.67 help and the official
[session guide](https://github.com/xai-org/grok-build/blob/2bdd1d6a6369de0e8c68132ea4539e9abd9e14a8/crates/codegen/xai-grok-pager/docs/user-guide/17-sessions.md)
distinguish it from `--session-id`, which creates a new conversation. An
owner-run future verification candidate is `grok --leader --leader-socket
<private-dir>/leader.sock --cwd <original-cwd> --resume <exact-session-UUID>`.
**This is not a supported Quorum recovery command yet:** the current process
guard rejects `--resume`, and no native UI receipt has verified this relaunch.
It would require a separate owner-authorized stop/relaunch, unchanged saved
sandbox and permission settings, the same session/cwd export, a private socket,
matching process birth/UID/executable/TTY, exact resident leader metadata and
receipt in that resumed TUI. Do not restart the chat during diagnosis or replace
the resume flag with `--session-id`, `--continue`, a title or `--fork-session`.

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
[official source](https://github.com/xai-org/grok-build). Ordinary-launch research
checked installed 1.0.67 help against upstream snapshot
`2bdd1d6a6369de0e8c68132ea4539e9abd9e14a8` (`SOURCE_REV`
`559751fdcec02d413e4c57c8832ab275e4f44980`): leader mode is selected at startup,
`--leader` is hidden, and leader management exposes no in-place attach command.
That source snapshot is not the installed binary's build commit; neither this
comparison nor local IPC fixtures prove ordinary-launch delivery in a real TUI.
Unsupported versions require renewed validation.
