# Evidence Ledger

Merge the findings of several agents into one decision. Each agent saw only
part of the evidence, so a panel's doubts add up and it stops committing even
when the pooled facts settle the question. The skill pools facts instead of
verdicts and applies one decision rule: commit plainly when the deciding
claim was observed on your own setup by someone who looked for themselves and
nothing else fits better; otherwise hold with the one cheapest check, or split
the decision. Instruction-only: no scripts, no network, no client-specific
features.

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
one agent can read all the evidence itself, that is cheaper and the skill is
not needed.

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
