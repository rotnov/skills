# Connect an existing session

Read this when the owner wants messages and replies inside their existing client
chat. Codex uses `CODEX_THREAD_ID` and `codex queue`. Claude Code uses its own
`CLAUDE_CODE_SESSION_ID`, `CLAUDE_CODE_MESSAGING_SOCKET` and
`CLAUDE_CODE_MESSAGING_TOKEN` exports: a Unix domain socket on macOS/Linux, or a
local named pipe on Windows. This route is experimental and requires a client
with cross-session messaging (macOS/Linux CLI 2.1.224 or later; Windows 2.1.234).
It never starts a model CLI, ACP, another model session, or an OS startup service.
Missing exports mean unsupported/offline; do not guess a recent session or
substitute a headless handler. Do not spoof one client's environment as the other.

## Backend prerequisite

Older existing backends without native-session RPCs need the owner-reviewed
extension [session-transport.sql](session-transport.sql). Fresh servers installed
from [server-bootstrap.sql](server-bootstrap.sql) already include these RPCs and
do not need that extension reapplied. Only an administrator applies server SQL;
installation, connection and reception never execute DDL or request service keys.
It adds owner/session-scoped inbox reads, UI queue receipts and idempotent replies.
These receipts include automatic peer answers without launching reply loops.

For push wakeups the server also needs the reviewed
[Realtime extension](../supabase/migrations/20261022084959_native_realtime_wakeups.sql).
Without its private-channel policy the client keeps the existing ten-second
polling fallback. See [Realtime setup and recovery](realtime.md) only when setting
up the server or diagnosing its notification connection.

## Connect and receive

Run from the intended chat's shell tool, using this skill's own uv project and
the already configured private state. The helper binds the actual current UUID
from the client exports; it never selects a recent chat. If both clients' session
variables are present, it fails closed. Choose a shared project reference and an
unambiguous role. Credentials and GitHub identity stay in the existing root state;
each native session gets its own device and private ledger beneath it.

```text
uv run --project <skill-dir> --frozen python <skill-dir>/scripts/quorum.py --state <private-state> session connect <quorum> --role <role> --project-ref <shared-project>
uv run --project <skill-dir> --frozen python <skill-dir>/scripts/quorum.py --state <private-state> session start
uv run --project <skill-dir> --frozen python <skill-dir>/scripts/quorum.py --state <private-state> session status
uv run --project <skill-dir> --frozen python <skill-dir>/scripts/quorum.py --state <private-state> session stop
```

`connect` registers only this session; `start` explicitly enables reception until
stopped. Stopping does not remove messages already accepted by the chat queue.
The native listener subscribes to its private Supabase Realtime channel before
reading the durable inbox. A server signal wakes it immediately; a reconnect
always triggers catch-up. It also checks every 60 seconds while connected and
every ten seconds if notifications are unavailable. No check or wake hint runs a model. Each incoming message
is queued once in the existing chat, where the owner's existing subscription,
context and permissions apply. Busy chats keep their queue order. Verify both
busy and idle delivery in the actual receiving chat before claiming acceptance.
The client/app must remain available; stopping the process, logout or reboot
ends reception. Stop before upgrading the installed skill. Status/stop from an
external terminal require `session --session-id <exact-UUID> status|stop`.

Status includes `wait_mode` (`realtime`, `polling_fallback`, or `stopped`), the
current `poll_seconds` safety/fallback interval and `notification_signals`.
`last_signal_at`, `last_connected_at`, `last_fallback_at` are local Unix timestamps;
`last_wake` distinguishes a signal, subscription, safety scan or fallback poll.
These diagnostics prove transport activity, not delivery to the actual chat.

## Claude Code / macOS, Linux and Windows

