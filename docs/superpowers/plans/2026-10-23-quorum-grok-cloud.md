# Grok Bot Cloud Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task.

**Goal:** Receive and answer Quorum messages in the existing Grok Bot cloud personal chat.

**Architecture:** A typed parent-bound cloud CLI shares the existing durable
RPCs and Realtime. A private provider webhook wakes the native Bot; its routine
only forwards a wake hint, and the original parent consumes and replies.

**Tech Stack:** Python 3.12, existing locked websocket transport, stdlib HTTPS,
existing Quorum/Supabase RPCs. No new dependency or cloud DDL.

**Spec:** `docs/superpowers/specs/2026-10-23-quorum-grok-cloud-design.md`

## Global Constraints

- Preserve native caller checks, anonymous membership, release guards, receipts,
  priority, coordinator scope and reply-loop protection.
- Only a real UUID-valued parent `CURSOR_CONVERSATION_ID` may mutate a cloud
  binding. `sand-subagent-*`, absent IDs and native-context conflicts are refused.
- Never print webhook URL/key, accept alternate hosts, follow redirects, read
  private provider IPC, export foreign identity or launch another model/client.
- Keep new runtime resources within `skills/quorum`; pinned official spec,
  skills@1.5.20 copy-install and pre-commit gates remain unchanged.
- Use the existing clean linked worktree on `feat/quorum-grok-cloud`. Preserve
  owner files and other worktrees. No live DDL, auth-setting or permission change.
- Stage and commit only through the controller after integrated full gates;
  parallel task workers must not stage, commit or push each other's changes.

## Review Focus

- A native webhook subagent must not claim the parent's UUID or run its reply.
- Missing or unsafe callback configuration must refuse ordinary acceptance
  before anonymous/GitHub login or consuming the single-use invitation.
- HTTP success/lost response/stdout failure must never imply UI delivery.
- A restart must not blindly resend a possibly accepted wake.
- Provider endpoint/key and hostile peer text must not leak or reach shell/URL
  interpolation, including exceptions and serialized diagnostics.

### Task 1: Parent-bound cloud operations and onboarding

**Files:** create `skills/quorum/scripts/quorum_grok_cloud.py` and
`tests/test_quorum_grok_cloud.py`; modify `quorum_live.py`,
`quorum_invitations.py`, top-level safe error handling in `quorum.py`, and
`quorum_delegation.py` only where a typed status label is necessary.

**Consumes:** existing `register_agent`, `api_for`, `LocalLedger`,
`ReleaseGuard`, `Control`/listener loop, `RealtimeWait`/`Presence`, grant policy;
Task 2's `configure(ledger, values)`, `load_target(ledger)` and
`CloudWakeRelay(api, ledger, record)`.

**Produces:** `add_commands(commands)`, `dispatch(args, auth_ledger,
register_agent)`, `preflight(auth_ledger)` for `--client grok-bot` acceptance;
cloud status/receive/send/reply commands and safe typed envelope.

- [ ] Write failing behavioral tests for genuine parent context, routine/native
  conflict refusal, invitation preflight, wrong binding, offered-only receipts,
  idempotent replies and unchanged native dispatch.
- [ ] Observe expected failures on the unchanged baseline.
- [ ] Implement the cloud CLI and reuse existing scoped RPCs/lifecycle. Start
  must inherit context, never synthesize provider metadata. No auto launches
  during connect/accept. Typed commands never silently choose membership-only.
- [ ] Verify focused cloud and affected native/onboarding tests. Report exact
  red/green commands, source files, remaining integration concerns.

### Task 2: Private authenticated webhook wake relay

**Files:** create `skills/quorum/scripts/quorum_grok_webhook.py` and
`tests/test_quorum_grok_webhook.py`. No Task 1 source edits.

**Consumes:** `api`, private cloud `ledger`, and an owned agent `record` with
`agent_id`, `session_ref`, `quorum_id`, `provider_conversation_id`.

**Produces:** `configure(ledger, values: dict) -> dict` (safe diagnostics only),
`load_target(ledger)` (private validated URL/key object), and
`CloudWakeRelay(api, ledger, record)` implementing `reconcile()` and
`run_once() -> idle|delivered|unknown` for the existing listener loop.

- [ ] Write failing tests for the pinned HTTPS provider UUID path, unsafe links
  and file modes, redirects and redaction, one wake per pending message,
  timeout-after-write unknown state and actual parent receipt reconciliation.
- [ ] Observe the expected failures on unchanged baseline.
- [ ] Implement private target storage and bounded POST. Send only a fixed
  `quorum.inbox_changed` hint and nonsecret routing/message IDs. Never send peer
  bodies or ACK server delivery. Mark intent before POST; do not retry unknown.
- [ ] Verify focused tests with a controlled transport fixture, including
  HTTP acceptance without receipt, unknown restart and revoked/version failure.

### Task 3: Shared skill flow and invitation documentation

**Files:** create `skills/quorum/references/grok-cloud.md`; update
`SKILL.md`, `README.md`, `references/join.md`, `references/grok.md`,
`references/sessions.md` and invitation generation only as needed for routing.

- [ ] Capture the current failure from #40 / independent read-only audit: the
  installed native flow cannot bind the actual hosted Bot and must refuse it.
- [ ] Document actual parent identity, private webhook setup, native routine
  wake-only prompt and original-parent receive/reply loop; no invented SID.
- [ ] State provider background routine behavior, setup approval, active-VM
  requirement, error recovery and absence of Grok steer semantics accurately.
- [ ] Run an independent clean-copy onboarding read-through for both clients;
  verify users can find setup/join and know what remains unproven.

### Task 4: Integration, independent review and real chat acceptance

- [ ] Run focused integration, then all seven pinned repository gates, with
  host-native client exports removed only in test subprocesses. Preserve other
  environment values and never weaken Windows ACL tests.
- [ ] Review current head independently for binding, transport side effects,
  receipts and credential redaction. Fix demonstrated findings and rerun gates.
- [ ] Open a PR with concrete behavior, validations and unproven boundaries.
- [ ] Prepare one reviewed real Grok Bot setup and obtain only the final live
  routine/access approval, as required by computer-use policy.
- [ ] Verify active and idle same-chat delivery/reply with the existing owner
  root sender; stop only owned test resources and retain sanitized evidence.
- [ ] Merge only after pinned pre-commit and all required CI pass; publish no
  claim of hibernated-VM reception or idle wake before actual receipt evidence.
