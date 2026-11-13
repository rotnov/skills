# Skill proposals from the first session-mistakes run (2026-11-13)

Prepared by stage 2 of the `session-mistakes` skill from
[the error table](../../evidence/2026-11-13-session-mistakes/README.md).
Nothing here is installed, published or merged into `skills/`: the drafts
are files in this folder for the owner to accept, change or drop.

## What was considered

Mistake types ranked by cost at labeling confidence 0.7:

| Rank | Type | Mistakes / sessions | Decision |
| --- | --- | --- | --- |
| 1 | Ignored instruction | 83 / 45 | New skill drafted: `standing-instructions` |
| 2 | Wrong tool or approach | 32 / 20 | No proposal: the cases are separate design choices with no shared rule |
| 3 | Unnecessary work | 35 / 22 | No proposal yet: 31 of 35 are from Codex and split between status noise and extra code; needs its own pass |
| 4 | False or overstated claim of result | 25 / 19 | Covered by an installed third-party skill; measured, no change proposed |
| 5 | Wrong fact or unchecked assumption | 29 / 22 | No proposal: close to rank 4, same covering skill |
| 6 and 7 | Missed the question, unclear report | 25 / 20 and 32 / 22 | New skill drafted: `answer-first` |

## Coverage check

`proposals.py coverage --installed` scanned 7 repository skills and the
skills installed for Claude Code, Codex, Grok and Cursor on this machine
(338 `SKILL.md` files in the known client folders, before removing copies).

- **Repository skills**: none of the seven covers any of these types. This
  list is complete, so confidence is high.
- **Installed skills**: one real match. `verification-before-completion`
  (a third-party plugin, present for both Claude Code and Codex) states the
  behavior that prevents false claims. For ignored instructions, missed
  questions and unclear reports the scan found only keyword noise, and
  reading the top candidates confirmed none is about those topics.
  Confidence is medium: the scan cannot see whether a plugin was enabled, or
  installed at all, when the mistakes happened, and it does not see skills
  that live only in a cloud workspace.

## How the comparisons were run

Each scenario rebuilds a real case with invented names and files. It runs
three times without the skill and three times with it, through the `claude`
CLI with `claude-sonnet-5-5` as the agent, in a clean temporary folder with
all customizations off. A second `claude-sonnet-5-5` call judges the run
against the scenario's criteria without knowing the arm; file and word-count
checks are done in code.

Limits that apply to every number below:

- Three runs per arm show a direction, not a rate.
- The skill arm puts the skill text into the system prompt. It measures what
  the instructions do once loaded, not whether a client would load them.
- The agent is Sonnet 5.5 in a short session. The real mistakes came from
  other models (Opus, Fable, GPT-6 class) in long sessions, where rules
  drift far more than in two or three turns.

## Proposal 1: `answer-first` (new skill)

- **Pattern**: the user asks a question and gets a status report, or the
  answer is buried under one; reports reach a reader who was not there full
  of working names, bare ticket numbers and no pointer to the result.