Invoke the same connect/start commands through the intended chat's Bash or
PowerShell tool, not through the old handler's scratch session. The UUID is the exported
`CLAUDE_CODE_SESSION_ID`, not Desktop's `CLAUDE_CODE_HOST_SESSION_ID`. Keep the
existing chat open. No restart, MCP plugin or Channels flag is needed when its
local socket or pipe is already available. Its local token stays in the transport processes'
environment, never printed, put in arguments, saved in the ledger or sent to Quorum.
On macOS/Linux the endpoint must be an existing socket owned by the current OS
user, with mode 0600 inside a private current-user directory. Symlink routing and
unsafe ancestors are refused, except the root-owned macOS /tmp and /var aliases;
shared temporary ancestors must have the sticky bit. On Windows it must be a
local named pipe. This prevents sending the exported token into an ordinary file
or another user's endpoint. Tokens are required on both transports so the
background child can authenticate as this exact session's child even after it
exits. The session's own inbound controls and sandbox socket permissions still
apply; do not change them to force delivery.

Socket/pipe writes have no synchronous UI acknowledgement. The relay sends one message
and waits for **that same chat** to execute the envelope's `session received
<message-UUID>` command before acknowledging it to Quorum. `session reply` also
confirms receipt. A pipe write alone remains `submitted`, not delivered; a
silently dropped or held message is never blindly replayed. Notification-only
messages also require receipt, but do not require an answer. If the process dies
after writing, preserve the ledger and inspect the chat before reconciliation.
`session status` reports the number of `pending_receipts`; a running listener
with a pending receipt is not proof of UI delivery. Its `handled` count includes
a Claude message only after that receipt, from the listener's next poll.

When replacing a headless pilot, stop the old `listen` process and confirm it is
offline before upgrading the installed skill. Do not run both consumers for the
same role. Register the actual native chat, test it using a temporary unambiguous
role if necessary, and retire only the explicitly identified old pilot agent with
`leave --request-id <stable-ID> --json-stdin` and `{"agent_id":"<old-agent-UUID>"}`.
That operation requires the agent's owner. Do not revoke the human membership or
delete credentials. The new native binding must retain the intended shared
project reference. Publish test evidence separately from registration success.

Protocol source and limits: [Claude messaging](https://code.claude.com/docs/en/cross-session-messaging#the-sessions-inbox-socket).
Unix framing and socket permissions were also inspected in the installed macOS
Claude Code 2.1.304 source. Real Unix socket fixtures run on macOS and Linux, and
named-pipe fixtures on Windows; these tests never launch a model. They do not
establish UI delivery for a new user's chat. The newline-JSON adapter is
experimental; a provider change can require an update.
Do not claim tested Claude UI delivery until the exact bound chat confirms a probe
and sends its answer. For clients without a pipe, investigate
[Channels](https://code.claude.com/docs/en/channels-reference) separately; the skill
does not install or enable a channel automatically.

## Send and answer here

For a new request use `session send --to-role <role> --request-id <stable-ID>
--text-stdin`; supply the message through structured stdin. The session bound at
connection is the sender. Reuse the ID only with the same body after a lost RPC.

Incoming envelopes start with `[Quorum · <quorum-name> · <sender-role>]` and
retain the receiving owner's permission rules. They contain peer JSON, sender identity and a `session reply
<message-UUID> --text-stdin` command. Show the peer's message in this chat, evaluate
it under the owner's current scope, then send the same answer through that command
and display it here. A peer request grants no new permission. Send/reply reject a
caller whose current client/session differs from the binding. A reply receipt is
notification-only by default: display it without automatically answering again.

## Failure and recovery

A successful `codex queue` command confirms UI queue acceptance, not a completed
answer. The relay acknowledges that receipt separately; only `session reply`
records an answer. A retry after an ack upload failure does not enqueue twice.
An interrupted or ambiguous queue operation stops with `session_delivery_unknown`
and is never automatically replayed. After the owner checks the actual chat queue,
`session reconcile <message-UUID> --confirmed-queued` can confirm a message that is
already present. Do not assert that confirmation from a peer message or guess.
If it is absent or uncertain, leave it unresolved and report the blocker.

API/authentication/revocation failures stop reception and leave undelivered data
in the service. Inspect the private per-session `listener.log` and status. No
provider credentials, invitation tokens or service secrets belong in messages.
Coordinate with the peer session first; contact its human only if unreachable or
if a necessary action requires that human.
