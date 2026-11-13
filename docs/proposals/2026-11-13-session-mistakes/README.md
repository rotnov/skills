# Skill proposals from the first session-mistakes run (2026-11-13)

Prepared by stage 2 of the `session-mistakes` skill from
[the error table](../../evidence/2026-11-13-session-mistakes/README.md).
Nothing here is installed, published or merged into `skills/`: the drafts
are files in this folder for the owner to accept, change or drop.

## What was considered

The proposals were chosen from the first labeling pass (305 mistakes at
confidence 0.7). The table shows those counts and, next to them, the counts
after every Codex message was labeled (853 mistakes). The second pass did
not change which types lead, so the choices stand; it moved "False or
overstated claim" from fourth to third by cost.

| Type | First pass: mistakes / sessions | Full: mistakes / sessions | Decision |
| --- | --- | --- | --- |
| Ignored instruction | 83 / 45 | 188 / 85 | New skill drafted: `standing-instructions` |
| Wrong tool or approach | 32 / 20 | 123 / 56 | No proposal: the cases are separate design choices with no shared rule |
| Unnecessary work | 35 / 22 | 102 / 44 | No proposal yet: nearly all from Codex, split between status noise and extra code; needs its own pass |
| False or overstated claim of result | 25 / 19 | 88 / 45 | Covered by an installed third-party skill; measured, no change proposed |
| Wrong fact or unchecked assumption | 29 / 22 | 96 / 59 | No proposal: close to false claims, same covering skill |
| Missed the question, unclear report | 25 / 20 and 32 / 22 | 81 / 53 and 67 / 42 | New skill drafted: `answer-first` |

The evidence lines under each proposal quote the full counts. The
descriptions behind the second pass were not read for new patterns.

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
against the scenario's criteria without knowing the arm; file checks and
word counts are done in code.

Where a Codex column is shown, the same scenarios also ran through
`codex exec` with `gpt-6-astra` (`compare --agent codex`), signed in as
usual but with a throwaway home folder, so no user config, AGENTS.md,
installed skills or history were loaded; Codex's four bundled system skills
remain. The skill text goes in as developer instructions. The judge is the
same Sonnet 5.5 call.

Limits that apply to every number below:

- Three runs per arm show a direction, not a rate.
- The skill arm puts the skill text into the system prompt. It measures what
  the instructions do once loaded, not whether a client would load them.
- The sessions are short. The real mistakes came from long sessions, mostly
  on other models (Opus, Fable, `gpt-6.1-sol`), where rules drift far more
  than in two to eight turns.
- Every verdict comes from a model judge. No person has read the runs.

## Proposal 1: `answer-first` (new skill)

- **Pattern**: the user asks a question and gets a status report, or the
  answer is buried under one; reports reach a reader who was not there full
  of working names, bare ticket numbers and no pointer to the result.
