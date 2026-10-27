# Quorum Claude Native Worker Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** One genuine native Claude worker creates or accepts/connects its own logical Quorum actor and receives/receipts/replies through its own finite foreground tool calls.

**Architecture:** Keep the actual shared provider SID. Bind an actual parent-returned native handle to a pending private slot, derive `claude-worker:<uuid5(real SID,handle)>`, and delegate through a scoped file capability with explicit bounded or until-owner-stop permission lifetime. Integrate caller/create/invitation preflight with existing registration and shared offered/acked delivery; preserve Codex and legacy routes.

**Tech Stack:** Existing Python 3.12, argparse, SQLite LocalLedger, OS file locks/private files, current auth/RPC/Realtime code and unittest; no dependency, DDL or protocol change.

**Spec:** `docs/superpowers/specs/2026-10-23-quorum-claude-worker-design.md`; approved private `task2-brief.md` remains the authoring scope/trust input. Controller promotes reviewed documents by exact copy: design to that spec path, this plan to `docs/superpowers/plans/2026-10-23-quorum-claude-worker.md`. The paired tracked design is self-contained for implementation.

## Global Constraints

- Source execution waits for controller-selected Task 1 merge/base including reviewed final F1 descriptor-lifetime and F2 sticky-parent connect fixes. These are pending at plan time. Record exact base SHA; do not edit or take ownership of Task 1 fixes.
- Use one fresh Claude Opus 5.5 medium implementation job, normal approved tool/permission constraints, after that base. No replacement provider/model CLI is part of shipped adapter behavior. Controller owns job launch/review authority.
- Preserve real `CLAUDE_CODE_SESSION_ID`, namespaced logical session_ref, optional tool-use-ID audit only, owner-parent/same-user trust truth and existing parent ledger/device.
- CLI grammar, scope schema, paths, defaults and bounds are exactly the design's. Default lifetime bounded/TTL 900 (1..3600); explicit until-owner-stop rejects TTL and has null permission expiry. Pending deadline prepare+120 wall-clock seconds or earlier permission expiry, await default 60/1..60; activation never resets permission expiry; pending expiry applies only while pending and becomes audit metadata after activation. Terminal stop/applicable expiry/pending timeout; no regrant or Claude start/run/reconcile in Task 2.
- Worker operation authority is exact scope and explicit permission lifetime intersected with current binding/server release/membership/device/project grant. No coordinator grant copied from parent/old child.
- Reuse LocalLedger/file/token primitives and one delivery implementation. No provider transcript-body reads, arbitrary transcript path, parent pipe, hook/global definition, daemon or general authority framework.
- Every new runtime helper/reference belongs under independently copy-installed `skills/quorum/`. Codex and Claude clean individual install and pinned validators/pre-commit remain mandatory.
- Source job performs no real provider/Quorum/auth/network acceptance, commit/push/merge unless controller separately dispatches it. All repository changes ultimately through PR; no direct default-branch push and no hook bypass.

## Review Focus

- Optional native tool-use audit unavailable: valid actual-handle bootstrap succeeds without hidden transcript access (`test_attest_without_tool_use_id_succeeds_without_history_access`).
- Create completes remotely after local revoke/uncertain response: no new request identity, target substitution or follow-up side effect (`test_create_ambiguous_stop_retains_intent_and_blocks_followups`).
- Timeout and parent attest race: exactly one terminal/active outcome, no abandoned capability (`test_pending_timeout_attest_race_is_serialized`).
- Same-user parent deliberately holds capability: classify as scoped delegated caller, never independent child authentication (`test_same_user_capability_possession_is_delegated_authority`).
- Namespace, changed profile or lookup target misroutes parent/worker state: deny before token/device/registration and leave parent intact (`test_actor_selector_service_and_target_cannot_cross_bind`).

---

## File map

