# Next Milestone

Plan and drive a project's roadmap as GitHub milestones. The skill picks the
first open milestone whose numbered acceptance checklist is not met with
verified evidence, adopts it as the standing goal, decomposes the gaps into
milestone issues, ticks criteria only with inline evidence, records honest
dated notes for unmet or amended criteria, and closes the milestone out.
Releasing stays the project's own procedure.

## Install the skill

Paste this into your agent:

> Set up Next Milestone for me: fetch and follow this guide
> <https://github.com/rotnov/skills/tree/main/skills/next-milestone>

Or install it yourself:

```bash
npx --yes "skills@>=1.7.4" add rotnov/skills --skill next-milestone -a claude-code codex --global --copy -y
```

## Requirements

Git, network access, and an authenticated `gh` CLI (or an equivalent GitHub
connector) with permission to edit milestones and issues. Without GitHub
access the skill stops and reports instead of falling back to a local roadmap
file.

## Use

Ask your agent, for example:

> What milestone is next? Drive it to an evidence-backed close-out.

For agent instructions, see [SKILL.md](SKILL.md).

## Setup guide for agents

You are an AI agent asked to set up this skill.

1. Install it globally for Claude Code and Codex with the skills CLI 1.7.4 or newer:
   `npx --yes "skills@>=1.7.4" add rotnov/skills --skill next-milestone -a claude-code codex --global --copy -y`.
2. Read the installed [SKILL.md](SKILL.md).
3. Check the requirements above and report anything missing with the exact fix.
4. Tell the user it is ready and give one example request from the Use section.
