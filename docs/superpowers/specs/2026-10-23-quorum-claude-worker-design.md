# Quorum Claude native-worker foreground inbox

## Scope and dependency

Publication paths: this design at `docs/superpowers/specs/2026-10-23-quorum-claude-worker-design.md`, paired plan at `docs/superpowers/plans/2026-10-23-quorum-claude-worker.md`. Promote reviewed private documents by exact copy.

Implement approved `task2-brief.md` and its approved review corrections as one integrated Task 2. Add honest Claude logical actors with trusted parent attestation and scoped same-user capability delegation with an explicit permission lifetime. Reuse Task 1's tool-inbox delivery/receipt code and existing auth/create/registration APIs. Do not add a daemon, provider session, independent identity framework, DDL, hook, global agent definition or dependency.

Execution starts only on the controller-selected Task 1 merge/base after its final F1 descriptor-lifetime and F2 sticky-parent connect fixes are reviewed and integrated. Those fixes are still in progress at planning time; this document does not claim them complete. Do not port or replace their implementation. Controller records exact execution base SHA and reruns changed-head gates.

A real native background Claude child already passed local synthetic foreground receive/explicit ACK with actual returned handle and shared genuine SID. This is provider-route evidence only. New-source live logical registration, child create/join, peer receipt/reply and privacy acceptance remain required. Desktop parent-idle is separate; keep #50 open for remaining lifecycle evidence.

## Identity and trust

Preserve `current_session()` as the canonical actual exported provider SID. A validated Claude binding carries:

```text
provider=claude, binding_version=1
provider_session_id=<actual CLAUDE_CODE_SESSION_ID>
native_worker_handle=<exact actual parent-returned Task/Agent handle>
logical_actor_id=uuid5(UUID(provider_session_id), native_worker_handle)
session_ref=claude-worker:<logical_actor_id>
slot_id=<random UUID>, generation=1
attestation=owner-parent-native-receipt
authority_hash=<SHA256 canonical immutable binding/scope>
spawn_tool_use_id=<optional only when native interface exposes it>
```

The optional tool-use ID adds audit correlation, not authentication; missing is valid. No transcript lookup, caller transcript-path flag or invented provenance. An independently inspected owned-test transcript can correlate exact tool IDs offline during acceptance. Product runtime reads no provider transcript bodies.

`engine=claude`, backend session_ref is logical, provider SID stays genuine/shared. Ordinary parent ledger/device and Codex proof remain unchanged. Capability possession delegates only scoped operations to the local caller; it does not independently authenticate a native child. Same OS user/parent can deliberately read files and invoke the capability. Existing machine auth can bypass a local CLI restriction if used outside this route; this is no new server ACL or sandbox.

## Exact CLI grammar

Below `Q` is `python3 <installed-skill>/scripts/quorum.py --state <absolute-state>`. Options shown before a subcommand must occur there; create/accept flags belong to their own subcommands.

```text
Q session worker prepare --json-stdin
Q session worker attest <slot-uuid> --native-handle <handle> [--spawn-tool-use-id <id>]
Q session worker await-binding <slot-uuid> [--wait-seconds <1..60>]
Q session worker status <slot-uuid>
Q session worker stop <slot-uuid>

Q create <slug> --access {github,open} --json-stdin --worker-capability <generated-file>
Q accept-id <invite-id> --transport tool-inbox --worker-capability <generated-file>
Q session --worker-capability <generated-file> connect <slug> --role <role> --project-ref <ref> --transport tool-inbox [--accept-tasks-from <coordinator-role>] [--dispatch-mode {inline,native-subagent}]
Q session --worker-capability <generated-file> receive [--wait-seconds <0..60>]
Q session --worker-capability <generated-file> received <message-uuid>
Q session --worker-capability <generated-file> reply <message-uuid> --text-stdin [--priority {normal,urgent}]
Q session --worker-capability <generated-file> status
Q session --worker-capability <generated-file> stop
Q session --session-id claude-worker:<canonical-uuid> status
Q session --session-id claude-worker:<canonical-uuid> stop
```

