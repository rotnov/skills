# Quorum Worker Inbox Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Deliver the first #50 milestone: a genuine distinct Codex service worker owns bounded Quorum reception through its own tool result.

**Architecture:** Add explicit tool-inbox transport to existing session registration and invitation preflight. A foreground receive loop reuses owned RPCs, Realtime hints, release checks and durable offered/acked receipts, with provider provenance and per-session locks. Existing parent transports remain compatible.

**Tech Stack:** Python 3.12, uv, argparse, SQLite, existing websockets dependency, unittest, existing Supabase RPCs.

**Spec:** `docs/superpowers/specs/2026-10-23-quorum-worker-inbox-design.md`

## Global Constraints

- Baseline: 511 tests passed, as reported by the coordinator; rerun after implementation. Base `dca8089a7c23a82007bab138b3908c0d099b332b`; branch `codex/quorum-worker-inbox`; all changes through PR.
- No new dependency, DDL or protocol/version change. Skill remains independently copy-installable for Codex and Claude Code.
- Real exported child UUID and provider first-line provenance are mandatory; no parent export spoofing, arbitrary worker ID or transcript path override.
- `--transport tool-inbox` is Codex-only for this milestone; `native`/omitted preserves existing routes. Wait seconds integer `0..60`, default `30`; network operation timeouts remain separate.
- No model CLI, background receiver, queue/steer, provider configuration change or secret transfer for tool mode.
- Worker owns receipts; a receipt never means parent delivery. New child identity never takes over parent/previous-worker state.
- Owner authorized execution without further design questions; task implementer is a fresh Claude Opus 5.5 medium worker. Execute one integrated code/tests/docs task, then independent task review, scoped live tests and final review/gates. Keep #50 open.

## Review Focus

- Provider metadata missing, ambiguous, shared with parent or forged through a path override: fail before invitation/RPC and never leak transcript content.
- Stop between foreground calls and concurrent consumers: later receive remains disabled; exact-generation control cannot stop a replacement run accidentally.
- Lost stdout or lost ACK response followed by a new urgent head: repeat the original offer without duplicate ACK or project execution.
- Presence restoration after ambiguous heartbeat, clock reversal or 60-second gap: stable retry epoch, bounded Hello and truthful local status.
- Unsupported Claude/Grok requests and stale release/revoked membership: no silent parent fallback, cached-body disclosure, invitation consumption or background launch.

---

### Task 1: Implement the bounded native-worker inbox end to end

**Files:**

- Create `skills/quorum/scripts/quorum_native_inbox.py`: provenance, offered-delivery helpers and foreground receive orchestration.
- Modify `skills/quorum/scripts/quorum_session.py`: mode-aware parser/preflight/connect/dispatch and start/stop/receipt integration.
- Modify `skills/quorum/scripts/quorum_live.py`: accept-id parser flags only.
- Modify `skills/quorum/scripts/quorum_invitations.py`: pre-accept identity check and generated create/join/invitation routing text.
- Modify `skills/quorum/scripts/quorum_presence.py`: opt-in persistent epoch for tool calls; preserve default listener behavior.
- Modify `skills/quorum/scripts/quorum.py`: safe fixed diagnostics for new failures.
- Modify `skills/quorum/scripts/quorum_listener.py` only if shared status fields need a small backwards-compatible extension; do not rewrite its background loop.
- Create `skills/quorum/references/native-inbox.md`: route-specific identity, wait/receipt and lifecycle contract.
- Modify `skills/quorum/SKILL.md`, `references/service-agent.md`, `references/sessions.md`, `references/join.md`, `references/invite.md`, `references/invitations.md`: conditional routing and fallback; remove contradictory unconditional parent-only claims for proven tool mode.
- Create `tests/test_quorum_native_inbox.py`; extend `tests/test_quorum_session.py`, `tests/test_quorum_presence.py`, `tests/test_quorum_invite_id.py`, `tests/test_quorum_invitations.py` as needed. Add route eval cases in `skills/quorum/evals/evals.json` if its existing prompt contracts cover this path.

**Interfaces:**