| File | Responsibility |
| --- | --- |
| Create `skills/quorum/scripts/quorum_claude_worker.py` | Typed scope/binding, pending/attest/await/stop, private capability, authorization guard and narrow scoped create wrapper. No alternate receive loop. |
| Modify `skills/quorum/scripts/quorum_session.py` | Typed namespace selector, worker management/capability grammar, preflight caller context, derived actor ledger and mode-specific dispatch. Preserve `current_session` and native routes. |
| Modify `skills/quorum/scripts/quorum.py` | Worker-aware create preflight before profile/auth, safe errors, optional expected quorum guard on existing registration. Preserve ordinary create. |
| Modify `skills/quorum/scripts/quorum_live.py` | Accept-id capability parser; route preflight without auth bypass. |
| Modify `skills/quorum/scripts/quorum_invitations.py` | Capability acceptance preflight and target pin; conditional public create/join prompts. |
| Modify `skills/quorum/scripts/quorum_native_inbox.py` | Provider binding context, generated scoped commands, authorization guard around shared delivery/receipt/reply boundaries. Preserve Codex proof interface. |
| Existing `quorum_state.py`, `quorum_os.py` | Consume existing settings/transactions/capability TTL/hash/revoke/lock/private primitives; change only if narrowly necessary with unchanged MCP behavior tests. No framework/schema migration. |
| Create `tests/test_quorum_claude_worker.py` | Scope/parser/private-path/lifecycle/preflight/create and delegated-authority tests. |
| Extend `tests/test_quorum_native_inbox.py`, `tests/test_quorum_session.py`, `tests/test_quorum_invite_id.py`, `tests/test_quorum_cloud_invite_prompt.py` | Shared delivery conformance, route regressions and prompt contracts. |
| Update `skills/quorum/references/native-inbox.md`, `references/{sessions,join,invite,invitations,service-agent}.md`, `SKILL.md`, `evals/evals.json` | Conditional independently installable workflow, exact CLI, fallback, trust and explicit permission/provider lifetimes. Existing create guidance updated at its actual reference location; no gratuitous new reference. |

## Task 2: Implement one end-to-end scoped Claude actor route

Identity, permission, create/accept and delivery are coupled: reviewing an independently shipped partial parser or partial capability would be misleading. Deliver as one integrated implementation task with the following bounded TDD cycles, then one fresh independent task review.

**Interfaces in `quorum_claude_worker.py`:**

- `prepare(state_root: Path, scope: dict) -> dict`: validates real Claude context and strict JSON schema; private pending slot; returns `{slot_id, state, lifetime, pending_expires_at, permission_expires_at, bootstrap_command}` only.
- `attest(state_root: Path, slot_id: str, native_handle: str, spawn_tool_use_id: str | None = None) -> dict`: same actual SID, one-time locked activate; returns public binding only. Validate bounded nonempty handle/audit strings (1..256 UTF-8 bytes, no control characters); preserve exact value, do not normalize/fabricate handles.
- `await_binding(state_root: Path, slot_id: str, wait_seconds: int = 60) -> dict`: integer 1..60 idle seconds; active returns public binding plus generated file reference; pending timeout terminally tombstones under slot lock.
- `slot_status(state_root: Path, slot_id: str) -> dict` and `stop_slot(state_root: Path, slot_id: str) -> dict`: owner actual-SID metadata/terminal revocation; no envelopes.
- `authorize(state_root: Path, capability_path: Path, operation: str, arguments: dict) -> dict`: read-only validation before worker ledger/profile/auth/API; returns exact binding+scope+generated command context, never token. Reject inactive/revoked/expired/mismatched scope/path/cwd/state/service/operation. Context permits same-user possessor, not independent native identity.
- `guard(context: dict) -> None`: recheck current binding/scope/generation/token validity at dispatch/blocking boundaries; fixed safe error on denial.
- `pin_target(context: dict, quorum_id: str, agent_id: str | None = None) -> None`: locked once-only fill of declared scope target/registration results; changed ID denied.
- `create_worker(args, context: dict, resolve_profile, api_factory) -> dict`: validate approved arguments before injected profile/auth/API functions; persist request intent; existing create/access APIs with stable identity and pinned target; retain ambiguity on failure. Injected functions are internal unit-test seams, not public bypasses.

**Shared integration interfaces:**

- `register_agent(..., device_key='device', expected_quorum_id: str | None = None)` retains all current behavior when None; compare looked-up ID before device/agent mutation otherwise.
- `quorum_native_inbox.receive/confirm/reply` accept optional keyword-only `authorize` callback defaulting to existing provider behavior. One shared guarded API wrapper can call it before each API invocation; revalidate after blocking returns before body disclosure or next operation. Keep existing positional signatures/tests compatible. Do not assert rollback of calls already sent.
- `quorum_session` resolves explicit capability before actor session-ledger creation; no change to real-provider `current_session/current_engine` output. Generated command context includes file reference only. Native Codex `prove_worker/require_worker` signature and binding shape remain unchanged.
- Safe error codes: reuse `native_worker_binding_denied` for capability/scope/context mismatch, `native_worker_unavailable` for unsupported private-path/provider capability, `native_inbox_transport_conflict` for incompatible mode, and add only `native_worker_slot_terminal` and `native_worker_create_ambiguous` if existing exact codes cannot represent those truthful outcomes. Never include path/body/token/exception text in error output.