The final two forms are metadata-only supervision by the exact owning real Claude SID; stop is terminal. They never authorize receipt/read/reply. Worker-capability forms derive actor from the file, need no actor selector, and reject `--session-id` even if equal. Keep existing provider UUID `--session-id` behavior unchanged. Typed parser accepts only canonical UUID or exact `claude-worker:<canonical UUID>`, never arbitrary text/path. Add a separate session `worker` action tree. No Claude tool `start`, `run`, `reconcile`, `send`, `thread`, `peers`, `delegate` or global legacy `accept/join` capability path in Task 2: reject explicitly; never silently ignore a capability.

Codex `--parent-thread-id` remains unchanged and conflicts with Claude capability. Worker mode conflicts with native/cloud/membership-only/anonymous routes. Require one unambiguous actual Claude export and no competing Codex/Grok identities. Preserve native Claude pipe behavior when no worker capability is selected; never resolve/submit parent pipe in worker mode. CLI does not spawn models/children: the owner-parent uses its actual native tool.

Create request identity comes from the scope slot; reject `--request-id` in worker create instead of accepting an override. Existing create JSON remains supported but only the approved `slug` and optional approved `role` fields are allowed for worker create; reject unknown fields and conflicting positional/JSON values. The outgoing `quorum_create` RPC carries only the approved slug plus the slot's stable request ID, matching the checked `public.quorum_create(p_slug, p_request_id)` signature; the approved role stays local scope and is applied at connect/agent registration (this supersedes the earlier technical decision to send slug plus role on the wire). Emit normal public create result without bearer or envelope. Parent normal create remains unchanged.

## Scope input, defaults and private storage

Prepare consumes UTF-8 JSON object at most 16 KiB, rejects duplicate/unknown keys and invalid types. Required scope keys, shown with the optional explicit service lifetime:

```json
{
  "mode": "create",
  "service": {"origin": "https://approved.example", "publishable_key_sha256": "<64 lowercase hex>"},
  "quorum_slug": "owner-approved-slug",
  "access": "github",
  "role": "service-worker",
  "project_ref": "approved-project",
  "cwd": "/absolute/approved/project",
  "lifetime": "until-owner-stop"
}
```

For `mode=accept`, replace `access` with required exact public `invite_id`; after accepted membership, pin returned quorum ID before connect. Keep approved slug too, and reject its lookup resolving to another quorum. Optional keys: `coordinator_role` (default null), `dispatch_mode` (default inline, native-subagent requires coordinator), `lifetime` (default `bounded`; the example explicitly selects `until-owner-stop` for existing ongoing owner service authority), `ttl_seconds` (integer default 900, range 1..3600, accepted only with `bounded`), `operations` (default selected create/accept plus connect, receive, received, reply, status, stop; explicit subset only, never adds commands). Require connect/receive/received/reply for the standard service-worker route; narrowed scopes may intentionally fail those operations. No new project authority follows from prepare/attest. Actual authorization remains intersection of capability scope, current binding, server membership/device/release and project/coordinator grant.

`bounded` measures `permission_expires_at` from prepare-time, including bootstrap; it is the safe default for finite QA. `until-owner-stop` must be explicitly selected under existing ongoing owner authorization and has `permission_expires_at=null`; reject any supplied `ttl_seconds` in that mode. Hash and report the exact lifetime and deadline. Neither mode changes actual native-provider lifetime, finite receive bounds, operation/target limits, stop, membership/release or project grants. Standard ongoing service guidance selects `until-owner-stop` once, without periodic owner reconfirmation; finite QA guidance selects `bounded`.

Pin canonical `--state` root, actual SID and prepare-time cwd relation. Compare caller cwd against approved canonical cwd for all worker actions. Reject conflicting origin/publishable-key flags; do not allow anonymous or service/profile switch. Before any profile/auth/API, validate the capability and operation arguments; then compare resolved nonsecret public service config to pinned origin/key digest before token access or API. Parent prepare itself resolves no profile/auth and makes no network call.

Paths are generated, not caller-chosen runtime layouts:

```text
<state>/claude-workers/<slot-uuid>/ledger.sqlite  # existing LocalLedger database
<state>/claude-workers/<slot-uuid>/capability.json
<state>/sessions/claude-worker:<actor-uuid>/...  # existing session ledger layout
```

Slot root 0700 and generated bearer file 0600; verify path/file owner, no-follow traversal, regular type, strict permissions, bounded JSON (16 KiB) and stable inode before use. Do not chmod existing untrusted/provider paths. Unsupported platform owner proof fails closed, consistent with Task 1. Canonical capability path must equal the generated slot path under the current state root; arbitrary external capability paths are denied.

