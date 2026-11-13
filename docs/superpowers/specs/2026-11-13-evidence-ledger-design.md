# Evidence Ledger: design

Status: experimental. Evaluation records are in
[docs/evals/evidence-ledger](../../evals/evidence-ledger/results.md). This
document was written without reading any earlier attempt at the same problem.

## Problem

Several agents investigate one uncertain question and a coordinator turns
their work into a decision. Three distinctions are easy to lose on the way:

1. Rival approaches collapse into the first plausible one.
2. Several voices repeating one source look like independent confirmation.
3. "Someone said so", "it is documented", "it was reproduced somewhere" and
   "it was verified on the asker's own setup" blur into "confirmed".

All three produce a decision that sounds surer than its evidence.

## Analysis

### Where the information is lost

The three errors share one cause. A decision needs four things about every
fact: what it says, who first observed it, how it is known, and on what setup.
A normal agent report keeps the first and drops the other three, because a
report is written as a conclusion ("the fix is confirmed") and a conclusion
has no slot for them. Once dropped they cannot be rebuilt by the reader:

- without origins, the coordinator can only count reports, so agreement
  stands in for independence;
- without the rung and the setup, every "confirmed" reads as the strongest
  kind;
- with conclusions in hand, the coordinator weighs conclusions, and the rival
  held by one lane loses to the one held by two.

So the errors are handoff errors. An agent that holds all the evidence itself
has no handoff, which predicts that a strong single agent will not make them
on a packet it can read whole. The evaluation confirmed this (see Results).

### The opposite error

A lane that sees a third of the evidence is right to say "not established":
from where it stands, it is not. A coordinator that merges three such
verdicts inherits three sets of doubts, even when the pooled facts settle the
question. A flat panel can therefore be less decisive than one agent with the
same evidence. This error was not in the brief, the first probe found it, and
it turned out to be the larger one for a strong model.

Both errors come from passing verdicts. The fix is the same for both.

### What the literature says

Checked for this work:

- Hidden-profile tasks (Stasser and Titus, 1985) give each group member part
  of the evidence; groups discuss what they share and miss what one member
  holds. HiddenBench reproduces this for LLM groups: about 30% accuracy with
  distributed information against about 81% for one agent holding everything
  ([arXiv 2505.11556](https://arxiv.org/abs/2505.11556)). The slices in this
  evaluation are a hidden-profile design.
- Multi-agent debate does not reliably beat simpler ensembling such as
  self-consistency and is sensitive to its settings (Smit et al., ICML 2024,
  [arXiv 2311.17371](https://arxiv.org/abs/2311.17371)). This argues against
  adding discussion rounds as the mechanism.

Used as background, from memory, not re-read for this work: the Condorcet
jury theorem (majorities help only when votes are independent), the Delphi
method (collect judgments blind before sharing), Heuer's analysis of competing
hypotheses (reject hypotheses by disconfirming evidence, do not collect
support for a favourite), the intelligence "Admiralty" grading (rate the
source and the information separately), GRADE in medicine (grade the body of
evidence, including indirectness: evidence from another population counts for
less), and circular reporting (one report cited through several outlets).

## Alternatives considered

| Alternative | Why not chosen |
| --- | --- |
| Debate: lanes read each other and argue for several rounds | Costs a round per exchange, pulls lanes toward agreement, and the literature above finds no reliable gain |
| Majority or confidence-weighted voting over lane verdicts | Counts voices. This is the failure itself when lanes share sources |
| A dedicated sceptic or red-team lane | Adds a call and a verdict, not the missing fields; in the probe the baseline was already too sceptical on sound evidence |
| A verifier pass that re-reads every source after the merge | Doubles the reading cost; needed only when reports lack origin and setup, which the report format fixes at the source |
| Structured report format only, no change to the merge | Kept as an ablation |
| A script that deduplicates sources by URL or hash | Shared origins are semantic (same figure, re-plotted data, quoted changelog); a script would miss them, and the skill must stay instruction-only and portable |
| Numeric confidence scores | Models produce them freely and they hide what the confidence rests on; a rung plus an origin count is checkable by a reader |

## Mechanism

The skill fixes what a report between agents must carry and how the
coordinator may use it.

1. **Frame before reading.** The coordinator writes the target (the exact
   setup the decision is about) and two to four rival answers, one of which
   rejects the question's own framing.
2. **Blind lanes that return claim lines.** Each lane gets the question, the
   target, the rivals and one rival to test hardest. It returns one line per
   fact with its origin, its rung (`told`, `documented`, `observed-elsewhere`,
   `observed-on-target`) and the setup it was observed on. It returns no
   recommendation.
3. **Ledger merge.** The coordinator groups claims by origin, re-rates each
   rung against the target, and drops a rival only when a named observation
   contradicts it.
4. **Decision capped by the crux.** The decision is as sure as the rung and
   origin count of the claim it turns on: commit plainly when that claim is
   observed on the target, otherwise hold with the cheapest check, or split
   the decision by where the evidence applies.
5. **Fixed report block** that names the crux, its rung, what was ruled out
   and by what, and what nobody observed on the target.

With no subagents the same steps run in one pass.

## Non-goals

- No scheduler, agent provisioning, spend enforcement, cross-session memory
  or auto-publishing.
- No scripts and no client-specific metadata.
- Not a search or browsing method: it says nothing about how a lane finds
  sources.
- Not for lookups with one authoritative source or for choices that are cheap
  to reverse.
- No numeric probabilities.

## Results and what was cut

See the last section of
[results.md](../../evals/evidence-ledger/results.md); it is the single place
where numbers, ablations and open questions are kept.
