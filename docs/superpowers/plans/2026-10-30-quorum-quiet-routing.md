# Quorum quiet routing implementation plan

> **For agentic workers:** Use superpowers:executing-plans to implement this plan inline; use superpowers:test-driven-development for behavior changes.

**Goal:** Make a requested quiet worker inbox fail before invitation consumption or registration when only parent-chat delivery is selected.

**Architecture:** Preserve both existing transports and their identity checks. Add an explicit worker-inbox requirement to acceptance and connection, enforce it before any state/authentication side effect, and use it in generated Codex/Claude worker setup. Capacity or provenance failures leave the invitation unused. Parent delivery requires a disclosed owner choice.

**Tech Stack:** Python 3.12, argparse, unittest, existing native worker inboxes.

**Spec:** [Issue #68](https://github.com/rotnov/skills/issues/68); existing worker authority and lifecycle contracts remain binding.

The integrated onboarding change also includes [#46](https://github.com/rotnov/skills/issues/46)'s narrow local macOS Cursor refusal, independently developed RED→GREEN in a separate worktree. It preserves unused invitations and supported native identities; it adds no Cursor transport. Actual original-client qualification remains separate.

## Global constraints

- No change to receipt meanings, automatic replies, runtime identity, grants, listener lifetime or existing bindings.
- No new background/model processes, provider spoofing, message dropping or ACK-as-UI-retraction claim.
- An explicit legacy parent route remains available, with visible queue cards disclosed.
- Source tests prove routing refusal and worker behavior; actual client UI acceptance remains separate.
- Run all seven repository gates before committing/pushing. Review the complete branch independently and require exact-head CI before merge.

## Review focus

Check refusal ordering, capability scope, untouched legacy parent behavior, generated-prompt consistency and truthful offline/busy/idle claims. The requirement flag is a routing constraint, never worker provenance or authorization.

## Task 1: Require an actual worker route before side effects

Files: `skills/quorum/scripts/quorum.py`, `quorum_session.py`, `quorum_native_inbox.py`; tests in `tests/test_quorum_native_inbox.py` and `tests/test_quorum_claude_worker.py`.

1. Add behavioral CLI tests for required quiet acceptance/connect with omitted or explicit native transport. Guard ledger/profile/login/API/registration and assert no state directory or consumed invitation.
2. Observe failure before adding `--require-worker-inbox` to the shared acceptance/connect transport arguments.
3. Enforce the flag before root ledger and Claude worker routing. Return fixed `native_worker_required` / `parent_route_visible` with a useful disclosure.
4. Prove the flag allows the existing verified Codex and attested Claude worker routes, cannot bypass missing provenance/capability and starts no parent queue or background process.
5. Run the focused native inbox/Claude worker/invitation/session suites.

## Task 2: Make quiet setup preserve the selected route

Files: `skills/quorum/scripts/quorum_invitations.py`, `SKILL.md`, `references/service-agent.md`, `references/native-inbox.md`, `references/claude-worker.md`, `references/join.md`, `references/invite.md`; existing prompt tests/evals when affected.

1. Put the requirement flag in generated Codex/Claude worker accept/connect commands.
2. Replace automatic parent fallback with one truthful blocker and unused invitation; describe the explicit owner choice needed for visible parent routing.
3. Keep actual unsupported-provider and bounded-worker limitations explicit. Never promise continuous reception between calls.
4. Run affected suites, inspect generated invitation variants and the complete diff for contradictory route instructions.

## Task 3: Verify and qualify

1. Run the complete unit suite and all seven required gates using pinned uv 0.11.7; preserve private RED/GREEN and gate logs.
2. Independently review the complete immutable candidate; fix material findings with regressions and rerun affected checks.
3. Perform a bounded authorized native worker receive/receipt/reply with actual parent UI busy/idle observation if the current client exposes the necessary tools. Treat missing capability as an explicit acceptance gap.
4. Open the PR, verify exact-head required CI, merge under the authorized milestone workflow, and record evidence. Close #68 only when its actual-client criteria hold.
