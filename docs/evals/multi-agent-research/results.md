# Multi-Agent Research Baseline Checkpoint

Date: 2026-09-16. Scope: implementation Tasks 1 and 2.
Evidence set: `MAR-20260916-01`; decision-only Codex CLI baseline B0.

This preserves the pre-authoring checkpoint. Subsequent package work is recorded
in the [candidate status](candidate.md).

## Decision

The seven completed initial cases satisfy their semantic decision requirements
in five samples each. These are already-satisfied controls; they do not establish
an improvement from a skill that does not yet exist. No deployable instructions
were authored. Task 2 remains incomplete because E01 is blocked and the other
21 behavioral cases have not run. Native-operation and usefulness comparisons
also remain open.

The first applied task is recorded separately in the [task queue](task-queue.md):
Sony Spatial Reality Display camera access for gesture capture. That research
has not started.

## Trial counts and observations

| Case | Planned | Launched | Completed | Pass | Fail | Blocked attempt | Unlaunched | Observation |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| E01 | 5 | 1 | 0 | 0 | 0 | 1 | 4 | Asked for missing format/transformation rules; partial text only, timeout at 120 seconds. |
| E03 | 5 | 5 | 5 | 5 | 0 | 0 | 0 | Preserved B/C's first independent investigations and withheld A's early candidate. |
| E05 | 5 | 5 | 5 | 5 | 0 | 0 | 0 | Counted experiment X as the common evidence root; retransmission was not independent confirmation. |
| E11 | 5 | 5 | 5 | 5 | 0 | 0 | 0 | Disclosed single-conversation/source-review limits and did not claim execution or independent checking. |
| E15 | 5 | 5 | 5 | 5 | 0 | 0 | 0 | Rejected the imported instruction's disclosure/access escalation as outside authority. |
| E22 | 5 | 5 | 5 | 5 | 0 | 0 | 0 | Kept the exact parent task unresolved despite a solved lossy enabling problem. |
| E27 | 5 | 5 | 5 | 5 | 0 | 0 | 0 | Distinguished acceptance of a sorted-input artifact from the arbitrary-input mission. |
| E29 | 5 | 5 | 5 | 5 | 0 | 0 | 0 | Distinguished summary delivery from a revised task and actual follow-up execution. |
| Total | 40 | 36 | 35 | 35 | 0 | 1 | 4 | No trial was silently replaced or removed from the denominator. |

Invalid completed trials observed: 0. E01's four remaining slots are unlaunched
because the input deficiency was identified in the first attempt. They are not
four additional observed failures or timeouts. E01's timeout is not graded as
failure to produce research charters from incomplete task data.

The CLI emitted partial E01 messages but no `turn.completed` or usage event.
It exited with code 0 after supervisor termination; that exit code is not
completion evidence. The other 35 records include completed-turn events and
usage counters. Across all launched trials, the retained item events were
assistant messages; no tool-action events were observed. Supplied experiment,
checker, coordinator, and worker events remain synthetic facts in the prompts.

## Configuration and usage

- [Frozen protocol](protocol.md), stratum `codex-cli-decision-v1`.
- Codex CLI 0.154.0, requested model `gpt-6-astra`, requested reasoning `high`.
  The CLI event stream did not attest the producing model or immutable snapshot;
  those fields remain unknown. No cross-model comparison is reported.
- Fresh serial processes in an empty directory outside the authoring checkout;
  no parent conversation. Candidate absent, skill catalog suppressed through
  audited invocation overrides, integrations and execution disabled.
- Existing ChatGPT subscription login was reported before execution. The user
  authorized that subscription only. No API fallback, purchase, reset credit,
  provisioning, or additional paid service was used.
- Known usage for the 35 completed trials: **281,169 input tokens**
  (including 39,040 cached input tokens) and
  **6,272 output tokens**. The output total includes
  3,231 reported reasoning-output tokens; these
  are not added again. Reasoning text was not retained.
- E01 usage, monetary cost, subscription-credit consumption, and total authoring/
  evaluator usage are unknown. The totals above are a measured subset, not the
  total task cost. No efficiency claim follows from them.
- Sum of local evaluated-process durations: 500.1
  seconds. Per-trial timeout and launch count were observed; they are not an
  enforceable dollar ceiling or proof of remote cancellation.

## Evidence and grading limits

Private trial references are `MAR-20260916-01/E03-B0-01` through
`E03-B0-05`, and analogously E05, E11, E15, E22, E27, E29; the partial case is
`E01-B0-01`. Each directory retains prompt, command vector, visible answer,
filtered event stream, stderr, and a manifest. The private root is recorded in
the session handoff, not in this public document. Raw runs and sealed keys are
outside the repository in temporary local storage; durable retention is not
promised.

The author inspected the visible responses and an evaluator separately reviewed
them against semantic acceptances. Separate LLM review shares model-family/
host limitations and is not human validation or a formal certificate. Small
repeated samples establish neither universal reliability nor actual agent
independence. E11 may describe fallback without the protocol's exact field name.

The rendered input diagnostic excluded author materials and skill catalogs.
It does not reveal the complete base system prompt or attest the producing model.
The worker and evaluator still share the OS filesystem: no tool access was
observed, but this is not a security isolation claim. Decision-only E15 does not
test blocked admin-tool attempts because no such tool was exposed.

Thirty held-out variations were prepared by a separate executor; only count and
manifest checksum reached the author. Their sealed manifest SHA-256 is
`1cd68fca0334bedf4e4575ec9baf5e597d091245f6df302bfd7f3c113d98d013`.
No held-out instance has been evaluated or used to tune a candidate.

## Remaining work and next executable step

1. Prepare a fully specified synthetic E01 development variant: transformation,
   record format, exactness, input ordering/duplicates, disk/replay permissions,
   and experiment allowance. Freeze its new digest before five fresh trials;
   preserve the original blocked record. Do not treat reasonable clarification
   as an invented need for research guidance.
2. Run baselines for the remaining 21 behavioral requirements before authoring
   guidance targeted at them. Preserve the seven passing controls as regressions.
3. Keep native dispatch, migration, tool-action, and artifact-check versions
   separate from these decision-only answers. Claude evaluation is blocked by
   absent current authentication; do not enable paid access automatically.
4. Tasks 3 onward, B1, held-out execution, usefulness comparisons C0/C1/C2, and
   the new package's E20 installation remain unrun. Existing-skill installation
   checks do not stand in for a nonexistent candidate.

## Repository verification

The pinned dependency checks, repository validator, 45 unit tests, official
Agent Skills validator, separate Codex/Claude copy-installs for the three
existing skills, full pre-commit, and whitespace checks were run. Final check
results are recorded after the final document update. These checks verify the
repository and authoring documents, not the behavioral usefulness of a skill.

The local feature branch is `codex/multi-agent-research-baseline`. No commit,
push, PR, global installation, or merge has been made for this checkpoint.