- `quorum_native_inbox.prove_worker(parent_thread_id: str) -> dict`: returns exactly `{provider: 'codex', session_ref: <actual UUID>, parent_thread_id: <verified UUID>}`. Discovers provider rollout under current Codex home; no CLI path argument. Uses `current_session/current_engine`, bounded first-line JSON, ownership/path checks and exactly-one candidate. Internal path/clock seams may be injectable for unit tests, never public bypasses.
- `quorum_native_inbox.require_worker(record: dict) -> dict`: re-proves against persisted expected parent and validates all worker-binding fields. Typed `NativeInboxError(code: str)` exposes only an allowlisted stable code, not path/transcript/exception text.
- `quorum_native_inbox.receive(api, auth_ledger, ledger, record: dict, wait_seconds: int) -> dict`: owns consumer lifecycle/locks, same-offer recovery, release/inbox checks and waiter cleanup. Envelope fields: `type='quorum.peer_message'`, `state='offered'`, `message_id`, `message`, `receipt_command`, `reply_command`, `priority`; commands target this worker SID. Empty result: `{state: 'timeout', message: None}`; disabled result: `{state: 'stopped', message: None}`. Include `run_id` and `session_ref` diagnostics without raw transcript material.
- `quorum_native_inbox.confirm(api, ledger, record: dict, message_id: str) -> dict`: validates owned offer, fresh service binding/release, idempotent ACK and CAS; returns `received=true`, `state='acked'`, `message_id`.
- `quorum_native_inbox.reply(api, ledger, record: dict, message_id: str, body: str, priority: str) -> dict`: validates offer and terminal flag, existing size/priority rules, `session_reply`, then confirm; returns normal reply IDs. Shared private helpers are allowed; do not couple native identity to cloud parent/webhook code.
- `Presence(..., persist_epoch: bool = False)` adds opt-in restoration/persistence of `presence-epoch`; use `True` only for tool mode under consumer lock. Existing tests expecting fresh background-start epoch still pass.
- Ledger: `session-agent.transport` (default legacy `native`), `.worker_binding`, `tool-inbox-control={enabled: bool}`, `presence-epoch` with connection UUID/last acknowledged wall time/pending epoch intent, and `delivery:<UUID>` with binding, message and `offered|acked`. Existing `listener`/`listener-release` generation records remain authoritative for active runs.
- Stable codes: `native_worker_unavailable`, `native_worker_binding_denied`, `native_inbox_transport_conflict`, `native_inbox_receipt_not_offered`, `native_inbox_reply_loop_denied`, `worker_already_running`. Map provider/missing metadata diagnostics safely without printing content; preserve existing release/service error codes.

- [ ] **Step 1: Pin CLI and provenance failures with focused tests.** Add tests named `test_tool_mode_requires_distinct_provider_spawn_metadata`, `test_wrong_parent_and_parent_caller_are_denied`, `test_rollout_missing_ambiguous_oversized_or_unsafe_fails_closed`, `test_claude_grok_and_mixed_exports_are_unsupported`, and `test_accept_preflight_failure_makes_no_service_call`. Assert exact first-line fields, 256 KiB cap, no body reads, no path override, zero invitation/registration/spawn calls. Fixture paths and environment must be isolated, never the real user's sessions/auth.

- [ ] **Step 2: Run the new tests and confirm the expected missing-feature failures.** Run `uv run --frozen python -m unittest discover -s tests -p 'test_quorum_native_inbox.py' -v`; record failure caused by absent route/functions, not fixture import errors.

- [ ] **Step 3: Implement parser, provenance and registration integration.** Add `--transport {native,tool-inbox}` and `--parent-thread-id` to accept/connect; reject contradictory flags. Prove worker before auth/config/invitation side effects, including accept-id's current profile-resolution order. Persist a new worker binding without changing the registration schema or engine values. Re-prove bound actions; allow exact-SID status/stop supervision only. Keep all dependencies inside the skill. Add fixed diagnostics to the entrypoint.

- [ ] **Step 4: Add receive/receipt/reply tests.** `test_offer_committed_before_stdout_without_ack`; `test_lost_stdout_repeats_offer_before_new_urgent_head`; `test_lost_ack_response_replays_one_server_receipt`; `test_cached_offer_after_revocation_or_release_mismatch_is_not_disclosed`; `test_reply_requires_owned_offer_and_rejects_terminal_messages`; `test_same_reply_retry_is_idempotent_changed_reply_is_rejected`; `test_existing_queue_intent_remains_unknown`. Assert recipient/session/quorum IDs, metadata-preserving CAS, exact message/priority, no automatic project actions and no queue/steer/spawn calls. Reuse cloud test semantics, not its parent identity functions.

- [ ] **Step 5: Implement offered delivery and bounded waiting.** Use `ReleaseGuard`, authoritative `session_inbox`, `RealtimeWait.prepare/wait/pulse/close` and structured command arrays. One offer per call; subscribe before initial read for positive budgets. Validate cached data ownership and service membership before output. Re-read delegation from the current session record in envelope instructions. Check deadline/stop between blocking operations; distinguish idle budget from network time. Zero wait must not subscribe or sleep.

