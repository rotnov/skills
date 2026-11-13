# Evidence Ledger

Run a multi-agent investigation of one uncertain question so that the final
decision rests on counted evidence instead of on how many agents agree. Each
agent reports facts with their origin, strength and setup, not verdicts; the
coordinator counts independent origins, rates every claim against the setup
the decision is about, and commits or holds accordingly. Instruction-only: no
scripts, no network, no client-specific features.

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
without them the same steps run in one pass. The skill adds one planning turn
and one merging turn to whatever the investigation itself costs.

## Use

Ask your agent, for example:

> Use three agents to find out whether upgrading fixes this bug for our
> setup, and keep an evidence ledger.

For agent instructions, see [SKILL.md](SKILL.md).

## Setup guide for agents

You are an AI agent asked to set up this skill.

1. Install it globally for Claude Code and Codex with the skills CLI 1.7.4 or newer:
   `npx --yes "skills@>=1.7.4" add rotnov/skills --skill evidence-ledger -a claude-code codex --global --copy -y`.
2. Read the installed [SKILL.md](SKILL.md).
3. Check the requirements above and report anything missing with the exact fix.
4. Tell the user it is ready and give one example request from the Use section.
