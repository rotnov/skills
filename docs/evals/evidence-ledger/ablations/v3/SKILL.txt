---
name: evidence-ledger
description: >-
  Use when several agents, or several passes by one agent, have investigated
  one uncertain question (research, diagnosis, architecture or library choice,
  review) and their findings must be merged into one decision.
---

# Instructions

Turn the findings of several agents into one decision. Each agent saw part of
the evidence, so each is right to sound unsure; merged as they stand, their
doubts add up to a decision that will not commit even when the pooled facts
settle the question. Merge facts, then decide once.

## Scope

Use it when two or more lanes (subagents, or separate passes) each looked at
part of the evidence for one decision. Skip it when one agent can read all the
evidence itself: that is cheaper and needs no merge.

## Workflow

### 1. Run the lanes

Lanes work apart and do not see each other's reports. Brief them as you
normally would, or read [references/lane-brief.md](references/lane-brief.md)
when you write the briefs yourself and want shorter reports. Reports that
already exist are fine as they are.

### 2. Pool

List the facts from all reports in one ledger before judging anything. Take
facts from the lanes, not conclusions: a lane's recommendation, and its list
of what is missing, were written without the other lanes' facts.

### 3. Decide

Find the crux, the claim the decision turns on. Decide for the action, not for
certainty: a doubt matters only if settling it could change what to do.

The crux is settled when it was observed on the asker's own setup, or on a
representative part of it, and not only somewhere else; by someone who made
their own observation, however many others repeat it; and no other answer
fits the observations better.

- **Commit** when the crux is settled. Say so plainly. Do not add conditions,
  pre-checks or caveats; routine monitoring is not a condition.
- **Hold** when it is not, and the action is costly to undo. Name the one
  cheapest check that would settle it and the action that is safe meanwhile.
- **Split** when the evidence covers part of the asker's setup: go where the
  crux is settled, hold where it is not.

### 4. Report

```text
Decision: <what to do>
Rests on: <the crux, who observed it, and where>
Not verified: <what would change the decision and nobody observed on the
asker's setup, or "nothing that would change the decision">
Next check: <the one cheapest step, or "none needed">
```

Write "confirmed" or "verified" only for what was observed on the asker's own
setup.

## Without subagents

Skip step 1. Read the evidence once, list the facts, then apply steps 3 and 4.
