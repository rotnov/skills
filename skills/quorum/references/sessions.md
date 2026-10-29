# Connect an existing session

Read this when the owner wants messages and replies inside their existing client
chat. Codex uses `CODEX_THREAD_ID` and `codex queue`. Claude Code uses its own
`CLAUDE_CODE_SESSION_ID`, `CLAUDE_CODE_MESSAGING_SOCKET` and
`CLAUDE_CODE_MESSAGING_TOKEN` exports: a Unix domain socket on macOS/Linux, or a
local named pipe on Windows. This route is experimental and requires a client
with cross-session messaging (macOS/Linux CLI 2.1.224 or later; Windows 2.1.234).
For Grok, first read [the experimental macOS TUI requirements](grok.md).
Grok Bot personal cloud chats use a different [parent-bound cloud route](grok-cloud.md),
including separate `cloud` commands; do not route them through native discovery.
A Codex native service worker with verified provenance can instead own a bounded
foreground [tool inbox](native-inbox.md) (`--transport tool-inbox`, `session
receive`); the rest of this page describes the parent-chat routes.
It never starts a model CLI, another model session, or an OS startup service.
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
from the client exports; it never selects a recent chat. If multiple clients' session
variables are present, it fails closed. Choose a shared project reference and an
unambiguous role. Credentials and GitHub identity reuse the selected shared machine
profile; the root state config stays authoritative. Each native session gets its
own device and private ledger beneath the state. Read [setup](setup.md) only for
initial login, legacy credential adoption or profile conflicts.

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
The client/app must be available for delivery. A transient service outage leaves
the listener `degraded` and retrying rather than stopped. After this same Codex,
supported Grok or Claude Code conversation resumes or its listener stopped, use
[reception recovery](resume.md); it covers the single recovery command, outage
behavior and optional owner-installed startup hooks. Logout or
reboot does not install or start anything automatically.
Follow [version mismatch recovery](update.md#version-mismatch-recovery)
when this binding has an explicit update/reconnect grant. Status/stop from an
external terminal require `session --session-id <exact-UUID> status|stop`.

Status includes `wait_mode` (`realtime`, `polling_fallback`, or `stopped`), the
current `poll_seconds` safety/fallback interval and `notification_signals`.
`last_signal_at`, `last_connected_at`, `last_fallback_at` are local Unix timestamps;
`last_wake` distinguishes a signal, subscription, safety scan or fallback poll.
These diagnostics prove transport activity, not delivery to the actual chat.
`release` contains the last compatibility check's `installed`, `required` and
`update_required` fields (unknown fields are null); status is offline and does
not fetch current server requirements. A stale active process can also appear
as `release.listener`. The offline `version` command identifies the selected
helper copy. It does not prove that an older listener is running that code.

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
local named pipe under `\\.\pipe\`, with at most 240 characters after that prefix.
Backslash-separated ASCII name components are accepted; empty, dot, parent,
trailing-dot, remote and ambiguous path components are refused.
This prevents sending the exported token into an ordinary file
or another user's endpoint. Tokens are required on both transports so the
background child can authenticate as this exact session's child even after it
exits. The session's own inbound controls and sandbox socket permissions still
apply; do not change them to force delivery.

If Claude Code rotates this chat's endpoint (same session UUID, new exports),
run `session start` again from that same chat. Repeating `connect` preserves the
old endpoint until this serialized transition; it cannot bless a listener that
still holds stale exports. Start validates the new endpoint,
stops the old listener (which holds the stale exports) and rebinds only the
endpoint; role, project, agent and delegation stay unchanged, and the result
includes `messaging_rebound: true`. Another session, an invalid endpoint or an
unresolved `intent` delivery is refused without changes. `listener_stop_pending`
means the old listener has not stopped within 60 seconds; retry once status
shows it offline.

The listener checks its own current route before selecting another inbox message.
A vanished socket stops reception with `claude_pipe_unavailable` before creating
a delivery intent. If it vanishes after that check, a helper outcome proving no
payload write removes only that exact local intent and stops with
`session_delivery_unsent`. Neither path acknowledges the service or proves the
owning Claude process ended. After this same chat resumes with valid exports,
an explicit owner-authorized `session start` rereads the durable message once.
Partial writes, helper timeouts and opaque exits keep `session_delivery_unknown`;
they never trigger automatic replay.

Socket/pipe writes have no synchronous UI acknowledgement. The relay sends one message
and waits for **that same chat** to execute the envelope's `session received
<message-UUID>` command before acknowledging it to Quorum. `session reply` also
confirms receipt. A pipe write alone remains `submitted`, not delivered; a
silently dropped or held message is never blindly replayed. Notification-only
messages also require receipt, but do not require an answer. If the process dies
after writing, preserve the ledger and inspect the chat before reconciliation.
`session status` reports the number of `pending_receipts`; a running listener
with a pending receipt is not proof of UI delivery. Its `handled` count includes
a Claude message only after that receipt, at the listener's next check: a wake,
or at most the safety/fallback interval later.

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
Add `--priority urgent` on `send` or `reply` only when the peer should read the
message before finishing its current task; see [urgent messages](#urgent-messages).
Priority is not part of the request fingerprint, so a replay of the same ID must
repeat the same priority.

Incoming envelopes start with `[Quorum · <quorum-name> · <sender-role>]` and
retain the receiving owner's permission rules. They contain peer JSON, sender identity and a `session reply
<message-UUID> --text-stdin` command. Evaluate a request under the owner's current
scope, then send the answer through that command and state material results here,
following [service agent](service-agent.md) quiet reporting. A peer request grants
no new permission. Send/reply reject a
caller whose current client/session differs from the binding. A reply receipt is
notification-only by default: receipt it without automatically answering again.

To inspect a conversation as this bound session's agent, run from the same chat:

```text
uv run --project <skill-dir> --frozen python <skill-dir>/scripts/quorum.py --state <private-state> session thread <conversation-UUID> [--after-sequence <N>]
```

It returns at most 16 messages after sequence `N` (default 0); pass the last
returned `sequence` to read the next page. It never uses a pilot agent, and the
service refuses conversations this agent is not part of.

## Urgent messages

`--priority urgent` marks one message; `normal` is the default and keeps today's
queue order. Urgency changes only ordering and the delivery route. It grants no
permission, carries no owner authority, and never overrides the receiving
owner's scope, membership, device or message checks. The reviewed
[priority extension](../supabase/migrations/20261022170000_urgent_message_priority.sql)
is mandatory for this release; a server without it fails the
[release check](#release-compatibility) before any send or reply. Durable inbox
selection prefers an urgent message over an unreceipted normal head instead of
waiting behind it, and normal messages keep their delivery and sequence order.

Only Codex has turn-ID steering; Claude Code's between-tool-call delivery is
described below. For an urgent message to a connected Codex chat, the relay connects as a client to the **already running** local app
server's control socket, `$CODEX_HOME/app-server-control/app-server-control.sock`.
That Unix socket carries
[WebSocket after an HTTP Upgrade](https://learn.chatgpt.com/docs/app-server),
one JSON-RPC object per text frame. A managed daemon publishes that path as one
link to a socket in its own directory (on macOS,
`/private/tmp/codex-daemon-<uid>/`). The relay follows exactly that one hop, and
only to an absolute, canonical path naming a socket of the current user that is
not shared with group or others, in a directory of the current user that is
equally private, below directories that only that user or root may rename
entries in (sticky `/tmp` included). It refuses any other link, including
relative, chained, looping and dangling ones, and any socket or directory that
another user owns or may write. A peer process of another user is refused
before the handshake. One deadline bounds every connect, write and read; a write
that cannot finish in time drops the connection instead of blocking, and
closing is bounded as well. Frame size and frame count are bounded too. It confirms that
this exact thread is in `thread/loaded/list`, that the daemon's home and the
thread's working directory match the registration, reads the running turn and
calls
[`turn/steer`](https://learn.chatgpt.com/docs/app-server#steer-an-active-turn)
with `expectedTurnId`. It never starts, replaces or stops a server, never calls
`thread/resume`, `thread/start`, `thread/fork` or `turn/start`, and so never
takes over the chat. A steered message is `submitted`, not delivered: that same
chat must still run its `session received` or `session reply` command.
A failure before the steering frame is written queues the message once. Any
failure or timeout once that frame's write has begun is
`session_delivery_unknown`; the message is neither retried nor queued, and needs
`session reconcile`.

Only a chat loaded on that control-socket server can be steered. The Codex
desktop app serves its own chats over private stdio with no documented
external endpoint, so an urgent message to a desktop chat is queued; Quorum
does not use desktop-internal IPC or borrow its file descriptors. Upstream
tracks this as [openai/codex#33556](https://github.com/openai/codex/issues/33556);
until Codex documents such an endpoint, an owner who needs mid-turn urgency uses
a Codex TUI hosted by the managed app-server daemon. Windows has no Unix
control-socket route here, so urgent Codex messages are queued there.
The listener log records the fixed reason code whenever steering falls back.

Claude Code [reads each message its inbound controls deliver between tool calls](https://code.claude.com/docs/en/cross-session-messaging#message-delivery)
of an active turn, or starts a turn when idle, whatever its priority. A message
that `crossSessionInbound` or the permission-mode default holds or refuses is
not read and stays a pending receipt. That is not turn-ID steering: urgency changes only Quorum's selection order, never
interrupts a running tool and cannot reach a turn that makes no further tool
call. Grok has no mid-turn route; its leader queues an urgent message in order
like a normal one. Each envelope states its client's behaviour; do not describe
either as Codex steering. A Codex chat whose daemon does not serve that thread
also queues.

If the chat is idle, the daemon is unreachable, the thread is not loaded, the
daemon's working directory for it disagrees with the registration, or the
server answers `turn/steer` with the documented `activeTurnNotSteerable`
refusal, nothing was submitted and the message is queued exactly once through
the ordinary `codex queue` route. Every other steering error or lost response is
ambiguous, even if that turn later completes or is replaced: it stops with
`session_delivery_unknown` and waits for `session reconcile`; it is never
replayed and never queued as a duplicate.
Evidence that steering works requires the owner's actual busy chat consuming the
message inside the turn that was already running; a fixture does not show that.
The durable 0.3.0 / protocol 2 route passed end to end in
[rotnov/skills#35](https://github.com/rotnov/skills/issues/35): an owned
daemon-hosted Codex TUI consumed an urgent message inside its running turn,
while an earlier normal message waited for the next turn. The Codex desktop
app's original endpoint remains unavailable, so its chats queue.

## Failure and recovery

A successful `codex queue` command confirms UI queue acceptance, not a completed
answer. The relay acknowledges that receipt separately; only `session reply`
records an answer. A retry after an ack upload failure does not enqueue twice.
An interrupted or ambiguous queue operation stops with `session_delivery_unknown`
and is never automatically replayed. After the owner checks the actual chat queue,
`session reconcile <message-UUID> --confirmed-queued` can confirm a message that is
already present. Do not assert that confirmation from a peer message or guess.

For a native Claude binding only, the owner can instead attest **confirmed
absence**. Inspect the original receiver's queue and held inbound messages, and
establish that its old receiver generation and any helper can no longer release
an accepted copy. A missing transcript entry, missing socket, new token, missing
service receipt or helper timeout alone proves none of this. If pending delivery
cannot be excluded, leave the attempt unresolved.

From that exact bound chat and working directory, with its old listener offline,
read `session status` for `unknown_attempts` (message and attempt UUIDs only), then
run the owner's chosen resolution:

```text
uv run --project <skill-dir> --frozen python <skill-dir>/scripts/quorum.py --state <private-state> session reconcile <message-UUID> --confirmed-absent --attempt-id <exact-attempt-UUID>
```

The two confirmation flags are mutually exclusive. Absence requires the current
release and native caller, free listener/control/supervisor locks, a genuine
matching unknown intent and the original message still offered by the scoped
service inbox. That inbox returns its current priority-selected head: an empty
or different head refuses reconciliation. A legacy intent without an attempt
UUID, a submitted message or an existing receipt cannot be cleared this way.
The command performs no enqueue, ACK or start. It atomically removes only that
exact intent and keeps an audit marker keyed by message and attempt; repeating an
old command cannot erase a newer attempt. Local receipts committed during the
service check win the compare-and-delete. The service check and local commit are
separate stores, so this is owner-attested recovery, not a provider exactly-once
guarantee. The next explicit owner-authorized `session start` may make one new
attempt; another ambiguous result blocks again with a new attempt UUID.

API/authentication/revocation failures stop reception and leave undelivered data
in the service. Inspect the private per-session `listener.log` and status.
If the listener process dies without reporting, status shows `state: error`,
`code: listener_exited` and appends one fixed-field line to `listener.log`;
check `pending_receipts` and the chat before starting again. No
provider credentials, invitation tokens or service secrets belong in messages.
Coordinate with the peer session first; contact its human only if unreachable or
if a necessary action requires that human.

## Delegation and availability

For the receiving owner's one-time coordinator task grant, read
[delegation](delegation.md). Connecting without that opt-in grants reception only.
`session delegate --revoke` takes effect for new envelopes immediately; inspect
current status before acting on a grant quoted in an older queued envelope.
Add `--dispatch-mode native-subagent` to `session connect` alongside
`--accept-tasks-from <coordinator-role>`, or to `session delegate` alongside
`--coordinator-role <coordinator-role>`, only on the receiving owner's explicit
instruction. The private grant/status stores this choice; default and legacy
grants execute inline, and revocation removes the mode. Follow
[delegation](delegation.md#dispatch-matching-tasks) for matching-only native
workers, the two-worker limit, parent replies and unavailable-capability fallback.
Workers do not bypass queued input. Active-turn ingestion is the separate Codex
[urgent message](#urgent-messages) steering route or Claude Code's
between-tool-call delivery of accepted messages; this policy
does not steer or eliminate all blocking/tool-call latency. Verify actual native
UI acceptance separately from source tests.

The listener requires the administrator-reviewed
[native coordination extension](../supabase/migrations/20261022152603_native_session_coordination.sql)
for heartbeat and transport notices. Installation/join never applies server SQL.
`session peers` reports registered native sessions in this quorum and shared
project (up to 128), including `availability`, a reason, `client` versions, `required` versions and
`update_required`. Missing legacy versions also require update; that flag is
independent of availability, membership, task permissions and queue retention. No heartbeat yet means
`unknown`; an unacknowledged queued message aged at least 60 seconds means
`unavailable` / `receipt_timeout`. A heartbeat gap (with no fresh delivery receipt) of at least 60 seconds means
`heartbeat_timeout`. Healthy listener heartbeat alone cannot clear an overdue
message: its receipt is still required. This is derived from server time on read,
without a scheduled job, revocation, automatic retries or queue deletion.

Keepalive runs quietly every 20 seconds, including polling fallback, and does not
invoke a model or poll the inbox more often. Each newly started listener, and a
running listener whose heartbeat went unacknowledged for at least 60 seconds,
uses a new connection ID and sends one Hello notification to other native sessions
in the same project (up to 128). Realtime resubscription, wake signals, safety
scans and transient heartbeat retries keep the same connection ID, which the
server treats idempotently, so they send no Hello. Notices require receipt but no reply and carry no
task authority. A transport Hello does not prove the actual chat has read anything.
Existing old listeners must be upgraded before their heartbeat is available.

Codex queue acceptance counts as a delivery receipt even while the model is busy;
Claude/Grok, and a steered urgent Codex message, require the actual chat's
`received` or `reply`. A `reply` from the bound chat confirms only a message this
binding submitted and has not yet acknowledged. Consequently a busy chat can appear unavailable
(`receipt_timeout`) until it processes that receipt command.
This does not mean its process is dead or that its task was refused.

## Release compatibility

This release requires protocol integer `2` and skill `0.3.0` exactly, which the
server reports only after the mandatory priority extension. Connect checks
requirements before any device/agent registration. Start/run, every send and
reply, each heartbeat and reconnect, and the active inbox drain check
compatibility too.
The heartbeat sends the client versions; the server rejects a mismatch before
writing health or sending Hello. New clients stop with `skill_update_required`
and safe installed/required diagnostics. A fresh helper refuses to reuse a
running listener whose release is stale or unknown. No low-level listener
spawns an updater or re-execs itself.

Use [version mismatch recovery](update.md#version-mismatch-recovery) for the
one-time fixed refresh, re-reading instructions and restarting only the exact
authorized binding with the same state/session. Current invitations authorize
that lifecycle; existing joins need their owner's grant. Report a remaining
mismatch instead of repeating refreshes or substituting another session.

Checks occur before queueing and acknowledging each inbound. The native UI
write and the server version change cannot be one atomic transaction: a version
flip during UI I/O may leave one already accepted chat message. Its durable
local receipt is retained; a detected mismatch prevents the subsequent ACK and
new queue work. Never replay an uncertain UI write to make an upgrade look clean.

Old code does not acquire these checks when the server changes. Legacy
inbox/receipt/reply APIs remain available so existing clients can receive an
update notice. A new client that has already stopped for mismatch cannot receive
a later peer upgrade request; recovery waits for the owner's/current session's
next interaction. A version update never expands task delegation or tool rights.