- [ ] **Step 1: Record execution base and isolate scope.** Read repository instructions/project and Quorum overlays from the controller worktree, design/approved brief, current Task 1 review receipt and source. Save exact base SHA and clean/staged status in task report. Do not reset/stash another worker's changes. If controller has not selected final Task 1 base, stop source execution at that dependency; no patching it from this job.

- [ ] **Step 2: Write failing pending/identity/private-state tests.** Add `test_prepare_is_pending_with_no_profile_auth_or_api`, `test_attest_without_tool_use_id_succeeds_without_history_access`, `test_actual_handle_sid_scope_and_uuid5_are_pinned`, `test_changed_attestation_and_wrong_sid_fail`, `test_actor_index_prevents_cross_slot_same_handle_regrant`, `test_pending_timeout_attest_race_is_serialized`, `test_pending_stop_expiry_are_terminal`, `test_private_path_symlink_owner_swap_and_oversize_fail_without_chmod`, `test_capability_generation_operation_cwd_scope_and_expiry_deny`, `test_same_user_capability_possession_is_delegated_authority`. Add `test_permission_lifetime_schema_rejects_conflicting_ttl`, `test_service_permission_survives_qa_ttl_until_terminal_stop` and `test_activation_preserves_permission_deadline_and_retires_pending_deadline`. Assert default lifetime bounded/TTL 900 with 1..3600 bounds, explicit until-owner-stop/null permission expiry and rejected TTL, pending prepare+120 wall-clock seconds or earlier permission expiry, await 60/1..60, generation1; no transcript/model/API reads and no token in public result. Tests use local fixtures/fake clocks, not real provider paths.

- [ ] **Step 3: Run RED for those tests.** Run `uv run --frozen python -m unittest discover -s tests -p test_quorum_claude_worker.py -v`. Save output; failures must be missing feature/assertions, not broken imports or fixture credentials. If needed import lazily to make the expected missing function failure explicit.

- [ ] **Step 4: Implement the minimum slot/capability module.** Use existing private/lock/ledger/token helpers with one small descriptor-safe atomic token-file operation if needed. Bound JSON/read sizes, validate before constructors that chmod paths, lock CAS lifecycle, use canonical scope hash and exact uuid5. Maintain the root actor-to-slot/owner index with fixed root-before-slot lock order; reject different-slot claims for an existing actor, including terminal slots. Logical parent selectors resolve only that index and exact owner SID. Await active vs terminal race under same slot lock. No polling model/network. Keep one adapter-local hashed random bearer record using existing private ledger settings/transaction primitives; represent nullable permission expiry explicitly. Preserve the existing finite MCP TTL contract; do not use far-future expiry, silently renewed tokens or widen `LocalLedger` MCP semantics. Run Step 3 command, expect green; retain token only in private file/memory.

- [ ] **Step 5: Write failing CLI/caller/preflight tests.** Add `test_cli_worker_management_and_capability_grammar`, `test_logical_selector_is_typed_and_management_only`, `test_actor_selector_service_and_target_cannot_cross_bind`, `test_worker_preflight_precedes_profile_auth_and_invitation_use`, `test_claude_tool_connect_uses_logical_ledger_without_pipe_or_env_mutation`, `test_worker_capability_rejected_on_unsupported_legacy_commands`, `test_codex_native_claude_grok_and_cloud_routes_unchanged`. Assert namespace+capability conflict, mixed exports, anonymous/cloud/membership conflicts and missing cap cannot use logical actor. Parent status/stop is metadata/exact owner only. Snapshot parent ledger/device and provider env unchanged.

- [ ] **Step 6: Run RED then implement parser/dispatch/accept/connect.** Run the Claude test module and affected session/invite modules; save the expected feature failures. Add exact design grammar and split real provider caller from logical actor context. Scope all worker parsers; reject unsupported capability input explicitly. Accept pins membership/quorum ID; connect verifies lookup target before device/agent effects and skips parent pipe only for validated tool mode. Run same affected tests green. Do not broaden UUID parsing for other providers.

