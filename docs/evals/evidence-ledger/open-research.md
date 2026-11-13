# Evidence Ledger: open-ended research with live web search

Run on 2026-11-13. Protocol, fixed before the first call and amended once
before the second batch: [open-research/protocol.md](open-research/protocol.md).
Tasks and keys: [open-research/tasks.json](open-research/tasks.json). Every
number below is recomputed from `open-research/runs/*.jsonl` by
`open-research/run_open.py summary`, with the author's adjudication in
`open-research/author-main.json` and `open-research/author-deep.json`.

## Question

The closed-packet evaluation ([results.md](results.md)) found that neither
one agent nor a flat panel made the three errors that heavier
multi-agent research machinery is meant to prevent. This probe asks whether
they appear when the research is real: live web search, agents that see
different material, contested questions.

- **E1, collapse:** the answer settles on one explanation while a rival
  that the evidence leaves open goes unnamed.
- **E2, shared root counted twice:** retellings of one origin are presented
  as separate support.
- **E3, level blur:** a retelling, a press summary, an abstract, or a result
  from a different setup is stated as established for the asker.

## Short answers

1. **E1 and E2 do not appear in the baseline.** Across 60 answers from one
   agent and 60 from a flat panel, the author confirmed no E1 and no E2.
   Every E2 found came from the coordinator that had the shipped skill.
2. **E3 appears in every condition, mostly on side claims.** One agent
   made it in 5 of 30 answers and a flat panel in 9 of 30. In 4 of those 14
   answers it touched the claim the decision rested on, all in the deep arm.
   Three ranked which `docker` cause dominates using benchmarks from other
   setups; one proposed citation wording for `unsafe` that rested on figures
   the answer itself called unchecked. No decision was wrong in any
   condition.
3. **Verdict: the research-organisation machinery is not needed.** Keeping
   rival hypotheses apart and tracing shared sources are already handled
   without help. The E3 that remains is not specific to multi-agent work:
   one agent makes it as well, and branching or exchange rounds would not
   address it.
4. **The shipped skill had its own fault.** Its "Rests on" line led the
   coordinator to claim more about where facts came from than the lanes
   reported. It wrote "all read directly by our researchers" when one lane
   had said it could not open the report, and "all three researchers
   independently found" when all three relied on one forum post. Across
   both arms, 8 of 30 skill answers had an error on the crux, against 2 of
   30 for the flat panel.
5. **Fix: three sentences in step 2** (keep each fact's provenance as the
   lane gave it, and never upgrade it). Errors on the crux fell from 8 of 30
   to 1 of 30, with 0 wrong decisions. On the held-out closed packets the
   fixed text scores the same as the shipped one: 48 of 48 traps and 15 of 15
   controls, both with subagents and alone.

## What was run

Model `claude-opus-5-5` through `claude -p`, with WebSearch and WebFetch for
researchers and no tools for coordinators. Settings, MCP and memory were off,
and each call ran in an empty directory.

| Arm | Tasks x runs | Researcher budget | Median researcher session |
| --- | --- | --- | --- |
| Standard | 6 x 3 | One agent: about 36 searches or fetches. Lanes: about 12 each | One agent: 12 turns, 69k input tokens. Lane: 12 turns, 74k |
| Deep (fresh runs) | 6 x 2 | One agent: about 60, at least 25 distinct sources. Lanes: about 20, at least 10 | One agent: 44 turns, 460k input tokens. Lane: 21 turns, 144k |

Conditions:

| Condition | What it is |
| --- | --- |
| `single` | One agent researches and answers |
| `flat` | Three lanes, briefed by angle (primary and official sources; press and forums; independent measurements and critiques), then a coordinator with no skill |
| `ledger` | The same three lane reports, coordinator with the shipped `SKILL.md` |
| `ledger_prov` | The same three lane reports, coordinator with `SKILL.md` plus the provenance fix |

All panel coordinators read identical lane reports, so differences between
`flat`, `ledger` and `ledger_prov` come from the merge alone.

Tasks (keys written from primary sources before the runs):

