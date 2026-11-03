# Quorum worker-owned foreground inbox

## Scope and authority

This is the first independently useful milestone of [#50](https://github.com/rotnov/skills/issues/50), based on `dca8089a7c23a82007bab138b3908c0d099b332b`. The owner authorized implementation and opted out of further design questions. Deliver actual Codex native-worker reception through its own tool result, with create/invitation guidance, explicit receipts and bounded foreground waits. Keep #50 open until Claude and full lifecycle acceptance pass.

The owner's #50 request supersedes the older parent-only rule in `references/service-agent.md` and the local quorum evolution overlay for this new, proven route. The native service worker owns create, accept, connect, receive, receipt and reply using its actual identity. Existing parent transports retain their own identity and behavior as explicitly identified fallbacks. Never reuse or rewrite the parent's binding for the worker. A worker receipt means worker consumption, never parent delivery.

## Evidence and approach

A real Codex child had a distinct exported UUID; ordinary external queue delivery failed with `-32600` for an unloaded spawned subagent. That establishes failure of the tested external queue route only. A subsequent provider-only foreground-tool probe passed: child `01a167b0-8caa-7533-ad39-321b0d35f98a` consumed its separate tool result; the unique raw marker occurred four times in the child transcript and zero times in the parent transcript. Its provider rollout first line identifies `source.subagent.thread_spawn.parent_thread_id` as `01a15a2b-12f9-73c3-9310-c6b3c7774415`. Evidence is retained outside the installed skill in `/tmp/quorum-native-inbox-20261023/provider-probe/verification.json`.

Choose a foreground `session receive` call: subscribe to Realtime, read the durable owned inbox, commit an offer, return one envelope through the worker's tool stdout. Reuse current auth, registration, RPCs, release checks and durable ordering. No new dependency, DDL or protocol/version change. Direct provider routing remains unproved; a background relay cannot itself keep a completed native worker alive. The foreground probe used actual child CLI version `0.163.0-alpha.10.2`; the unrelated PATH CLI `0.162.1` queue failure does not govern foreground tool results. The probe does not establish live Quorum delivery, Claude support, parent-idle survival, or perpetual service; those acceptance checks remain pending.

## Identity and interface contract

- `accept-id … --transport tool-inbox --parent-thread-id <UUID>` and `session connect … --transport tool-inbox --parent-thread-id <UUID>` opt into this route. Omission preserves existing behavior; explicit `--transport native` denotes the existing transport. Tool mode conflicts with membership-only/cloud-client flags. Parent UUID is a nonsecret expected relationship, not authority.
- The actual engine remains `codex`. `current_session()` must match the child's own unmodified `CODEX_THREAD_ID`, differ from the parent, and have no competing client exports. Validate the provider-owned rollout first line before consuming an invitation or registering: `type == session_meta`, `payload.id == current_session`, and `payload.source.subagent.thread_spawn.parent_thread_id == expected_parent`. Never accept a user worker ID, arbitrary caller-supplied transcript path, or copied exports as proof.
- Resolve only an exact UUID-suffixed rollout under the current local Codex home's `sessions` directory. Require exactly one regular, current-user-owned, non-symlink candidate; reject unsafe path components and writable-by-other-user files. Read only the bounded first line (256 KiB maximum), never transcript bodies. Missing, ambiguous, malformed or unsupported metadata fails closed. This corroborates provider provenance under the local-user trust model; it is not cryptographic protection against that same OS user forging files or environment.
- Persist transport and verified `worker_binding = {provider: codex, session_ref, parent_thread_id}` beside `session-agent`, retaining existing registration fields/device key. Revalidate provenance for bound tool-mode actions; parent can inspect status or request stop by exact SID, but cannot receive/send/receipt/reply as child. Claude/Grok tool-inbox requests return fixed unsupported diagnostics without accepting the invitation. No impersonation or model launch fallback.
- `session receive --wait-seconds N` accepts integer `0..60`, default `30`. Zero does one owned read without a Realtime wait. Nonzero subscribes before the first read and waits on scoped hints/reconnects/safety fallback. Return one offered envelope, `timeout`, or `stopped`; never call queue, steer, socket/pipe delivery, or background spawn.

## Delivery, concurrency and release

Use the existing per-session ledger and exclusive `supervisor.lock` plus `listener.lock`; serialize lifecycle transitions with `listener-control.lock` without holding that control lock through the wait. A second consumer fails closed. Publish exact run/release markers so status/stop/release checks cannot target a different generation.

Commit `offered` before stdout with message UUID, agent/session/quorum IDs, bounded message fields and terminal markers. Fresh owned `session_inbox` validation and release checks precede every cached offer. Repeat the same unresolved offer after lost output or lost ACK response, even if the server head changed or became empty. Otherwise select the server's urgent-first durable order. Output is not a receipt. No peer body enters diagnostics or parent summaries.

Same-worker `received` requires a matching offer; idempotent `session_ack` then compare-and-set `offered → acked` preserves metadata. Reply requires a matching offer, rejects notifications/replies, calls idempotent `session_reply`, then ACK. Identical lost-response replay is permitted; changed reply text/priority remains rejected. Do not convert old queue `intent` into an offer or auto-repeat project work after recovery. Preserve release, membership, device, project/coordinator grant and reply-loop checks.

Registration creates a new child ledger and device identity using existing APIs. Existing bindings default to native transport; reject implicit transport changes or ownership transfer. The parent ledger remains untouched.

## Foreground lifecycle and availability

Connect initializes tool reception enabled. `session stop` disables future receive calls and requests stop for the exact active run. `session start` for tool mode only re-arms the same verified binding; it spawns nothing and reports offline until receive holds the lock. This prevents repeated bounded calls from silently undoing stop. Normal timeout releases locks and leaves enabled true; lost process status reports the lost run. Cancellation closes the waiter and never acknowledges an offer.

Persist a tool-mode presence epoch across calls. Keep the connection UUID for contiguous calls and retry ambiguous heartbeat responses with that same UUID. Rotate once after an acknowledged heartbeat gap of at least 60 seconds, or an explicit stop/start. Persist intent before heartbeat; track wall-clock freshness, detect clock reversal and fail conservatively. Existing background presence behavior remains unchanged. Never heartbeat between tool calls with an unattended process.

The wait budget limits idle waiting, not strict elapsed runtime. Existing HTTPS socket timeouts are 20 seconds per operation; connect/join and auth/heartbeat checks may extend elapsed time. Stop is checked between blocking calls; the tool host may terminate a call. Document this practical boundary, without promising a strict 60-second wall timeout.

The native worker has a finite owner-scoped brief. Reuse/resume that actual same worker when the provider supports it. Parent completion can suspend a child; retain durable messages and report offline rather than claim continuous service. A new worker UUID cannot inherit the old inbox or receipts. Server health is recent activity and may lag stop by 60 seconds; local lock-backed status distinguishes active receive from an offer awaiting handling.

## Create, invitation and quiet fallback

Select/reuse the native service worker before setup. For a proven Codex worker, pass the bounded owner authorization, public invitation and expected parent UUID; the worker reads the installed skill, uses already authorized machine auth, performs refresh once, creates/accepts as authorized, connects itself in tool mode and receives for its finite brief. Automatic invitation prompts must choose this route when proven, preserve coordinator grants, and avoid `session start` as a background-listener instruction for tool mode.

If a provider lacks verified worker identity/capacity, report one quiet capability limitation. Do not consume a tool-mode invitation on failed preflight. Existing parent native/cloud routes remain truthful fallbacks when within the owner's scope; explicitly report parent reception and never label it worker inbox success. Do not transfer secrets or change auth/provider configuration to force support. Only setup outcome, material result, actionable blocker or owner decision is escalated; routine bodies/Hello/receipts stay with the worker.

## Rulings and cost if wrong

| Ruling | Cost if wrong / required containment |
| --- | --- |
| Provider rollout metadata corroborates distinct Codex child identity | Layout changes cause false unsupported results; fail closed and update the adapter, never guess another session. |
| A tool result is isolated from parent message history | Parent UI may aggregate output differently; repeat actual transcript/UI inspection with the live route before claiming privacy. |
| Foreground receive is useful without a daemon | Reception pauses when worker ends; report bounded coverage and keep #50 open for broader lifecycle work. |
| Existing server RPCs suffice | No immediate offline/reassignment operation exists; accept stale health window and preserved old inbox, never promise migration. |
| Presence epoch can survive bounded calls | Bad restoration creates Hello spam or misleading health; test retries, time gaps and stop/start with deterministic clocks. |
| Safe Codex-only support is a valid first milestone | Claude users use explicit supported parent fallback; installability is still required for both clients. |

## Acceptance

Pass source tests, independent task review, then bounded owner-authorized live Codex Quorum reception while parent busy, empty timeout, offered-result recovery and same-worker receipt/reply with independent conversation evidence. Inspect parent transcript/UI for raw-message leakage. Verify stop/re-arm and release/grant failures without broadening authority. Complete final current-head review and every pinned repository gate before PR/merge. Retain exact evidence levels and unresolved Claude/parent-idle/long-lived lifecycle cases in the PR; do not close #50.