- [ ] **Step 6: Add lifecycle and epoch tests.** `test_two_consumers_are_excluded`; `test_subscribe_precedes_initial_read_and_reconnect_catches_up`; `test_zero_wait_and_empty_deadline_return_without_ack`; `test_stop_disables_next_receive_until_same_worker_start`; `test_tool_start_only_arms_no_spawn`; `test_exception_or_cancel_closes_waiter_and_preserves_offer`; `test_lost_run_detected_without_pid_guess`; `test_epoch_survives_calls_and_ambiguous_heartbeat`; `test_epoch_rotates_once_at_60_seconds_or_explicit_restart`; `test_clock_reversal_does_not_publish_false_freshness`. Use fake clocks/socket/API and real temporary FileLocks. Existing native/Claude/Grok queue behavior must remain covered by original tests.

- [ ] **Step 7: Implement lifecycle and persistent presence.** Acquire supervisor/listener locks for receive and use short control-lock transitions with exact run IDs. Connect enables reception; stop persists disabled and signals current run; start re-arms only verified same binding, clears the epoch and spawns nothing. Normal timeout keeps reception enabled but status offline. Persist heartbeat epoch intent before RPC and ACK time after success; retry the same unresolved epoch. No heartbeat process runs between calls. Leave stale server health explicitly bounded by its existing 60-second window.

- [ ] **Step 8: Update shared skill and invitation guidance.** Read the existing project/skill overlays as authoring inputs; #50 supersedes their parent-only instruction for proven tool mode. Route create and invitations through a reused native service worker before setup, actual child accept/connect and finite receive brief. Preserve owner/project coordinator grant and refresh-once rules. Document supported parent fallback, unsupported provider preflight, quiet reporting, worker receipt meaning, same-UUID recovery, network timeout limits and no perpetual service claim. Add prompt assertions that both tool and legacy/cloud paths remain coherent, without duplicating the detailed reference in SKILL.md.

- [ ] **Step 9: Run focused and full local regression.** Run each changed test module with `uv run --frozen python -m unittest discover -s tests -p '<filename>' -v`, then `uv run --frozen python -m unittest discover -s tests -v`. All must pass; tests must make no live provider/service/auth calls.

- [ ] **Step 10: Independent task review before live work.** Give a fresh reviewer the spec, patch and test evidence. Review provenance trust boundary, caller/parent distinction, consume-before-authorize mistakes, concurrent ACK/stop races, safe diagnostics, release guards and legacy fallback. Fix findings and rerun affected/full required checks. Reviewer approval is source evidence, not runtime acceptance.

- [ ] **Step 11: Run the already owner-authorized bounded live Codex check through the coordinator.** Reuse a genuine finite native worker and already authorized auth context. The coordinator exercises authorized create and one new open invitation, with actual child preflight/accept/connect and unchanged parent registration. Use only the new child state/binding and scoped test roles. Send a finite probe while parent busy; child receives, receipts and replies; independently read conversation IDs/participants. Exercise empty timeout, deliberately unreceipted offered result followed by same-worker receive/recovery, and stop/re-arm. Inspect actual child/parent transcripts/UI for raw-body leakage. Keep outputs private and report only sanitized IDs/counts/status. No server changes, new auth or substitute session; stop with a precise blocker if scope/capability is missing. Parent-idle continuation and Claude are separate outstanding acceptance, not inferred from busy tests.

- [ ] **Step 12: Final review and pinned gates, then hand off the PR.** Obtain fresh whole-patch review at final head; resolve findings. Run the complete required commands below before committing and again pre-commit before push/after any changed head. Commit only intended files, push the feature branch and use PR flow; never default-branch push or hook bypass. PR must say first Codex foreground milestone, link #50 without a closing keyword, record live/source evidence separately and list Claude/lifecycle limits. Merge only after all required checks and review pass under the coordinator's authorization.

```bash
uv lock --check
uv sync --frozen
uv run --frozen python scripts/validate_skills.py
uv run --frozen python -m unittest discover -s tests -v
./scripts/check-agent-skills-spec.sh
./scripts/check-skills-cli.sh
uv run --frozen pre-commit run --all-files
```

The official pinned validator, `skills@1.5.20` individual clean copy-installs for both Codex and Claude Code, and unchanged pinned pre-commit gates are mandatory. Do not call a model/live registration as part of unit tests. Do not claim #50 complete from this milestone.

## Self-review

All spec sections map to Task 1: identity/parser (steps 1–3), delivery/guards (4–5), lifecycle/presence (6–7), create/invitation/fallback (8), evidence/review/gates (9–12). Each Review Focus case has a named test. There is one integrated implementation task because identity, receipt and lifecycle invariants must ship together; review and live acceptance are gates on that task, not independent architecture projects.
