# Evidence Ledger: design

Status: experimental. Numbers, ablations and open questions are in
[docs/evals/evidence-ledger/results.md](../../evals/evidence-ledger/results.md).
This document was written without reading any earlier attempt at the same
problem.

## Problem as posed

Several agents investigate one uncertain question and a coordinator turns
their work into a decision. Three distinctions were expected to get lost:

1. Rival approaches collapse into the first plausible one.
2. Several voices repeating one source look like independent confirmation.
3. "Someone said so", "it is documented", "it was reproduced somewhere" and
   "it was verified on the asker's own setup" blur into "confirmed".

All three would produce a decision that sounds surer than its evidence.

## What the probe found instead

Before the design was settled, the baseline was probed on the dev scenarios:
8 traps and 3 controls in three families (short packets, the same packets
buried in filler, and "quiet" packets where no document contradicts the
claim), with one agent and with a flat panel of three, on a strong and on a
small model, with lane reports cut to 120, 60 and 40 words.

- The baseline made none of the three expected errors: 192 of 192 trap runs
  passed, in every configuration tried.
- A flat panel made the opposite error. On controls, where the evidence was
  independent and observed on the asker's own setup, it committed in 6 of 24
  runs on the strong model and 0 of 15 on the small one. One agent holding
  all the evidence committed in 11 of 15 runs on the strong model and 3 of 9
  on the small one.

So for current models, on evidence of this size, the measurable failure of a
panel is not overconfidence. It is a decision that will not commit.

### Why a panel over-hedges

A lane that sees a third of the evidence is right to say "not established":
from where it stands, it is not. It also lists what it could not see. A
coordinator that merges three such reports inherits three verdicts of "open"
and three lists of gaps, and it keeps them even when the three sets of facts,
put side by side, settle the question. The doubts are real for each lane and
false for the panel.

This is the hidden-profile effect (Stasser and Titus, 1985) with the sign
reversed: the facts do get pooled, but the lanes' conclusions, formed before
pooling, survive it.

### What the literature says

Checked for this work:

- HiddenBench reproduces hidden-profile failures for LLM groups that discuss
  and vote: about 30% accuracy with distributed information against about 81%
  for one agent holding everything
  ([arXiv 2505.11556](https://arxiv.org/abs/2505.11556)). The slices in this
  evaluation follow that design, with a coordinator in place of a discussion.
- Multi-agent debate does not reliably beat simpler ensembling and is
  sensitive to its settings (Smit et al., ICML 2024,
  [arXiv 2311.17371](https://arxiv.org/abs/2311.17371)). This argued against
  discussion rounds as the mechanism.

Used as background, from memory, not re-read for this work: the Condorcet
jury theorem, the Delphi method, Heuer's analysis of competing hypotheses,
the intelligence "Admiralty" grading of source and information, GRADE's
treatment of indirect evidence, and circular reporting.

## Alternatives considered

| Alternative | Outcome |
| --- | --- |
| Debate rounds between lanes | Not built: a round per exchange, pressure toward agreement, no reliable gain in the literature |
| Voting over lane verdicts | Not built: counts voices, and verdicts are what misleads the coordinator |
| A sceptic or red-team lane | Not built: the baseline was already too sceptical on sound evidence |
| A verifier pass that re-reads every source | Not built: doubles the reading cost for a failure that did not occur |
| A script that deduplicates sources | Not built: shared origins are semantic, and the skill must stay instruction-only |
| Numeric confidence scores | Not built: they hide what the confidence rests on |
| Planning turn with rivals written before reading, one rival per lane | Built (drafts 1 and 2), then cut: removing it changed no decision and saved a call |
| Four-rung evidence ladder with a reference file | Built (drafts 1 and 2), then cut: removing it changed no decision. Draft 1 made things worse: lanes rated sound evidence as "elsewhere" and the panel hedged on every control |
| Lane report format (claim lines with origin, how known, where observed, no verdict) | Built, tested alone: short controls stayed at 0 of 6. Kept as an optional brief through draft 3, then cut: it changed no decision on dev or held-out and its effect on cost was inconsistent |
| Pooling rule, origin count, target check and rival test as separate merge rules | Built, then folded into one sentence: removing them changed no decision |

## Mechanism

The shipped skill is a merge procedure for the coordinator. It works on any
lane reports, including ones already written.

1. **Run the lanes** apart, briefed as usual. The skill puts no format on
   their reports.
2. **Pool.** List the facts from all reports before judging. Take facts, not
   the lanes' conclusions or gap lists, which were written without the other
   lanes' facts.
3. **Decide for the action, not for certainty.** The crux is settled when it
   was observed on the asker's own setup (not only elsewhere), by someone who
   made their own observation (however many repeat it), and no other answer
   fits the observations better. Then: commit plainly when it is settled;
   hold with the one cheapest check when it is not and the action is costly
   to undo; split when the evidence covers part of the setup.
4. **Report** in four lines: decision, what it rests on, what is not verified
   that would change it, the next check or "none needed".

The three distinctions from the problem statement survive as the three
clauses of "settled" in step 3. They are the conditions for committing, not
separate machinery.

Without subagents, the same rule is applied to one agent's own reading.

### What carries the effect

Ablations on dev (see results) attribute the whole measured effect to step 3.
With step 3 removed, controls fall from 9 of 9 to 3 of 9. With everything
except pooling and step 3 removed, nothing measured changes. The shipped
skill is therefore one file of under 70 lines with no references.

## Non-goals

- No scheduler, agent provisioning, spend enforcement, cross-session memory
  or auto-publishing.
- No scripts and no client-specific metadata.
- Not a search or browsing method: it says nothing about how a lane finds
  sources.
- Not a replacement for one agent reading the evidence when it fits in one
  context: that is cheaper and scored about as well.
- No numeric probabilities.

## Risks carried by the design

- The effect was measured mainly in a harness whose lanes report in 60 to 120
  words. In real Claude Code and Codex sessions the no-skill panel hedged far
  less (3 of 16 control runs with split reading, none when every subagent
  could read everything), so the skill may matter little where reports are
  long and views overlap.

- The rule that restores commitment could push a panel to commit on thin
  evidence. Two scenarios written to provoke this are in the evaluation; the
  middle ground between a clear trap and a clear control is otherwise
  untested.
- All scenarios and the skill have one author, who knew the held-out
  scenarios while writing the skill. A unit test keeps scenario names out of
  the skill; it cannot keep out ideas.