| Id | Kind | Pressure built in |
| --- | --- | --- |
| `tre` | Health claim | "8-hour eating, 91% more heart deaths": one observational abstract, retold by many outlets |
| `ft313` | Technical choice | Free-threaded Python: production status and wheels differ between 3.13 and 3.14+ |
| `docker` | Diagnosis | Slow dev container on Apple Silicon: two separate causes (amd64 emulation, bind mounts) |
| `unsafe` | Contested figure | Share of Rust crates using `unsafe`: one foundation report retold widely; studies differ by definition |
| `sqlite` | Safety (works as a control) | SQLite with WAL on NFS: the official docs against forum anecdotes; the right answer is a plain no |
| `blackout` | Contested cause | Iberian blackout of 2025: a misattributed early statement; the blame is contested |

Grading: a judge call (same model, never told the condition) with the key,
the answer and, for panels, the lane reports. The author read every answer
the judge flagged and 12 it did not flag. A second judge prompt
(`prov`) asked one question of every panel answer: does it claim more about
where facts came from than the lane reports show?

## Results

Counts are author-confirmed. E1 is not applicable to `sqlite`. "On the crux"
means the error sits in the claim the decision rests on, or in the part the
asker said they needed to be attributable.

### Standard arm (18 answers per condition)

| Condition | Decision good/acceptable/wrong | E1 | E2 | E3 | Any error | On the crux |
| --- | --- | --- | --- | --- | --- | --- |
| single | 16/2/0 | 0/15 | 0/18 | 3/18 | 3/18 | 0/18 |
| flat | 18/0/0 | 0/15 | 0/18 | 5/18 | 5/18 | 0/18 |
| ledger (shipped) | 14/4/0 | 0/15 | 2/18 | 8/18 | 9/18 | 4/18 |
| ledger_prov (fix) | 15/3/0 | 0/15 | 0/18 | 5/18 | 5/18 | 1/18 |

### Deep arm (12 answers per condition, fresh runs)

| Condition | Decision good/acceptable/wrong | E1 | E2 | E3 | Any error | On the crux |
| --- | --- | --- | --- | --- | --- | --- |
| single | 11/1/0 | 0/10 | 0/12 | 2/12 | 2/12 | 2/12 |
| flat | 10/2/0 | 0/10 | 0/12 | 4/12 | 4/12 | 2/12 |
| ledger (shipped) | 11/1/0 | 0/10 | 2/12 | 4/12 | 5/12 | 4/12 |
| ledger_prov (fix) | 10/2/0 | 0/10 | 0/12 | 0/12 | 0/12 | 0/12 |

### Provenance check (judge only, panel answers)

| Condition | Standard (paired lanes) | Deep (fresh) |
| --- | --- | --- |
| flat | 8/18 | 8/12 |
| ledger (shipped) | 12/18 | 8/12 |
| ledger_prov (fix) | 7/18 | 5/12 |

This check is strict: it counts any detail stated with more certainty about
its source than the lanes gave. It is high even for `flat`. The fix brings the
skilled coordinator back to the flat panel's level or below it. It does not
make provenance perfect.

### What the errors looked like

- **E1: none confirmed.** The judge flagged two answers. In one, the answer
  called the evidence of harm weak; it did not claim the harm was excluded.
  The other came from a stale item in the key (below). Rivals that lanes
  raised and the answer left out fell into the same two groups: the key
  item, or a residual-risk caveat that the answer covered by calling the
  evidence weak.
- **E2: only with the shipped skill (4 answers).** Three said the lanes
  found something "independently" when all three cited one source. One
  listed a project that quotes the SQLite documentation as a separate source
  "with the same conclusion". On the planted shared-origin traps (`tre`,
  `unsafe`, `blackout`), every condition traced the news coverage back to its
  origin. Several answers volunteered that a second analysis of the same data
  "is not independent confirmation".
- **E3 without the skill:** mostly side claims taken from a secondary
  source and stated flatly. One example appeared in all six standard-arm
  `ft313` answers from both conditions: "manylinux dropped 3.13t", sourced to
  a PyTorch forum post. On the crux it happened four times, all in the deep
  arm. In three, a `docker` answer ranked which cause dominates using
  benchmarks from other machines, although its own caveat said nobody had
  measured the asker's setup. In the fourth, an `unsafe` answer's suggested
  citation rested on figures it had called unchecked.
- **E3 with the shipped skill:** the same side claims, plus provenance
  upgrades in the "Rests on" line. Examples: "all read directly by our
  researchers", press summaries listed as "the panel's findings", news-only
  figures placed in the decision's basis.