- [ ] **Step 7: Write failing scoped-create tests.** Add `test_worker_create_denies_pending_expired_revoked_and_wrong_scope_before_auth`, `test_worker_create_exact_slug_access_service_and_role_only`, `test_worker_create_then_connect_pins_target_once`, `test_create_lost_response_reuses_private_request_id`, `test_open_access_retry_preserves_target_and_blocks_early_registration`, `test_create_ambiguous_stop_retains_intent_and_blocks_followups`, `test_worker_create_rejects_request_override_and_unknown_json_fields`, `test_parent_create_behavior_is_unchanged`. Assert zero auth/API on local denial; same request ID on retry; no connect until known access; remote completion after revoke represented without rollback/new create.

- [ ] **Step 8: Run RED then integrate existing create branch.** Run Claude test module, record RED. Gate before `resolve_profile`/token/API, preserve parent branch, and use injected seams for fake APIs. Persist create intent before send and target once after result. Access call retains existing stable target-specific request key. Only approved role/slug input fields, exact requested access and no external request override; the create RPC sends the slug alone with the stable request ID per `public.quorum_create(p_slug, p_request_id)`, and the role applies at connect (supersedes the earlier slug-plus-role wire decision). Run test module green; no new server ACL/schema.

- [ ] **Step 9: Write failing shared-delivery/cancellation/privacy tests.** Parameterize applicable existing conformance fixtures with a Claude binding; add `test_claude_offer_precedes_stdout_and_explicit_receipt_only`, `test_claude_lost_output_ack_reply_reuses_owned_offer`, `test_claude_stop_or_expiry_during_blocked_api_suppresses_disclosure`, `test_service_stop_during_blocked_api_suppresses_disclosure`, `test_claude_inflight_side_effect_is_not_claimed_rolled_back`, `test_claude_stop_terminal_no_start_or_regrant`, `test_claude_grant_release_membership_and_terminal_reply_rules`, `test_worker_commands_and_public_summaries_never_emit_bearer_or_body`. Assert token/nonce markers absent diagnostics; messages appear only worker receive envelope. Retain same-offer/CAS/presence tests and actual Task 1 F1/F2 regressions at new base.

- [ ] **Step 10: Run RED then add narrow guards to shared delivery.** Run Claude/native-inbox tests and record RED. Preserve one offered/acked receive path and existing locks/presence/release checks. Guard API entry and post-blocking disclosure/next action; stop revokes slot then exact run, not an unrelated binding. Generate scoped commands/file reference and truthful worker notice. Run same tests green. Do not rewrite Presence/RealtimeWait or undo Task 1 fixes.

- [ ] **Step 11: Update docs/prompts/evals with route tests.** Write/extend prompt assertions first: `test_claude_prompt_pending_background_attest_is_exclusive_with_parent_fallback`, `test_claude_create_and_join_commands_use_capability_file_not_token`, `test_missing_background_support_reports_once_without_replacement`. Run RED, then update references/conditional skill routing. Document create/access recovery, explicit service permission selected only under existing ongoing owner authority, default finite QA, terminal pending timeout/stop and finite waits. Permission does not prove provider lifetime, and no renewal/regrant/replacement is added. Preserve same-user trust. No promise of Desktop idle, child resume, host secrecy, continuous service or instantaneous remote revocation. Run affected prompt/eval tests green.

- [ ] **Step 12: Run focused/full source checks and self-review.** Run `uv run --frozen python -m unittest discover -s tests -p test_quorum_claude_worker.py -v`, affected native-inbox/session/invite/prompt/delegation suites, then `uv run --frozen python -m unittest discover -s tests -v`. Expected all applicable tests pass, skips documented. Inspect changed files and confirm no auth/provider/config/runtime side effect/test hidden launch. No redundant full-suite reruns absent further changes/failures.

- [ ] **Step 13: Run every pinned gate before handoff/commit.** Commands below must exit0; no SKIP/no-verify/config overrides. Save logs and exact head/diff evidence. Source implementer returns report with changed files, RED/GREEN, limitations, base identity and no-live-call statement; controller conducts fresh independent task review before integration.

```bash
uv lock --check
uv sync --frozen
uv run --frozen python scripts/validate_skills.py
uv run --frozen python -m unittest discover -s tests -v
./scripts/check-agent-skills-spec.sh
./scripts/check-skills-cli.sh
uv run --frozen pre-commit run --all-files
```

If full suite already passed at exactly the same unchanged head in Step12, retain that evidence rather than run it twice; rerun affected/full gates when changed head or fixes justify it. CLI smoke must discover and individual-copy-install Quorum alone for both Codex and Claude Code from clean temporary projects. No repository-local parser substitutes for pinned skills-ref/skills@1.5.20.

