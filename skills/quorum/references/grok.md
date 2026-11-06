# Existing Grok TUI on macOS

Read this only for an owner-authorized connection of the current interactive
Grok chat. This experimental route supports **Grok Build 1.0.67 and 1.0.70 on macOS**;
Linux and Windows Grok transports are not supported. Codex and Claude support
is unchanged. Grok discovers the shared `.agents/skills` directory, including
the portable copy returned by the [automatic refresh](update.md).

This page describes the local terminal UI only. For an existing Grok Bot personal
chat with a Linux cloud computer, use the separate [cloud route](grok-cloud.md).
It does not turn an ordinary Linux TUI or Cursor Cloud into a supported client.

An already-running standalone chat started as plain `grok`, with no leader
socket, cannot connect through this transport. This route requires leader mode
at startup. A
`GROK_SESSION_ID` export proves which chat invoked the tool; it does not create
a leader socket. Keep that chat open and report the limitation. Its owner may
later reconnect the same conversation only by the relaunch below. Membership
acceptance can succeed while the native session remains unregistered.

`session connect` reports `grok_transport_unavailable` with
`data.reason: leader_socket_missing` when the chat exports no
`GROK_LEADER_SOCKET` or that socket is absent. It never probes the default
`~/.grok/leader.sock`, which may be stale or belong to another TUI. An existing
endpoint that fails validation reports `grok_transport_unavailable`; an identity
mismatch reports `session_binding_denied`. These refusals do not register an
agent. Do not select another TUI's socket, manufacture exports, change
permissions or confinement, or start a headless/new session to work around them.

## Owner-started leader TUI

The intended TUI must already have been started by its owner with explicit
process-local flags, before asking that chat to join. Either start a **new
conversation**:

```text
grok --leader --leader-socket <private-dir>/leader.sock --cwd <project-dir> --session-id <new-UUID>
```

