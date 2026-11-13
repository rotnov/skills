# Evidence Ledger

Merge the findings of several agents into one decision. Each agent saw only
part of the evidence, so a panel's doubts add up and it stops committing even
when the pooled facts settle the question. The skill pools facts instead of
verdicts and applies one decision rule: commit plainly when the deciding
claim was observed on your own setup by someone who looked for themselves and
nothing else fits better; otherwise hold with the one cheapest check, or split
the decision. Instruction-only: no scripts, no network, no client-specific
features.

## Status: experimental

This skill is an experiment, not a proven tool. Read the
[evaluation results](https://github.com/rotnov/skills/blob/main/docs/evals/evidence-ledger/results.md) before you
rely on it. The honest limits:

- **The errors it was designed for did not show up.** The skill was meant to
  stop a panel from collapsing rival explanations, counting a shared source
  twice, or treating a retold claim as verified. The strong baseline model
  made none of these errors: 288 of 288 trap runs passed without the skill.
  Nothing here shows that the skill prevents them.
- **What it fixes is the opposite failure.** A flat panel that merges
  verdicts tends not to commit even when the pooled facts settle the
  question. In the test harness the skill committed on 15 of 15 held-out
  controls, against 8 of 15 for the flat panel, and kept all 42 traps.
- **The effect in real hosts is small.** In real Claude Code and Codex
  sessions with the reading split between subagents, panels without the
  skill passed 13 of 16 control runs and panels with it passed 16 of 16. With
  two runs per cell, that difference is within chance. When every subagent
  could read all the evidence, the skill made no difference.
- **One author.** The same author wrote the scenarios, the reference answers
  and the skill, and knew the held-out scenarios while writing it. The effect
  rests on six control scenarios and one model family in the harness.
- **Cost.** About 6% more list-price cost than a flat panel with the same
  number of calls.

## Install the skill

Paste this into your agent:

```text
Set up Evidence Ledger for me: fetch and follow this guide https://github.com/rotnov/skills/blob/main/skills/evidence-ledger/README.md
```

Or install it yourself:

```bash
npx --yes "skills@>=1.7.4" add rotnov/skills --skill evidence-ledger -a claude-code codex --global --copy -y
```

## Requirements

None beyond the agent itself. Subagents are used when the host has them;
without them the same decision rule applies to one agent's own reading. If
one agent can read all the evidence itself, that is about three times cheaper
than a panel and the skill's decision rule still applies.

## Use

Ask your agent, for example:

> Use three agents to find out whether upgrading fixes this bug for our
> setup, then merge what they found with the evidence-ledger skill.

For agent instructions, see [SKILL.md](SKILL.md).

## Setup guide for agents

You are an AI agent asked to set up this skill.

1. Install it globally for Claude Code and Codex with the skills CLI 1.7.4 or newer:
   `npx --yes "skills@>=1.7.4" add rotnov/skills --skill evidence-ledger -a claude-code codex --global --copy -y`.
2. Read the installed [SKILL.md](SKILL.md).
3. Check the requirements above and report anything missing with the exact fix.
4. Tell the user it is ready and give one example request from the Use section.