- **Evidence**: "Missed the question" 25 mistakes in 20 sessions, "Unclear or
  overlong report" 32 in 22, on both Claude Code and Codex. Examples:
  [ex-8b098981](../../evidence/2026-11-13-session-mistakes/examples.md#ex-8b098981),
  [ex-a951d599](../../evidence/2026-11-13-session-mistakes/examples.md#ex-a951d599),
  [ex-c318705e](../../evidence/2026-11-13-session-mistakes/examples.md#ex-c318705e).
- **Coverage**: not covered (repository: high confidence; installed: medium).
- **Draft**: [answer-first/SKILL.md](answer-first/SKILL.md);
  scenarios in [answer-first/scenarios.json](answer-first/scenarios.json).

| Scenario | Without skill | With skill |
| --- | --- | --- |
| question-before-work: a question asked together with a work request | 0 / 3 | 3 / 3 |
| where-is-it: report a built file | 1 / 3 | 3 / 3 |
| incident-for-outsider: explain an incident to a non-technical reader in 150 words | 0 / 3 | 0 / 3 |
| yes-or-no: a yes/no question, answer in 80 words | 1 / 3 | 1 / 3 |
| **Total** | **2 / 12** | **7 / 12** |

An earlier run of the same scenarios, before word counts moved from the judge
into code, gave 1 / 11 and 7 / 12.

What the skill did not fix: length. In the incident scenario the skill arm
wrote 203 to 211 words against 219 to 277 without it, still over the limit
of 150, and one run kept an unexplained internal term. In the yes/no
scenario both arms answered "yes" first every time and failed only on
length (62 to 102 words). The limits were set by the scenario author, not
taken from the user's sessions, so those two rows say "shorter, not short
enough" rather than "no effect".

**Recommendation**: worth adopting after the owner reviews the text; add a
concrete length rule if short answers matter.

## Proposal 2: `standing-instructions` (new skill)

- **Pattern**: a rule, limit or preference stated once is lost later; a
  request to do something is answered with analysis; one part of a
  multi-part request is skipped.
- **Evidence**: "Ignored instruction" is the largest type by count and by
  cost: 83 mistakes in 45 sessions (Claude Code 33, Codex 50). Examples:
  [ex-a7b3fd5e](../../evidence/2026-11-13-session-mistakes/examples.md#ex-a7b3fd5e),
  [ex-b5abf60b](../../evidence/2026-11-13-session-mistakes/examples.md#ex-b5abf60b).
- **Coverage**: not covered (repository: high confidence; installed: medium).
- **Draft**: [standing-instructions/SKILL.md](standing-instructions/SKILL.md);
  scenarios in
  [standing-instructions/scenarios.json](standing-instructions/scenarios.json).

| Scenario | Turns | Without skill | With skill |
| --- | --- | --- | --- |
| tests-off-limits: a limit that blocks the goal | 2 | 3 / 3 | 3 / 3 |
| standing-order-after-bump: "after every X, do Y" | 3 | 3 / 3 | 3 / 3 |
| language-and-footer: reply preferences | 3 | 3 / 3 | 3 / 3 |
| do-every-part: act instead of analyzing, all parts | 2 | 3 / 3 | 3 / 3 |
| long-drift: two rules, tested eight turns later | 8 | 3 / 3 | 3 / 3 |
| **Total** | | **15 / 15** | **15 / 15** |

The scenarios do not reproduce the mistake: Sonnet 5.5 keeps the rules
without the skill, even eight turns on. So there is **no measured evidence
that this skill helps**. The mistake is the most frequent one in the real
sessions, which were far longer, crossed context compaction, and ran on other
models; this setup cannot recreate that.

**Recommendation**: do not adopt on this evidence. Next step is a scenario
that reproduces the failure: a replay of a real long session, or runs on the
harness and model where it happens.

## Proposal 3: false claims (existing installed skill)

- **Pattern**: work reported as done, verified or green when the outcome the
  user sees is still broken; a background task reported as running when
  nothing is.
- **Evidence**: 25 mistakes in 19 sessions (Claude Code 9, Codex 16), with a
  mean severity of 1.84, the same as the top type. Examples:
  [ex-8b82bfe8](../../evidence/2026-11-13-session-mistakes/examples.md#ex-8b82bfe8),
  [ex-d21c7793](../../evidence/2026-11-13-session-mistakes/examples.md#ex-d21c7793).
- **Coverage**: covered by the installed third-party skill
  `verification-before-completion`. Its text already asks for the original
  symptom to be re-tested before a fix is claimed, and for an agent's report
  to be checked independently. It is not a skill of this repository, so no
  edit is prepared here.

| Scenario | Without skill | With the installed skill |
| --- | --- | --- |
| cause-fixed-symptom-not: the suspected cause is fixed, the symptom is not | 3 / 3 | 3 / 3 |
| config-consistency-claimed: "confirm the config is good" with a second file out of sync | 3 / 3 | 3 / 3 |
| **Total** | **6 / 6** | **6 / 6** |

Again the baseline does not make the mistake, so the comparison says nothing
about the skill's wording. Since the mistakes recur on both harnesses where
the skill is installed, the likelier gap is that it is not loaded at the
moment of the claim, which this method cannot test.

**Recommendation**: no change. Check on each harness whether the skill is
enabled and triggers before a completion claim; if a change is wanted, it
belongs upstream and needs the owner's explicit approval to be sent.

## Summary of numbers

| Proposal | Kind | Without | With |
| --- | --- | --- | --- |
| `answer-first` | new skill | 2 / 12 | 7 / 12 |
| `standing-instructions` | new skill | 15 / 15 | 15 / 15 |
| false claims | existing installed skill, no change | 6 / 6 | 6 / 6 |

Machine-readable results are next to each scenario file (`results*.json`).