Keep one root-ledger setting `claude-worker-actor:<session_ref>` mapping actor to slot and owning provider SID. Populate it with attestation under root/slot locks in a fixed root-before-slot lock order. A different slot cannot claim the same actor, including after terminal stop; this milestone has no same-handle regrant. Logical status/stop selectors resolve only this verified index and exact owning real SID, never scan arbitrary files. Repeated attest on the same slot remains idempotent.

Reuse `LocalLedger` settings/transactions, file locks, existing random-bearer/hash patterns and `open_private`/`protect` primitives, with a small descriptor-safe atomic replacement helper where needed. The existing `LocalLedger` MCP token contract requires a finite TTL at most 3600; do not widen it, use a distant expiry sentinel or silently renew tokens. Keep one adapter-local record for both lifetimes: `secrets.token_urlsafe(32)` bearer, SHA256 hash in private ledger state, explicit `permission_expires_at` (timestamp for bounded, null for until-owner-stop), immutable scope/generation and revocation checks. Leave MCP capability semantics unchanged. Raw bearer only in capability file and memory, never argv/output/error/event records. No new general capability framework or schema migration.

Storage lifetime premise (controller-amended during Task 2 review; supersedes the earlier lifetime-long sidecar and ancestor-rename guarantee, which was not verified): the owner-controlled private actor directory and its ancestor mappings must stay in place while a SQLite connection is in use; stop or close active commands before moving or replacing them. Before any writable actor ledger, profile, auth or API work, reject unsafe supplied, root and actor paths, links, wrong owners, loose modes and unsafe existing files (including recovery sidecars) without chmod. Verify that the main database file SQLite actually opened is the verified inode; this descriptor-set observation is a controlled-open-phase inference, not a SQLite-owned descriptor API, and unavailable or ambiguous proof fails closed. Keep ordinary disk rollback journaling and crash recovery; a private, owned, single-link regular recovery journal is legitimate and is left for SQLite. Helper locks and logs open through the held verified directory. SQLite's own journal I/O is pathname-based: a concurrent same-OS-user process that substitutes ancestors during that I/O violates the premise, and the adapter claims no sidecar isolation or same-user sandbox. Identity, scope, lifetime, stop and authority rules above are unchanged.

Final-wave amendment (controller call, not an owner selection): the same stable-directory premise covers all Claude worker SQLite state (state root, actor index, slot and actor ledgers) while used. Every Claude route opens those ledgers and consumes their lock files through the same verified no-follow/owner/mode/single-link/sidecar checks before mutable construction, profile, auth or API work, and refuses unsafe files unchanged; capability status and stop construct no root ledger, and legacy callers keep `LocalLedger`. Receive rechecks authority after waiter close, final status persistence and lock release, immediately before disclosure. Narrow recovery-before-record-validation exception: when a validated private hot rollback journal makes an existing slot database unreadable, an owner lifecycle command (`session worker await-binding`, `attest` or `stop`) or a non-status capability command, after caller export form, slot/capability syntax and static path/file/sidecar checks, takes the root-before-slot locks, opens only that existing verified database (main-file proof) and lets SQLite restore its last committed state; no schema, setting, grant, handle or generation is written, and SID, state, capability, scope, target and expiry are then fully revalidated before any authority-bearing action. A structurally valid request later denied by its recovered record may already have caused that local rollback. Read-only status stays read-only and reports `recovery_needed` naming the affected ledger and its actual writable route: slot (owner `await-binding`, `attest` or `stop`, or a non-status capability command), actor (the worker's capability `receive`, `connect`, `received` or `reply`; owner slot commands never open it) or state (the worker's writable capability commands). Residual correction (controller procedural exception, unchanged deny-first contract): the generated capability path, bounded strict capability JSON and structure, forbidden modes, flags and request overrides, and the create input (stdin read once) are all checked before any recovery. Recovery discards only uncommitted local SQLite changes; completed remote operations and earlier committed local steps remain.

## Lifecycle and create/registration