### Cost (means per answer, list price as reported by the CLI)

| Arm, condition | Calls | Input tokens | Output tokens | USD | Wall seconds |
| --- | --- | --- | --- | --- | --- |
| Standard, single | 1 | 87k | 3.3k | 0.28 | 67 |
| Standard, any panel | 4 | 229k to 230k | 11.5k to 11.8k | 0.91 to 0.92 | 90 to 93 |
| Deep, single | 1 | 427k | 9.7k | 0.85 | 178 |
| Deep, any panel | 4 | 452k to 453k | 16.6k to 16.8k | 1.39 to 1.41 | 129 to 133 |

The fix adds about 90 input tokens to the coordinator. Totals for the subject
calls: 126 calls, $24.94 (standard), and 84 calls, $29.30 (deep), at list
price. Judge calls are not metered here. Everything ran under the existing
subscription; no new money was spent.

## The fix

Added to step 2 ("Pool") of `skills/evidence-ledger/SKILL.md`:

> Keep each fact's provenance as the lane gave it: read in the primary
> source, or seen only in a summary or retelling, with any doubt the lane
> attached. Never upgrade it: do not write that a source was read, or that
> lanes agree independently, unless the reports show it.

Ablation: `ledger` is the same skill without these three sentences, run on the
same lane reports. The decision rule fixed in the addendum was to ship only
if the fix had fewer provenance overclaims than `ledger` in both arms and no
more wrong decisions. Result: 7 against 12, and 5 against 8, with 0 wrong
decisions. The rule was met.

## Regression on the closed packets

The fixed text was run on the held-out closed packets used for
[results.md](results.md) (`runs/heldout-provenance.jsonl`, the same families,
lane caps and conditions as `heldout-shipped.jsonl`).

| Condition | Traps | Controls |
| --- | --- | --- |
| Skill panel, shipped text | 48/48 | 15/15 |
| Skill panel, fixed text | 48/48 | 15/15 |
| Skill alone, shipped text | 48/48 | 15/15 |
| Skill alone, fixed text | 48/48 | 15/15 |

The judge said the trap was named ("aware") in 16 of 18 long-family panel
runs with the fixed text, against 18 of 18 before; this does not affect
passing. Tokens per run are within 5%. The CLI reported a higher list price
for the fixed panel runs (about $0.17 against $0.09). Token counts barely
changed, so the difference is probably cache writes for the new text; this
was not checked.

## Deviations

- **Stale key item.** For `blackout`, the key listed "the role of renewables
  and inertia" as contested. The ENTSO-E final report (March 2026) and the
  Spanish committee both say lack of inertia was not the cause. Answers that
  treated this as settled by the investigations were not marked down. This
  changed 15 decisions from "acceptable" to "good" across all conditions, and
  one E1 flag to "no".
- **Sessions in the standard arm were shorter than intended.** Researchers
  stopped well inside their budget. The deep arm was added (pre-registered)
  to cover long sessions with dozens of sources.
- **The fix and the provenance check were designed after the first batch**,
  because of what that batch showed. They were committed before any further
  call, and the fix was then measured on fresh deep runs.

## What remains unverified

- **The author is not blind.** The skilled answers use a recognisable
  four-line format, and the same author wrote the keys and adjudicated the
  flags. The judge is the same model family as the subjects. No
  second-family cross-check was run for this probe.
- **Detection is uneven.** Panel answers were judged with their lane
  reports, so a provenance upgrade can be seen. A single agent's upgrade of
  its own sources cannot be seen this way, so E3 for `single` is a lower
  bound.
- **The sample is small.** Six tasks, five runs each per condition. This
  finds frequent errors only. An error rate below about one in six could be
  missed.
- **Model memory.** Several tasks are partly answerable from it. A
  question entirely after the model's cutoff might behave differently.
- **Only one model.** A weaker model was not tried in this probe.
- **The skill used alone (no subagents) was not tested on open research.**
  The deep `docker` E3 (ranking causes from other setups) is the kind of
  error its "observed on the asker's setup" rule targets.
- **Real hosts were not tested.** Claude Code or Codex subagents doing live
  research were not run end to end. This harness plays the host.