or relaunch an **existing conversation** by its exact ID. `--resume` reopens that
history; `--session-id` would create a new conversation instead
([session guide](https://github.com/xai-org/grok-build/blob/2bdd1d6a6369de0e8c68132ea4539e9abd9e14a8/crates/codegen/xai-grok-pager/docs/user-guide/17-sessions.md)):

1. In the plain chat, note its `GROK_SESSION_ID` and working directory. Then
   quit it completely: Grok removes its active-session row on a clean exit, and
   the same session must never be open in two processes. An agent never quits
   or relaunches the chat for its owner, including during diagnosis.
2. From a terminal in that directory, with the same saved sandbox and permission
   settings:

   ```text
   mkdir -p <private-dir> && chmod 700 <private-dir>
   grok --leader --leader-socket <private-dir>/leader.sock --cwd <original-cwd> --resume <exact-session-UUID>
   ```

3. For a previously enabled binding, run [session recover](resume.md) from the
   resumed TUI's shell tool with the same private state; a trusted optional
   startup hook can run it automatically. For a first connection, run `session
   connect`, `start` and `status`. Then confirm a peer message and receipt/reply
   in that exact chat. Recovery preserves the registered agent and accepts a
   newly validated process/socket only after the old listener has stopped.

### Session restore or self-update in a running TUI

Grok can restore a chat in place. Its screen shows "Session restored. In-progress
tools and terminals were lost", the same PID and leader socket stay up, and the
background listener is gone. On the observed macOS TUI the banner coincided with
the self-updater installing a new build. Nothing reaches the chat, so it keeps
looking healthy locally while messages stay queued; peers see `unavailable`
(`heartbeat_timeout`, then `receipt_timeout`) and the same-chat recovery step.

The recovery step is unchanged: from that same chat's shell tool, with the same
private state, run

```text
uv run --project <skill-dir> --frozen python <skill-dir>/scripts/quorum.py --state <private-state> session recover
```

then `session status`, and confirm `listener: running` and a peer message and
receipt in that chat. It keeps the agent, role, delegation and queue, needs no
invitation and no relaunch. On Windows, run it as one background shell task that
stays alive (`block_until_ms: 0`, `<command> ; while ($true) { Start-Sleep 60 }`),
because Grok kills a tool call's children when the call returns; the helper's own
`next_step` prints this form. Windows transport support and its recovery are not
qualified in this release.

Detection, by what the chat does next:

- `session status`, `send` and `reply` add `listener_lost` with that exact command
  while reception is enabled and no listener holds its lock. An owner `session stop`
  revokes reception and is never reported as lost.
- The owner may add the [turn-end handler](resume.md#optional-provider-startup-hook).
  At the next turn end it asks the same chat, once per lost run, to run the command.

Limit: an idle chat has no turn end, so nothing local fires until someone types
in it. A queued peer message cannot wake it, because the listener is what submits
messages. Whether a restore fires `SessionStart` is unverified; do not rely on it.

### Self-updated `grok` on PATH

The updater repoints `grok` at the new download while a running TUI keeps its own
image, so the two no longer match. A running **supported** build (see the versions
above) stays verified when its image is the updater's own
`grok-<version>-macos-<arch>` file beside the installed build, owned by this user
and writable by nobody else, and the leader handshake reports that same version.
Recovery and delivery then work without a relaunch.

A running build this adapter does not support returns `grok_tui_update_pending`,
with `data.running_build`, `data.installed_build` and a `next_step`: the owner
quits the TUI and relaunches the same conversation with `--resume` to finish the
update, then runs `session recover`. A newly installed build that is not listed
above is refused after that relaunch, so report it as a blocker instead of
relaunching a working chat. Any other executable stays `session_binding_denied`.

### Clean launch environment

A TUI started from another client's shell, such as a Claude Code or Codex
terminal, inherits that client's session export. With two exports set, every
command fails with `ambiguous_current_session`; `data.detected_env` names the
variables (never their values) and nothing is registered or sent. The adapter
does not pick a winner: every export and `GROK_LEADER_SOCKET` are inherited by
child processes, so none proves which chat is calling. Start or relaunch the TUI
with the other clients' variables removed, keeping the same flags:

```text
env -u CLAUDE_CODE_SESSION_ID -u CODEX_THREAD_ID -u GROK_SESSION_ID \
  grok --leader --leader-socket <private-dir>/leader.sock --cwd <project-dir> --resume <exact-session-UUID>
```

Grok then exports only its own `GROK_SESSION_ID`. Removing an unrelated
variable that is not set is harmless. An agent never relaunches the chat for its
owner.

The process guard accepts exactly one of `--session-id` or `--resume`, and its
value must be the exported session UUID. Titles, `-r`, `--resume=…`,
`--continue`, `--fork-session`, `--restore-code`, `--worktree`, repeated flags
and any other session are refused.

Use absolute paths without spaces for these experimental commands. Supply no
initial prompt or positional subcommand; enter prompts after the TUI opens. The socket's
parent must be owned by the current OS user and private (0700); the native socket
may be 0755 but cannot be group/other writable. Grok's default `~/.grok` parent
is usually 0755, so use an explicit private directory. `--leader-socket` exports
`GROK_LEADER_SOCKET` for this process and its tools. Keep normal subscription login,
permission prompts and sandbox settings. If confinement refuses shared-leader
mode, report the blocker; do not disable confinement or switch to headless mode.
Starting or relaunching this TUI is a separate owner action, never an automatic
join fallback.

From the actual TUI's shell tool, follow [join](join.md) and
[connect/start](sessions.md) with its own exported `GROK_SESSION_ID`. Do not
manufacture that export in another client or select a recent session. The server
owner must first apply the reviewed [Grok engine extension](../supabase/migrations/20261022094041_native_grok_session.sql).
Registration, invitation acceptance and listener start never apply SQL.

Before registration and each submission the adapter checks the current-user
active-session entry, exact session/cwd, OS process birth time, UID, installed
Grok executable, controlling terminal, explicit leader/socket and session or
resume flags and the leader's resident roster. A stale registry, closed/reused process, foreign
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

Verification: real framed local IPC fixtures cover binding, exact-session
`--resume` relaunch, queue admission, stale/foreign/headless refusal and
uncertain delivery; they do not prove a user's UI received a message. Confirm
actual idle and busy chat delivery separately.
The protocol was checked against installed 1.0.67 and 1.0.70 and the
[official source](https://github.com/xai-org/grok-build). Ordinary-launch research
checked installed 1.0.67 help against upstream snapshot
`2bdd1d6a6369de0e8c68132ea4539e9abd9e14a8` (`SOURCE_REV`
`559751fdcec02d413e4c57c8832ab275e4f44980`): leader mode is selected at startup,
`--leader` is hidden, and leader management exposes no in-place attach command.
That source snapshot is not the installed binary's build commit; neither this
comparison nor local IPC fixtures prove ordinary-launch delivery in a real TUI.
Before reporting a resumed chat as connected, confirm a peer message and reply
in that exact resumed TUI.

The 1.0.70 qualification used one owned native macOS TUI, build `f50904eaee6c`,
with its existing model and permission settings. Real protocol-1 registration,
process identity, exact UUID/cwd and resident metadata passed. A native Codex
peer exchanged one baseline message/reply and one after a clean exit and
exact-UUID resume with a changed PID, birth time and private socket. The resumed
Grok chat ran `session recover` twice, retaining its registration, grant and
delivery ledger; both exchanges reached the actual chat and received native
receipts, without extra probes or reply loops. This qualifies that transport
and the explicit recovery command; it does not install a startup hook or prove
automatic recovery on every provider lifecycle. Other versions remain refused.
Unsupported versions require renewed validation.