1. Prepare creates pending slot with stable random create request identity if mode=create. Return only public slot, `lifetime`, `pending_expires_at`, `permission_expires_at` and bootstrap command. Pending expires at prepare-time plus 120 wall-clock seconds, or the earlier bounded permission deadline; this deadline never extends. Pending has no server/membership/receive rights.
2. Parent starts exactly one actual native background general-purpose child. Child awaits slot in its own foreground tool (default 60, allowed 1..60 idle seconds). Parent attests exact handle from native receipt. Under a per-slot lock, same-SID one-time attest binds actor/capability; identical retry is stable. Changed handle/scope and terminal state deny.
3. Await returns active binding and generated capability-file reference only. Timeout terminally tombstones pending slot under the same lock; late attest cannot revive it. Parent stop or applicable pending/permission expiry is likewise terminal. If attest wins before timeout under lock, await may return active; if timeout wins it cannot. Activation retains the immutable lifetime/permission deadline and never resets it. Pending expiry is enforced only while pending; after atomic activation `pending_expires_at` is audit metadata and never expires active permission. Expired bounded permission denies every action; until-owner-stop has no elapsed-time permission expiry.
4. Child create calls existing `quorum_create` with persisted stable request ID. Persist intent before dispatch and returned quorum ID once. For open access, retry existing `quorum_set_access` with stable target-specific identity; do not register until approved access is known. Lost response retries exact same request/scope; uncertain unrecoverable outcome remains explicitly ambiguous, never a different create. Child accept pins returned membership/quorum ID once. Connect resolves approved slug, verifies exact pinned ID before register/device side effects, and pins returned agent ID once.
5. Connect uses logical actor ledger/device key and existing tool reception enabled state. A narrow optional `expected_quorum_id` check on existing `register_agent` protects lookup before device/agent side effects; legacy default stays unchanged. Worker owns create/accept/connect/receive/received/reply under capability. Scope adds no automatic inherited coordinator/project grant.
6. Shared receive/offer/receipt/reply code handles authoritative checks, locking, same-offer recovery, CAS and reply idempotence. Pass a narrow authorization guard around API operations and blocking boundaries rather than duplicate delivery. Recheck after blocking calls before body disclosure or next operation. An already-sent heartbeat/create/ACK/reply can complete remotely after stop; retain truthful ambiguous evidence and suppress subsequent operations. No rollback/instant remote-revocation claim.
7. Owner or capability stop terminally revokes this slot/capability and requests exact active-run stop. Preserve unresolved offers; no automatic ACK, no regrant/start reopening in Task 2. Bounded permission expiry invalidates authority even if remote presence lags. Until-owner-stop removes only that elapsed-time deadline; owner/capability stop remains terminal and every other guard remains required. Completion ends observed receive; status is last observed run/offer/offline, not provider liveness. Same still-running child's helper retry can recover an unresolved offer within active scope and actual provider lifetime. No automatic token renewal, child restart, replacement or regrant is part of service permission. New native handle requires new slot/actor and cannot inherit inbox/grants. Deterministic same-handle actor stays the same; later regrant is out of scope.

Public metadata/status never contains message body/bearer. Generated receipt/reply commands contain generated file reference, no token. Child final reports material outcomes/IDs/hashes only. Host private multiplex stream can contain child tool rows: no same-user/host secrecy promise. Live parent transcript/UI privacy acceptance is required separately.

## Source seams and acceptance

New narrow module `skills/quorum/scripts/quorum_claude_worker.py`; preserve Codex `prove_worker`/`require_worker` shape. Integrate typed selectors/parser/preflight with `quorum_session.py`, `quorum_live.py`, `quorum_invitations.py` and create branch `quorum.py`. Reuse `quorum_native_inbox.py` command/notice/delivery through guarded caller context; keep `quorum_delegation.matches` and worker holder behavior. All runtime dependencies remain inside the independently copy-installed skill. Update conditional references/prompts/evals, not the routing description into a feature inventory.

Source acceptance: isolated named TDD cases, all repository gates, fresh independent task review, unchanged Codex/native Claude/Grok/cloud behavior. Runtime acceptance: actual native child create and independent join, new-source namespaced registration, unknown-to-parent peer nonce in own foreground result, explicit receipt/reply with independent API/conversation evidence, matching real SID/actual handle/logical actor and unchanged parent ledger. Verify scope failures/stop/recovery and native parent transcript/UI leakage separately from private host stream. Synthetic provider proof does not substitute. Until-owner-stop is a local owner-permission contract, not evidence that the provider preserves a running child. Parent-idle, continuous reception and child resume remain separate/unproven.
