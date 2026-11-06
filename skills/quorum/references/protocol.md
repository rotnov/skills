# Coordination contract

The live contract is the [native session receipt](#native-session-receipts). Extend
only that. The handler, ACP worker and work tracker are a
[frozen historical pilot](#frozen-historical-pilot). New server behaviour follows
[add server behaviour](server.md#add-server-behaviour).

## Transport

The configured HTTPS origin serves Supabase Auth and PostgREST. Requests are
restricted to named RPCs at `/rest/v1/rpc/<operation>`; arbitrary paths, plaintext
HTTP and redirects carrying credentials are rejected. The publishable project key
is not a server credential. Human access/refresh tokens stay in the owner process.

QuorumApi prefixes payload fields with `p_`. Dispatcher mutations require
`p_request_id`; native receipts and replies use message IDs for deduplication,
and listener heartbeats use a stable connection UUID. Deploying this contract
requires the separately reviewed backend; it is not supplied by a generic Supabase
database.

Public errors are stable codes; API failure bodies and credentials are not echoed.
There is no exactly-once guarantee for arbitrary external actions, nor a claim
that database fencing can stop a disconnected operating-system process.

## Native session receipts

A message stays durable on the server until its recipient has a `session_receipts`
row. Every native route uses these RPCs, and none exposes a table. Each `session_*`
RPC checks the caller's exact agent and session (`native_session`); `message_send`
goes through the dispatcher, which checks the sender agent's owner.

| Step | RPC | Contract |
| --- | --- | --- |
| Send | `message_send` | Explicit quorum, sender, recipient and conversation IDs, optional `reply_to`, a UTF-8 body up to 16 KiB, and `priority`. Writes the message and its `deliveries` row, which orders the inbox. |
| Select | `session_inbox` | At most one message with no receipt for this agent: urgent first, then delivery time, conversation and sequence. It adds `notification` and `maintenance` metadata and reads no attempt or lease. |
| Receipt | `session_ack` | Idempotent. Inserts the receipt and refreshes `session_health`, so a receipt also counts as liveness. This is what moves the queue. |
| Answer | `session_reply` | One answer per message; a different second body is `reply_already_sent`. Marks the original delivery `answered` and writes no receipt, so the client still acknowledges. |
| Read one | `session_message` | One message addressed to this agent, or null. Reads no receipt and writes nothing; for a queued pointer the client calls `session_ack` after it. |
| Check one | `session_message_unreceipted` | Whether one exact message is still unreceipted; no body or sender. Used by `session reconcile --confirmed-absent`. |
| Task check | `session_task_authority` | See the [authority card](#authority-card). |

`priority` is `normal` (default) or `urgent`, on `message_send` and `session_reply`;
any other value is rejected. Priority is metadata: it orders durable selection and
selects the client's delivery route, and never affects identity, membership, device,
ownership or any permission check. It stays outside the deduplication fingerprint,
so a replayed request ID must repeat the stored priority.

Conversations are ordered. An automatic reply needs a receipt and no answer, so
replies do not loop.

### When each route acknowledges

`scripts/quorum_delivery.py` is the one local ledger: it owns each `delivery:<message UUID>`
row, its compare-and-set transitions, the `session_ack` call and the unknown and unsent
outcomes. A client adds only its identity proof and a push or pull function. The
routes differ on purpose, and this table is their contract.

| Route | Local states | Who acknowledges, and when | After a crash |
| --- | --- | --- | --- |
| Parent Claude pipe, parent Grok leader frame, Codex `turn/steer` | `intent`, `submitted`, `queued`, `acked` | The same chat, with `received` or `reply`. The CLI records `queued` first, then runs `session_ack`, then writes `acked`. A pipe write, a whole frame or an accepted steer is not a receipt | An `intent` is never replayed and needs [owner reconciliation](sessions.md#failure-and-recovery). A `submitted` row stays and nothing is sent again. A `queued` row is finished by the next listener pass |
| Parent `codex queue` | As above, and `submitted` keeps its attempt UUID | The same chat, when `session message` returns the body, or with `received` or `reply`. CLI acceptance writes no receipt | As above. An accepted pointer is never queued again unless the owner [attests its absence](sessions.md#a-queued-message-stays-unread) |
| Codex tool inbox, Claude worker | `offered`, `parked`, `acked` | The worker. `received` and `reply` run `session_ack`, then write `acked`. `park` and dismiss write the row first, then run `session_ack`. `receive` parks an offer whose window ended | An `offered` row is offered again. A `parked` row whose receipt was lost is acknowledged again by the next `receive`. An `intent` or `submitted` row left by parent reception refuses `receive` with `session_delivery_unknown` |
| Grok Bot cloud | `offered`, `acked` | The parent chat, with `received` or `reply`: `session_ack`, then one attempt to write `acked`. The wake relay never acknowledges a peer message | An `offered` row is offered again, and its receipt can be repeated |

Every route receipts a quiet Hello or roster notice itself, with no chat turn: the
notice log is written first, then `session_ack`, then `acked`. On the cloud route the
relay or `cloud receive` does this. A repeat of an already `acked` notice or row only
repeats the idempotent `session_ack`.

The frozen `pilot-worker` route has no delivery row. Its attempts are server state.
Cloud `wake:` rows track webhook posts only; a wake is never a receipt.

A proved-unsent attempt removes its `intent` and stops reception; the next
owner-authorized `session start` or `recover` rereads the message once. Failure after
the `intent` with no such proof raises `session_delivery_unknown` and keeps the row.

`queued` means the chat's proof is recorded and only the `session_ack` upload is left;
whichever pass runs next finishes it, and nothing is delivered again. On every
`intent` and `submitted` route, one unconfirmed message holds later normal messages
to that recipient, because `session_inbox` returns only the head. An urgent message
passes it. The sender sees the hold as `receipt_timeout`.

### Liveness and maintenance

Native `session_heartbeat` validates the exact owned session, live membership and
device before updating private health. New connection UUIDs create idempotent Hello
notices for same-project native peers; ordinary heartbeat retries create no notice.
The optional Hello reporting extension returns the recipient count and bounded roles
for that connection, and sends the newcomer one idempotent roster notification.
The optional reconnect dedupe skips a Hello to a peer already greeted within 30 minutes
of the sender's unbroken presence. Clients receipt both notice kinds themselves
([transport notices](sessions.md#transport-notices)).
`session_peers` derives availability from server time and private receipt/heartbeat
records; the [status card](sessions.md#status-card) maps each signal.

The optional owner-maintenance extension exposes `session_update_request`,
`session_update_status` and `session_update_report`; see
[update](update.md#explicit-owner-update) for their owner workflow and
[server](server.md#owner-maintenance-extension) for the separately authorized
deployment. Its inbox `maintenance` metadata is projected from private typed
records. A target is only bounded protocol/package versions and a content SHA-256,
never an executable source or authority. Notices are deduplicated per selected
quorum/target/recipient, do not require a model handler, and are excluded from
project-task delegation. Receipt, refreshed copy and active runtime self-report
are separate facts. Status is paginated and never treats a remote report as proof
of current liveness.

## Authority card

Four planes decide who may act. Add no fifth one.

| Plane | Source | Decides | Does not decide |
| --- | --- | --- | --- |
| User | Verified JWT subject: a GitHub identity (immutable provider ID), or an anonymous sign-in admitted by a single-use open invitation | Who is calling. Creating a quorum also needs an `instance_creators` grant | Anything inside a quorum without a membership. An anonymous user counts only while a live member of an open quorum |
| Membership | `memberships.role`: `owner` or `member` of one quorum | Owner-only operations: invitations, open access, `member_revoke`, `agent_retire`, `quorum_agents` | Which agent receives a message. It is not an agent role |
| Agent | One `agents` row: device, project reference, engine, session reference and a free role string. Owner, device and membership must all be live | Which session sends and receives, through `owned_agent` and `native_session` | What that session may do for a peer |
| Local owner policy | The receiving binding's private local state: delegation, member trust, standing grants | Whether this chat acts on a peer's request | Nothing on the server, and peers cannot see or supply it. Losing local state loses the grant, not the membership |

`agents.role` is the only role `resolve_role` sees. It matches role and project
exactly among agents whose agent, device and membership rows are unrevoked, and
anything but one match is `recipient_ambiguous_or_missing`. Live here means
unrevoked, not online, so a stale binding still counts. It returns an agent ID and
grants nothing; a role name alone cannot choose an arbitrary online agent.

The other role strings stay out of it:

- `memberships.role` is only owner or member.
- `memberships.assigned_role` and `assigned_project_ref` come from an invitation
  and only constrain `agent_join` (`role_assignment_mismatch`).
- A coordinator role is a local string the receiving owner pins in delegation.

`session_task_authority` reports, for one message addressed to this session,
`current_member` when the sender's agent is still live in this quorum and project,
`sender_unavailable` when it is not, and `not_task` for a reply, a transport notice
or a maintenance notice. It grants nothing: `session task-check` also requires the
local grant, fresh for each task.

A device is an owner's opaque server ID, one per native session, not a machine
identity. Delegation is the receiving owner's private local policy, never a
peer-supplied privilege claim. Peer text and priority change no check.

Project knowledge has its own grants: membership does not imply access. It uses
separate authenticated `knowledge_admin`, `knowledge_write`,
`knowledge_search`, `knowledge_read`, `knowledge_index`, `knowledge_source_write`,
`knowledge_source_read` and `knowledge_catalog` RPCs with exact native
agent/session binding. See [knowledge](knowledge.md) for bounded payloads, owner
assertions, individual space permissions, revision CAS and optional derived vectors.
No peer envelope, task grant or shared project label grants access. These RPCs
require the separately deployed project-knowledge extension; source/catalog/wiki
features also require its additive retained-source migration. CLI `source-write`
and `source-read` map explicitly to underscore RPC names. Source metadata conveys
no authority. Source/page capture uses the same finite exact native grant;
source audit and source withdrawal/purge require explicit owner action. They
never fetch references, generate embeddings or verify pages automatically.

## Frozen historical pilot

The first design ran one headless handler per message. Every RPC below stays
deployed and callable, so the opt-in pilot keeps working. Frozen means no new
feature, caller, state or client depends on them. Do not re-enable them to make
the transports look uniform.

| Frozen part | What still uses it |
| --- | --- |
| `inbox_claim`, `attempt_update`, `delivery_complete`, `attempts`, leases, and every `deliveries` state except `queued` and `answered` | `Worker` in `quorum_worker.py`, driven only by the explicit [`pilot-worker`](pilot.md) and [`listen`](listener.md) commands |
| ACP: `quorum_acp.py` | `ClaudePilotRunner` for those same commands. The default `worker` command and Codex runner raise `live_agents_disabled`, and `assets/engines.lock.json` accepts no engine |
| `work_register`, `work_admit`, `work_transition`, `work_items`, `capacity_reservations` | `quorum.py work` still reaches `work_register`. No client calls the other two |

One frozen check reaches the live path: `session_reply` refuses with
`active_attempt_requires_reconciliation` while a handler attempt holds the original
delivery. A plain native send keeps the `requires_handler` default of true, and
replies and server notices set it false; no native RPC reads that column.

Historical semantics, kept for pilot recovery only:

- `inbox_claim` atomically creates an attempt. The server holds one running handler
  per device and at most two per quorum.
- Sixty-second leases can be renewed by heartbeats. Lost leases become `unknown`,
  not automatically queued.
- `delivery_complete` writes the reply and completion atomically, with generation
  checks and request deduplication. Follow-ups to one handler are serialized.
- `Worker` runs one synchronous step. Long-turn heartbeats, budget enforcement and
  cancellation supervision have not passed real-adapter acceptance.
- Work admission reserves unfinished capacity before implementation. Review must
  cover the current SHA and come from an independent member. Merge holds capacity
  through QA; failed QA returns to the same owner. QA evidence is a human
  attestation, not a verified deployment check. No merge or tracker write is
  performed here.
