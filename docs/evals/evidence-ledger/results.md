# Evidence Ledger: evaluation results

Run on 2026-11-13. Protocol: [protocol.md](protocol.md). Design:
[the design doc](../../superpowers/specs/2026-11-13-evidence-ledger-design.md).
Every number below is recomputed from `runs/*.jsonl` by
`harness/summary.py` and `harness/summary_e2e.py`.

## Short answers

1. **Gap.** The baseline did not make the three expected errors (collapsed
   rivals, shared sources counted as independent, retold claims treated as
   verified) on any scenario class tried: 288 of 288 trap runs passed without
   the skill. A flat panel made the opposite error: it would not commit on
   sound evidence (14 of 39 control runs on the strong model, 0 of 21 with
   small-model lanes).
2. **Effect.** On held-out scenarios the skill keeps every trap (42 of 42)
   and commits on every control (15 of 15), against 8 of 15 for the flat
   panel and 13 of 15 for one agent. In real Claude Code and Codex sessions
   the effect is much smaller: none when every subagent can read the whole
   evidence folder, and 13 of 16 against 16 of 16 control runs when the
   reading is split between subagents.
3. **Cost.** At the same four calls as the flat panel: 13% more input tokens,
   21% more output tokens, 6% more list-price cost, 11% more model time.
   Alone: 31% more cost than one agent without the skill.
4. **Portability.** The installed skill was found, followed and produced the
   right decision in Claude Code and in Codex, with three real subagents and
   with none: 12 of 12 runs with the shipped text.
5. **Minimality.** One rule carries the measured effect: decide for the
   action, with explicit commit, hold and split. The planning turn, rivals
   written in advance, the evidence ladder, the lane report format and the
   separate origin, target and rival rules changed no decision and were cut.
   The shipped skill is one file of under 70 lines.

## What was run

| File in `runs/` | Content | Records |
| --- | --- | --- |
| `dev-baseline.jsonl` | Short dev, one agent and flat panel | 48 |
| `dev-baseline-haiku.jsonl` | Same with a small model, and with small-model lanes only | 72 |
| `dev-long-baseline.jsonl` | Long dev, lane caps 120 and 60 words | 72 |
| `dev-quiet-baseline.jsonl` | Quiet dev, lane caps 120 and 60 | 27 |
| `dev-weak-probe.jsonl` | Small model, 40-word lanes, long and quiet dev | 42 |
| `dev-skill-v1.jsonl`, `-v2`, `-v3` | Three drafts of the skill on dev | 48, 24, 123 |
| `dev-ablations.jsonl`, `dev-ablations-merge.jsonl` | Ablations on a dev subset | 84, 63 |
| `heldout.jsonl`, `heldout-long.jsonl` | Held-out, all conditions, frozen draft 3 | 195, 120 |
| `heldout-shipped.jsonl` | Held-out, the shipped one-file text | 126 |
| `e2e*.jsonl` | Real Claude Code and Codex sessions | 67 |
| `judge-crosscheck-codex.jsonl` | Second-family re-grading of a sample | 36 |

The subject model was `claude-opus-5-5` ("strong") through `claude -p`; the
small model was the CLI's `haiku` alias. No run errored.

## 1. Gap: where does the baseline fail?

The table is dev only; the held-out sets were not touched for it. "Trap" is a
run on a scenario with a planted error; "control" is a run where the evidence
is sound and the right answer is to commit.

| Family, condition, lane cap | Traps passed | Controls passed |
| --- | --- | --- |
| Short, one agent | 18/18 | 5/6 |
| Short, flat panel, 120 words | 18/18 | 0/6 |
| Long, one agent | 18/18 | 5/6 |
| Long, flat panel, 120 words | 18/18 | 1/6 |
| Long, flat panel, 60 words | 18/18 | 2/6 |
| Quiet, one agent | 6/6 | 1/3 |
| Quiet, flat panel, 120 words | 6/6 | 2/3 |
| Quiet, flat panel, 60 words | 6/6 | 1/3 |
| Short, small model, one agent | 18/18 | 2/6 |
| Short, small model, flat panel, 120 words | 18/18 | 0/6 |
| Short, small-model lanes, strong coordinator | 18/18 | 0/6 |
| Long, small model, flat panel, 40 words | 18/18 | 0/6 |
| Quiet, small model, one agent | 6/6 | 1/3 |
| Quiet, small model, flat panel, 40 words | 6/6 | 0/3 |

