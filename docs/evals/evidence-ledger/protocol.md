# Evaluation protocol (fixed before any run)

This file, the 16 scenarios, the split and the harness were committed before
the first model call. Later changes to any of them are listed in
[results.md](results.md) under "Deviations".

## Scenarios

Each file in `scenarios/` is a closed packet: a question, the asker's setup,
six or seven short documents, a reference answer, and yes/no checks. All
products and people are invented so that model memory cannot answer.

| Class | Planted trap | Right answer |
| --- | --- | --- |
| `shared-origin` (so1-so4) | Several documents repeat one upstream source; one unrelated measurement disagrees | Do not act on the repeated claim |
| `evidence-level` (el1-el4) | A claim is documented or reproduced, but on a setup that differs from the asker's in a way that matters | Do not treat it as verified for the asker |
| `collapse` (co1-co4) | The first, most salient explanation or framing is wrong; a quieter rival fits every observation | Name the rival |
| `control` (ct1-ct4) | None: the evidence is independent and verified on the asker's setup | Commit plainly |

Controls are there to catch a method that only teaches hedging.

Each scenario also fixes three slices of its documents, one per panel member,
as if three agents had each found different sources. The document that breaks
the trap sits in one slice only, and copies of a shared origin are spread
across slices.

## Split

`split.json` comes from `run_eval.py split`: a shuffle with seed 20261113
inside each class, two scenarios to `dev`, two to `heldout`. The skill is
designed and tuned on `dev` only. `heldout` is run once, at the end, for all
conditions, and is reported whole, not filtered by who failed.

## Conditions

All calls use the same model through `claude -p` with no tools and no
settings. Every final memo is capped at 250 words; working notes before the
memo marker are allowed in every condition.

| Condition | Calls | What each call sees |
| --- | --- | --- |
| `single` | 1 | Question and every document |
| `flat` | 4 | Three analysts each see the question and one slice and report in at most N words; a coordinator sees the question and the three reports |
| `skill` | 5 | A coordinator with the skill writes three briefs from the question alone; three lanes each see a brief and one slice and report in at most N words; the coordinator with the skill sees the reports |
| `solo` | 1 | Question, every document and the skill, told that no subagents exist |

N (the lane word cap) is 120 unless stated. It stands for the fact that a
subagent cannot hand over everything it read; `flat` and `skill` always get
the same N. `single` and `solo` hold all documents, which favours them: in
real work one agent often cannot read everything.

Ablations are `skill` runs against a copy of the skill with one part removed.

## Grading

A judge call sees the question, the reference answer, the memo and the
scenario's checks, and answers each check yes or no. It never sees the
condition. A record **passes** when every check whose id starts with
`decision` or `hedge` gets its expected answer. The `aware` check (did the
memo name the trap) is reported separately and does not affect passing.

Primary numbers, per condition:

- trap pass rate: passes over runs of the 12 trap scenarios;
- control pass rate: passes over runs of the 4 controls;
- mean calls, input tokens, output tokens, list-price cost and model seconds
  per run, as reported by the CLI.

Three runs per scenario and condition. With 4 to 6 trap scenarios per set this
detects only large effects; results are reported as counts, not p-values.

## Judge check

A sample of at least 20 graded memos is re-graded by hand by the author and,
separately, by a second model family (`codex exec`). Disagreements are
reported.

## Known limits, accepted in advance

- Scenarios are written by the same author as the skill.
- Packets are small and closed; live web research is not covered.
- The harness plays the host: it carries briefs and reports between calls. A
  separate end-to-end check runs the installed skill in real Claude Code and
  Codex sessions on a few scenarios.
