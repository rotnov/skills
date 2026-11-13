---
name: evidence-ledger
description: >-
  Use when several agents, or several passes by one agent, investigate one
  uncertain question that drives a decision (research, diagnosis, architecture
  or library choice, review) and the answer must say how well it is supported.
---

# Instructions

Run a multi-agent investigation so that the final decision rests on counted
evidence, not on how many agents agree. Reports between agents are short, so
what they must carry is fixed: for each fact, where it was first observed, how
strong that observation is, and on what setup.

## Scope

Use it when a wrong answer is costly and the evidence is second-hand, mixed or
gathered by more than one agent. Skip it for a lookup with one authoritative
source or a choice that is cheap to reverse: it costs one planning turn, the
lanes, and one merging turn.

## Terms

- **Target**: the exact setup, population or case the decision applies to.
- **Rival**: one competing answer to the question.
- **Lane**: one independent investigation, by a subagent or a separate pass.
- **Origin**: the person, team or run that first observed or measured a fact.
  Every source that repeats it shares that origin.
- **Rung**: how a fact is known, lowest to highest:
  `told` (someone says so), `documented` (a primary source states it),
  `observed-elsewhere` (measured or reproduced on a setup that is not the
  target), `observed-on-target` (measured or reproduced on the target itself).

## Workflow

### 1. Frame before reading

Write down, before opening any evidence:

1. the decision and the target;
2. two to four rivals. One of them must reject the question's own framing
   ("the premise is wrong", "neither option", "the effect is not real").

### 2. Run lanes blind

Give each lane the question, the target, the rivals, and one rival to test
hardest. Lanes do not see each other's work. Write each brief from
[references/lane-brief.md](references/lane-brief.md) when you are about to
dispatch lanes; it holds the report format lanes must return.

With no subagents, do the same alone: frame first, then read everything once
and write the claim lines yourself before forming a view.

### 3. Merge into a ledger

Build one ledger from the claim lines, not from the lanes' conclusions. Read
[references/ledger.md](references/ledger.md) when an origin or a rung is
unclear.

1. **Count origins, not voices.** Group claims by origin. Lanes, articles and
   people repeating one origin are one line of evidence. A lane agreeing with
   another lane is not evidence.
2. **Re-rate every rung against the target.** "Verified", "confirmed" and
   "reproduced" in a report mean only the rung the claim line supports. An
   observation on a setup that differs from the target where the mechanism
   lives is `observed-elsewhere`.
3. **Kill rivals with observations only.** A rival is dropped when a named
   claim contradicts it, never because fewer lanes backed it or it surfaced
   later. Test the leading rival against every observation: when the effect
   started, where it appeared, when it stopped. One observation it cannot
   explain keeps the other rivals alive.

### 4. Decide

Find the crux: the claim the decision turns on. The decision can be no surer
than the crux's rung and its count of independent origins.

- Crux `observed-on-target` and nothing contradicts it: commit plainly. Do not
  add checks or caveats the evidence does not call for.
- Crux lower: say what is established and what is not, choose the action that
  is safe under every surviving rival, and name the cheapest check that would
  raise the rung.
- The evidence covers only part of the target: split the decision. Go where
  it is observed, hold where it is not.

### 5. Report

```text
Decision: <what to do>
Rests on: <crux claim> - <rung>, <n> independent origin(s)
Ruled out: <rival> - <the observation that kills it>
Not verified: <what nobody observed on the target, or "nothing material">
Next check: <cheapest step that raises the rung, or "none needed">
```

Keep rung words exact. Never write "confirmed" or "verified" for anything
below `observed-on-target`.