What this shows:

- **No overconfidence was found.** 192 of 192 dev trap runs passed without
  the skill, across shared-origin, evidence-level, collapse and scope-join
  traps, with documents up to about 270 words each, lane reports down to 40
  words, and a small model. Held-out added 96 more no-skill trap runs
  (including the weak-target probe), all passed. Making the trap quieter, the documents longer, the handoff narrower
  and the model weaker did not produce a single failure.
- **A flat panel under-commits.** Each lane sees part of the evidence and
  reports "not established" with a list of gaps; the coordinator keeps those
  doubts after pooling the facts. Example from `dev-baseline.jsonl`, scenario
  `ct2`: three lane reports each call the root cause unproven; the
  coordinator holds the pooled, complete chain of evidence and still writes
  "keep the investigation open for one more check".
- **One agent is mostly fine**, and cheapest. It hedged mainly on the two
  quiet controls (one dev, one held-out), where a comment in the packet
  raises a doubt that the packet's own trial answers.

The scenario classes on which the baseline makes the three errors named in
the brief were therefore **not found**. That is a finding about these
closed packets and these models, not proof that such errors never happen.

## 2. Effect

### Held-out

Eight short scenarios, the same eight in long form, and three quiet ones;
three runs each; lane cap 120 words (short) and 60 (long, quiet). The skill
text was frozen in git before these runs.

| Condition | Traps | Controls | Calls | Tokens in | Tokens out | Cost USD | Seconds |
| --- | --- | --- | --- | --- | --- | --- | --- |
| One agent, no skill | 42/42 | 13/15 | 1 | 2676 | 1010 | 0.032 | 13 |
| Flat panel, no skill | 42/42 | 8/15 | 4 | 6906 | 2186 | 0.085 | 35 |
| Skill panel, draft 3 with lane brief | 42/42 | 15/15 | 4 | 8724 | 2467 | 0.104 | 40 |
| Skill panel, draft 3, ordinary lane reports | 42/42 | 15/15 | 4 | 8202 | 2823 | 0.097 | 41 |
| **Skill panel, shipped text** | 42/42 | 15/15 | 4 | 7793 | 2643 | 0.090 | 39 |
| Skill alone, draft 3 | 42/42 | 15/15 | 1 | 4004 | 1210 | 0.039 | 15 |
| **Skill alone, shipped text** | 42/42 | 15/15 | 1 | 3591 | 1247 | 0.042 | 15 |

The shipped text is draft 3 with the lane brief removed and one sentence of
scope reworded; it was run on held-out once more because the shipped file
must be the tested file. Costs are means per run at list price as the CLI
reports them; nothing was billed beyond the existing subscription.

### Controls by scenario

Strong model, all families and lane caps pooled. The effect sits in a few
scenarios, and runs of one scenario are not independent.

| Control | Set | One agent | Flat panel | Skill panel | Skill alone |
| --- | --- | --- | --- | --- | --- |
| ct1 | dev | 4/6 | 2/9 | 9/9 | 3/3 |
| ct2 | dev | 6/6 | 1/9 | 9/9 | 3/3 |
| qc2 | dev | 1/3 | 3/6 | 6/6 | 3/3 |
| ct3 | held-out | 6/6 | 5/6 | 18/18 | 12/12 |
| ct4 | held-out | 6/6 | 3/6 | 18/18 | 12/12 |
| qc1 | held-out | 1/3 | 0/3 | 9/9 | 6/6 |

The flat panel's failure is weaker on held-out (8 of 15) than on dev (6 of
24). The dev controls happened to be the hard ones; nothing was selected.

### Does the rule push toward over-committing?

The rule that restores commitment says "do not add conditions". Two
scenarios were written after the ablations to provoke the obvious side
effect: evidence that is on the asker's own setup but too thin (a 25-minute
night canary before a peak-hour rollout; a one-day comparison before an
irreversible purge). They were committed before being run.

| Condition | Passed |
| --- | --- |
| One agent, flat panel (no skill) | 6/6, 6/6 |
| Skill panel, draft 3 (both variants) and shipped | 6/6, 6/6, 6/6 |
| Skill alone, draft 3 and shipped | 6/6, 6/6 |

No over-commitment on these two. They are loud; the middle ground is not
covered (see Unknowns).

## 3. Cost

Held-out means per run.

