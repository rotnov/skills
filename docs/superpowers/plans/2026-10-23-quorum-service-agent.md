# Quorum Service Agent Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development or superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Delegate authorized Quorum setup and maintenance to native `quorum`
while keeping routine transport chatter out of main-chat responses.

**Architecture:** One canonical service-agent reference and a short entrypoint
rule, consistently used by creation/join, generated invitations and native/cloud
envelopes. The real bound parent retains all identity-bound commands.

**Tech Stack:** Markdown behavior contracts; existing Python prompt generators.

**Spec:** `docs/superpowers/specs/2026-10-23-quorum-service-agent-design.md`.

## Global Constraints

- Only existing supported native subagents; no model CLI as a runtime fallback.
- No auth, grant, session identity, receipt implementation, transport, SQL or
  release-version changes. Routine notifications never create task workers.
- No claim of hidden client UI routing or independent worker wake support.
- Shared Codex/Claude instructions, independent skill installation and pinned
  skills-ref / skills@1.5.20 / pre-commit gates remain required.

## Review Focus

- Create and join both select/reuse the named worker, not just task dispatch.
- Native/cloud notices and ordinary replies do not demand main-chat echoes.
- Substantive peer work still respects local owner delegation and parent IDs.
- No-native/capacity fallback stays functional and quiet without fake workers.
- Public skill and invitation prompts do not contradict the canonical policy.

### Task 1: Service-worker policy and generated instructions

**Files:**

- Add `skills/quorum/references/service-agent.md`.
- Update the concise `skills/quorum/SKILL.md` routing and relevant creation,
  join, invite, sessions, delegation and cloud references / README.
- Update prompt generators in `quorum_invitations.py`, `quorum_session.py`,
  `quorum_delegation.py`, `quorum_grok_cloud.py` only where they instruct agents.
- Adjust affected invitation, session, dispatch and cloud tests as needed.

**Interfaces:** Existing function signatures, JSON output and APIs stay intact.
The reference defines worker duties and quiet parent output; generators carry
the same policy without granting task rights or changing protocol receipts.

- [ ] Record the existing noisy/show-every-message baseline and a bounded
  behavior scenario before editing; write meaningful failing prompt regression
  coverage where generated instructions contradict the new contract.
- [ ] Implement the canonical reference, routing and generated instructions.
- [ ] Run affected tests and independent behavior scenarios for the five
  review-focus cases; record exact commands and results.
- [ ] Run all seven AGENTS.md gates, including full pre-commit, before commit.
- [ ] Review the whole immutable diff independently, fix concrete findings,
  create a PR, and merge under existing owner authority after green checks.