## Storage contract amendment

Controller-selected during Task 2 review fix round 3 under the existing autonomous delivery instruction, not a separate owner selection, and not a claim that the earlier guarantee passed. The paired design's storage lifetime premise replaces the former lifetime-long sidecar/ancestor-rename isolation requirement: private actor directories and ancestors stay in place while a SQLite connection is in use. Keep ordinary disk journaling and crash recovery (no MEMORY or OFF journal, weakened synchronization or sidecar deletion), allow validated private recovery journals, and refuse unsafe sidecars before mutable use. Keep static no-follow/owner/mode refusal without chmod, the actual main-file identity check and exact A-to-B-to-A open test, read-only metadata and anchored helper locks/logs. Add crash rollback of uncommitted existing-row updates and hot-journal recovery through a capability route. All other steps, scope and acceptance gates are unchanged. These tracked documents are therefore no longer exact copies of the originally promoted files.

Final whole-branch wave (controller ruling `final-fix-recovery-ruling.md`): apply the premise and the static file, sidecar and lock checks to every Claude root, index, slot and actor ledger entry; recheck authority after receive cleanup immediately before disclosure; add the narrow locked slot hot-journal recovery before record validation through existing owner lifecycle and capability commands, with read-only status reporting `recovery_needed`; and limit installed wording to rollback of uncommitted local SQLite changes. No new command, grant, dependency or storage engine.

Bounded residual correction (controller procedural exception to the one-wave cap; recovery contract unchanged): run every record-independent capability, argument and create-input check before slot recovery, and make `recovery_needed` advice ledger-specific (slot, actor, state) with a behavioural actor-journal regression.

## Independent review and integration handoff

Controller dispatches fresh task review of immutable patch/head against approved brief/design, focusing on trust, preflight ordering, create retry scope, race safety, blocking-boundary guards and unchanged Codex/native paths. Findings feed a bounded fix round and changed-head verification. Controller then commits/pushes only through PR after required gates; never merge missing/skipped/cancelled/pending/failing required checks. No source job claims runtime acceptance from tests. Keep #50 open.

## Runtime acceptance — separate from implementation/gates

Do not execute in source job. Controller uses explicit owner-authorized existing Claude/native context and approved private auth/service targets after reviewed source is installed. Synthetic provider proof is already done; do not rerun a substitute proof to declare this new integration accepted.

- [ ] Create flow: explicit until-owner-stop under existing service authority, one actual native background child, bounded pending slot, actual returned handle, trusted parent attest (tool-use audit optional), child scoped create/open-or-github access and connect. Verify stable target/request recovery and unchanged parent registration from independent service evidence; do not broaden create scope.
- [ ] Join flow independently: exact approved invitation and slug, pending/native handle/attest, own child accept/connect; denied preflight leaves invitation unused. No replacement session to evade unavailable surface.
- [ ] Send a fresh peer nonce unknown to parent through real Quorum. Actual bound child consumes its foreground receive result and explicitly receipts/replies with its own commands. Independently verify real membership/device/agent/session IDs, backend receipt/reply, logical actor/actual handle/genuine shared SID and owned child native tool-result/action evidence. Source job must not pre-generate that peer nonce.
- [ ] Verify empty bounded receive, lost-output/offer recovery, service stop/revocation and a separate bounded permission expiry case, pending expiry and scope failures and matching/revoked coordinator grant behavior without raw parent body leakage. Do not claim rollback if a stopped in-flight request completed remotely.
- [ ] Inspect native parent transcript/UI separately from private host multiplexed child rows. Raw nonce/body absent parent transcript and routine summaries; child final public/material only. Token never in chat. Runtime inspection is exact owned-test evidence, not a product transcript-reading dependency.
- [ ] Report explicit permission lifetime separately from observed provider lifetime and exact evidence level/remaining unknowns. Desktop parent-idle, provider resume of child, post-completion reception, continuous service and unsupported-platform owner proof remain separate/unproven; no completion claim for those from this Task2 acceptance.

## Plan self-review receipt

Spec coverage mapped to Steps2–11 and separate runtime checklist; typed signatures/fields/grammar/defaults agree with compact design. Review Focus conditions each have a named test. One integrated task avoids publishing partial authority plumbing. No source changes, provider launches, live calls or Task1-completion claims were made while writing this plan.