| Comparison | Calls | Tokens in | Tokens out | Cost | Model seconds |
| --- | --- | --- | --- | --- | --- |
| Shipped skill panel vs flat panel | 4 vs 4 | +13% | +21% | +6% | +11% |
| Shipped skill alone vs one agent | 1 vs 1 | +34% | +23% | +31% | +15% |
| Flat panel vs one agent | 4 vs 1 | 2.6x | 2.2x | 2.7x | 2.7x |

The skill adds no calls. It adds about 900 input tokens, loaded once by the
coordinator. The first two drafts used a planning turn and long briefs: five
calls and 2.5 to 2.8 times the flat panel's cost, for no better result.

In a real host the fixed overhead dominates: a three-subagent Claude Code
session on a six-file evidence folder used about 380,000 input tokens (mostly
cache reads) and 35 to 50 seconds with or without the skill.

## 4. Portability

The skill was copied to `.claude/skills/` and `.agents/skills/` of an empty
project (the layout the skills CLI installs), the scenario's documents were
written to `evidence/`, and a headless session was asked the question with
"use the evidence-ledger skill". The host decided everything else.

| Host, mode | Skill | Scenario | Pass | Subagents | Tokens in | Seconds |
| --- | --- | --- | --- | --- | --- | --- |
| Claude Code, three subagents | yes | el1, ct4, qc1 | 3/3 | 3 each | 375k to 385k | 34 to 50 |
| Claude Code, subagents disallowed | yes | el1, ct4, qc1 | 3/3 | 0 | 94k to 129k | 18 to 25 |
| Claude Code, three subagents | no | el1, ct4, qc1 | 3/3 | 3 each | 377k to 428k | 39 to 42 |
| Codex, three subagents | yes | el1, ct4, qc1 | 3/3 | 3 each | 205k to 251k | 57 to 71 |
| Codex, told to work alone | yes | el1, ct4, qc1 | 3/3 | 0 | 68k to 83k | 28 to 43 |
| Codex, three subagents | no | el1, ct4, qc1 | 3/3 | 3 each | 198k to 310k | 56 to 68 |

An earlier pass with draft 3 on Codex (`e2e.jsonl`: el1, ct3, q1) gave the
same picture, 9 of 9. Codex does not report subagent starts as events; the
count comes from the configuration warnings each new thread repeats, and a
separate probe confirmed that headless Codex does start subagents.

What this does and does not show:

- The skill is discovered and followed in both hosts, with and without
  subagents, and the four-line report appears in every run.
- **It shows no effect.** In these sessions every subagent could read the
  whole folder, so no lane had a partial view and the no-skill panel
  committed correctly on ct4 and qc1, the same controls the harness's flat
  panel failed.

### Real hosts with split reading

To put the partial view back, the same sessions were repeated with the
coordinator told to give each subagent one slice of the files and not to
read them itself.

Four controls (ct1, ct2, ct4, qc1) and one trap (el1), two runs each, in
`e2e-sliced.jsonl` and `e2e-sliced-codex.jsonl`. Every session started three
subagents.

| Host | Skill | Controls passed | Trap passed | Mean tokens in | Mean seconds |
| --- | --- | --- | --- | --- | --- |
| Claude Code | no | 6/8 | 2/2 | 331k | 41 |
| Claude Code | yes | 8/8 | 2/2 | 369k | 46 |
| Codex | no | 7/8 | 2/2 | 212k | 68 |
| Codex | yes | 8/8 | 2/2 | 205k | 75 |

Without the skill the real panels failed 3 of 16 control runs (qc1 twice in
Claude Code, ct1 once in Codex); with it, 0 of 16. That points the same way
as the harness, but it is much weaker: real subagents write long reports and
real coordinators mostly commit anyway. The harness's flat panel, with
reports capped at 60 to 120 words, is a harsher baseline than a real host,
and three failures in 16 runs is within what chance could produce.

## 5. Minimality

History of the skill, short dev set (18 trap runs, 6 control runs):

| Draft | What it had | Calls | Traps | Controls | Cost USD |
| --- | --- | --- | --- | --- | --- |
| 1 | Rivals framed before reading, one per lane; claim lines with a four-rung ladder; ledger merge; "confidence capped by the crux" | 5 | 18/18 | 0/6 | 0.251 |
| 2 | Draft 1 plus: lanes do not conclude or list gaps; pool first; decide for the action with commit, hold, split | 5 | 18/18 | 6/6 | 0.227 |
| 3 | Decision rule, pooling step, optional lane brief | 4 | 18/18 | 6/6 | 0.095 |
| Shipped | Decision rule and pooling step, one file | 4 | not run on dev | not run on dev | 0.090 on held-out |