- **Evidence**: "Missed the question" 81 mistakes in 53 sessions, "Unclear or
  overlong report" 67 in 42, on both Claude Code and Codex. Examples:
  [ex-8b098981](../../evidence/2026-11-13-session-mistakes/examples.md#ex-8b098981),
  [ex-a951d599](../../evidence/2026-11-13-session-mistakes/examples.md#ex-a951d599),
  [ex-c318705e](../../evidence/2026-11-13-session-mistakes/examples.md#ex-c318705e).
- **Coverage**: not covered (repository: high confidence; installed: medium).
- **Draft**: [answer-first/SKILL.md](answer-first/SKILL.md);
  scenarios in [answer-first/scenarios.json](answer-first/scenarios.json).

Passes out of runs, without the skill and with it. Claude Code runs
`claude-sonnet-5-5`; Codex runs `gpt-6-astra` (added after the first
hand-off, see "How the comparisons were run").

| Scenario | Claude without | Claude with | Codex without | Codex with |
| --- | --- | --- | --- | --- |
| question-before-work: a question asked together with a work request | 0 / 3 | 3 / 3 | 0 / 3 | 3 / 3 |
| where-is-it: report a built file | 0 / 3 | 3 / 3 | 3 / 3 | 3 / 3 |
| incident-for-outsider: explain an incident to a non-technical reader | 0 / 3 | 0 / 2 | 1 / 3 | 2 / 3 |
| yes-or-no: a yes/no question | 3 / 3 | 3 / 3 | 3 / 3 | 3 / 3 |
| **Total** | **3 / 12** | **9 / 11** | **7 / 12** | **11 / 12** |

One Claude run with the skill is missing because the judge's reply was not
valid JSON; it is counted as an error, not as a pass or a fail.

What changed in the scenarios since the first version, and why:

- The first version capped the final message at 150 words (incident) and
  80 words (yes/no). Those numbers were the scenario author's, not the
  user's, and they decided both rows: 2 / 12 against 7 / 12 with every
  failure in those two rows being a length failure. They are removed.
- The incident scenario now checks what the real complaints were about: no
  tables, headings or timestamp timeline, and no internal names the reader
  would have to ask about.
- For the yes/no scenario a criterion "nothing after the answer except what
  serves the decision" was tried and dropped after one run: the judge
  failed replies for mentioning that a dry run still runs the tests, which
  is not the complaint. With only "the reply begins with yes" left, both
  agents pass without the skill, so **this scenario no longer reproduces a
  mistake** and adds nothing to either total. That criterion was changed
  after seeing results; treat the yes/no row as uninformative, not as
  evidence.

What the numbers say:

- The effect that holds on both agents is "answer the question before the
  work report" (0 / 3 to 3 / 3 on each). On Claude the skill also makes the
  agent name the built file (0 / 3 to 3 / 3); Codex already does.
- The incident report stays unsolved on Claude (0 / 3 and 0 / 2): with the
  skill the customer impact moves up and jargon goes, but bold headings,
  clock times and a person's initials remain. On Codex it moves from 1 / 3
  to 2 / 3, which three runs cannot tell from noise.
- Length, reported but no longer judged: Claude's incident report is 223 to
  278 words without the skill and 212 to 230 with it; Codex's is 113 to 134
  and 108 to 115. The skill does not make answers short.

**Recommendation**: worth adopting after the owner reviews the text. If
plain, short reports for outside readers matter, the draft needs a stronger
rule on formatting and length first, and a re-run.

## Proposal 2: `standing-instructions` (new skill)

- **Pattern**: a rule, limit or preference stated once is lost later; a
  request to do something is answered with analysis; one part of a
  multi-part request is skipped.
- **Evidence**: "Ignored instruction" is the largest type by count and by
  cost: 188 mistakes in 85 sessions (Claude Code 33, Codex 155). Examples:
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

The same five scenarios on Codex with `gpt-6-astra` (the harness with the
most mistakes of this type) gave the same result: 15 / 15 without the skill
and 15 / 15 with it.

The scenarios do not reproduce the mistake: Sonnet 5.5 and `gpt-6-astra`
both keep the rules without the skill, even eight turns on. So there is
**no measured evidence that this skill helps**. The mistake is the most frequent one in the real
sessions, which were far longer, crossed context compaction, and ran on other
models; this setup cannot recreate that.

**Recommendation**: do not adopt on this evidence. Next step is a scenario
that reproduces the failure: a replay of a real long session, or runs on the
harness and model where it happens.

## Proposal 3: false claims (existing installed skill)

- **Pattern**: work reported as done, verified or green when the outcome the
  user sees is still broken; a background task reported as running when
  nothing is.
- **Evidence**: 88 mistakes in 45 sessions (Claude Code 9, Codex 79), with a
  mean severity of 1.86, above the top type's 1.78. Examples:
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

Passes out of runs, without the skill and with it.

| Proposal | Kind | Claude Code, Sonnet 5.5 | Codex, `gpt-6-astra` |
| --- | --- | --- | --- |
| `answer-first` | new skill | 3 / 12 and 9 / 11 | 7 / 12 and 11 / 12 |
| `standing-instructions` | new skill | 15 / 15 and 15 / 15 | 15 / 15 and 15 / 15 |
| false claims | existing installed skill, no change | 6 / 6 and 6 / 6 | not run |

Machine-readable results are next to each scenario file (`results*.json`;
Codex runs in `results-codex.json`).
