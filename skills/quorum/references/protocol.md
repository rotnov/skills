# Coordination contract

The configured HTTPS origin serves Supabase Auth and PostgREST. Requests are
restricted to named RPCs at `/rest/v1/rpc/<operation>`; arbitrary paths, plaintext
HTTP and redirects carrying credentials are rejected. The publishable project key
is not a server credential. Human access/refresh tokens stay in the owner process.

QuorumApi prefixes payload fields with `p_`. Mutations require `p_request_id`.
The backend derives human identity from verified JWT subject, checks live
membership and device ownership, and uses immutable GitHub provider IDs for invites.

Core RPCs: `quorum_create`, `invite_create`, `invite_accept`, `device_register`,
`agent_join`, `agent_status`, `agent_leave`, `resolve_role`, `quorum_lookup`,
`message_send`, `conversation_read`, `context_read`, `inbox_claim`,
`attempt_update`, `delivery_complete`, `work_register`, `work_admit`,
`work_transition`, and `member_revoke`. Deploying this contract requires the
separately reviewed backend; it is not supplied by a generic Supabase database.

`message_send` takes explicit quorum, sender, recipient and conversation IDs,
optional `reply_to`, and a UTF-8 body up to 16 KiB. It also takes an optional
`priority` of `normal` (default) or `urgent`, as does `session_reply`; any other
value is rejected. Priority is metadata: it orders durable selection and selects
the client's delivery route, and never affects identity, membership, device,
ownership or any permission check. It stays outside the deduplication
fingerprint, so a replayed request ID must repeat the stored priority.
`resolve_role` requires exactly one live match. A role name alone cannot grant authority or choose an arbitrary
online agent. Conversations are ordered; follow-ups to one handler are serialized.

`inbox_claim` atomically creates an attempt. The server holds one running handler
per device and at most two per quorum in this pilot. Sixty-second leases can be
renewed by heartbeats. Lost leases become `unknown`, not automatically queued.
`delivery_complete` writes the reply and completion atomically, with generation
checks and request deduplication. Automatic reply receipts do not launch handlers.

The local Worker class is currently a synchronous synthetic-test harness, not a
live long-running supervisor. Its injected runner must produce normalized
`started` and successful `completed` events; actual acpx event compatibility,
heartbeats during long turns, budget enforcement and cancellation supervision
must pass real-adapter acceptance before live execution is enabled.

Work admission reserves unfinished capacity before implementation. Review must
cover the current SHA and come from an independent member. Merge holds capacity
through QA; failed QA returns to the same owner. QA evidence is a human attestation,
not a verified deployment check. No merge or tracker write is performed here.

Public errors are stable codes; API failure bodies and credentials are not echoed.
There is no exactly-once guarantee for arbitrary external actions, nor a claim
that database fencing can stop a disconnected operating-system process.