Draft 1 is the design the problem statement suggests, and it made the panel
worse than doing nothing useful: lanes rated a reproduction in the production
namespace as "observed elsewhere", and the coordinator treated every rung
below the top as a reason to hold.

Ablations on a dev subset (five short and two quiet scenarios; 12 trap runs
and 9 control runs per row). Copies are in `ablations/`, derived from draft 2
by `harness/make_ablations.py`.

| Variant | Traps | Controls |
| --- | --- | --- |
| Flat panel, no skill (same scenarios, from the baseline files) | 12/12 | 2/9 |
| Lane report format only, coordinator without the skill | 12/12 | 3/9 |
| Coordinator rules only, ordinary lane reports | 12/12 | 9/9 |
| No planning turn, no rivals in advance | 12/12 | 9/9 |
| Also no rung ladder, no ledger reference | 12/12 | 9/9 |
| Coordinator rules without "pool first" | 12/12 | 9/9 |
| Coordinator rules without the decision rule | 12/12 | 3/9 |
| Only pooling and the decision rule | 12/12 | 9/9 |

Reading:

- The decision rule is necessary (without it, 3 of 9) and, with pooling,
  sufficient (9 of 9) on these scenarios.
- The lane report format, which was the core of the first design, does
  nothing alone. With the full skill it made the short dev runs 10% cheaper
  and the quiet dev runs 11% dearer, so it was cut.
- Rivals framed in advance, the planning turn, the rung ladder, and separate
  origin, target and rival rules changed no decision. They cannot be shown to
  help here, because the baseline never made the errors they guard against.
  The shipped text keeps their content only as the three clauses that define
  when the crux is settled.

## Judge check

- By hand: 23 graded memos read by the author (14 baseline, 9 skill); the
  judge's verdict matched in all 23.
- Second model family: 36 graded records re-graded with `codex exec` and the
  same prompt. Pass or fail agreed in 35 of 36, single checks in 73 of 74.
  The one disagreement was a baseline memo that Codex graded as hedged and
  the main judge as not; the stricter reading would lower a baseline number.
- The judge never saw the condition, only the memo.

## Deviations from the protocol

1. Two scenario families were added after the first probe found no trap
   failures: `long` (the 16 scenarios with each document buried in filler,
   generated by a model and checked to contain the original text verbatim)
   and `quiet` (six new hand-written scenarios with their own seeded split).
   Both were committed before they were run.
2. A third family, `weak` (two scenarios, no split), was added after the
   ablations to probe the skill's own risk. Committed before it was run.
3. The lane cap was varied (120, 60, 40 words), as the protocol allows.
4. The skill condition changed shape: the protocol describes five calls with
   a planning turn; from draft 3 the skill uses four.
5. The shipped text differs from the frozen draft 3 that the first held-out
   run used: the lane brief was removed and one sentence of scope was
   reworded after seeing held-out results (the decision rule was not
   touched). The held-out sets were run a second time for the shipped text;
   that second look is reported next to the first.

## Unknowns

- **The original three errors.** Not observed, so nothing here measures
  whether the skill prevents them. Larger or open evidence (live web
  research, long sessions, dozens of sources per lane), or other model
  families as the subject, may differ.
- **The middle ground.** Scenarios are clear traps or clear controls. How the
  commit rule behaves when the evidence is on the asker's setup but arguable
  is covered by two loud scenarios only.
- **Few controls.** The effect rests on six control scenarios; runs of one
  scenario are correlated. One of them (`qc2`) contains an unintended real
  caveat (a dropped file format) that a careful reader may fairly raise; it
  was kept as written.
- **One author.** Scenarios, reference answers and skill come from the same
  author, who knew the held-out scenarios while writing the skill.
- **One subject model family** in the harness. Codex appears only in the
  end-to-end sessions.
- **Real-host effect.** Small and not separable from chance in these
  samples (two runs per cell). The size of the effect in real work depends
  on how partial each lane's view is and how terse its report, and neither
  was measured outside the harness.
- **Judge.** Same family as the subject for the main grading; the
  cross-check covers 36 of about 1,050 graded harness records.
