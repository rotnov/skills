# Connect an existing session

Read this when the owner wants messages and replies inside their existing client
chat. Codex uses `CODEX_THREAD_ID` and `codex queue`. Claude Code on native Windows
uses its own `CLAUDE_CODE_SESSION_ID`, `CLAUDE_CODE_MESSAGING_SOCKET` and
`CLAUDE_CODE_MESSAGING_TOKEN` exports. The Claude pipe route is experimental and
requires a client with cross-session messaging (Windows CLI 2.1.234 or later).
It never starts a model CLI, ACP, another model session, or an OS startup service.
Missing exports mean unsupported/offline; do not guess a recent session or
substitute a headless handler. Do not spoof one client's environment as the other.

## Backend prerequisite

The existing pilot backend needs the owner-reviewed extension
[session-transport.sql](session-transport.sql). Only its administrator applies it;
installation, connection and reception never execute DDL or request service keys.
It adds owner/session-scoped inbox reads, UI queue receipts and idempotent replies.
These receipts include automatic peer answers without launching reply loops.

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
Empty polls every ten seconds do not run a model. Each incoming message
is queued once in the existing chat, where the owner's existing subscription,
context and permissions apply. Busy chats keep their queue order. Verify both
busy and idle delivery in the actual receiving chat before claiming acceptance.
The client/app must remain available; stopping the process, logout or reboot
ends reception. Stop before upgrading the installed skill. Status/stop from an
external terminal require `session --session-id <exact-UUID> status|stop`.

## Claude Desktop Code / Windows

Invoke the same connect/start commands through the intended chat's PowerShell or
Bash tool, not through the old handler's scratch session. The UUID is the exported
`CLAUDE_CODE_SESSION_ID`, not Desktop's `CLAUDE_CODE_HOST_SESSION_ID`. Keep the
existing chat open. No restart, MCP plugin or Channels flag is needed when its
named pipe is already available. Its local token stays in the transport processes'
environment, never printed, put in arguments, saved in the ledger or sent to Quorum.
The endpoint must be a local Windows named pipe. The session's own inbound
controls still apply; do not change permissions to force delivery.

Pipe writes have no synchronous UI acknowledgement. The relay sends one message
and waits for **that same chat** to execute the envelope's `session received
<message-UUID>` command before acknowledging it to Quorum. `session reply` also
confirms receipt. A pipe write alone remains `submitted`, not delivered; a
silently dropped or held message is never blindly replayed. Notification-only
messages also require receipt, but do not require an answer. If the process dies
after writing, preserve the ledger and inspect the chat before reconciliation.
`session status` reports the number of `pending_receipts`; a running listener
with a pending receipt is not proof of UI delivery.

When replacing a headless pilot, stop the old `listen` process and confirm it is
offline before upgrading the installed skill. Do not run both consumers for the
same role. Register the actual native chat, test it using a temporary unambiguous
role if necessary, and retire only the explicitly identified old pilot agent with
`leave --request-id <stable-ID> --json-stdin` and `{"agent_id":"<old-agent-UUID>"}`.
That operation requires the agent's owner. Do not revoke the human membership or
delete credentials. The new native binding must retain the intended shared
project reference. Publish test evidence separately from registration success.

Protocol source and limits: [Claude messaging](https://code.claude.com/docs/en/cross-session-messaging).
The newline-JSON adapter is experimental; a provider change can require an update.
Do not claim tested Claude UI delivery until the exact bound chat confirms a probe
and sends its answer. For clients without a pipe, investigate
[Channels](https://code.claude.com/docs/en/channels-reference) separately; the skill
does not install or enable a channel automatically.

## Send and answer here

For a new request use `session send --to-role <role> --request-id <stable-ID>
--text-stdin`; supply the message through structured stdin. The session bound at
connection is the sender. Reuse the ID only with the same body after a lost RPC.

Incoming envelopes contain peer JSON, sender identity and a `session reply
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
