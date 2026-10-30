# Next Milestone

Plan and drive a project's roadmap as GitHub milestones. The skill picks the
first open milestone whose numbered acceptance checklist is not met with
verified evidence, adopts it as the standing goal, decomposes the gaps into
milestone issues, ticks criteria only with inline evidence, records honest
dated notes for unmet or amended criteria, and closes the milestone out.
Releasing stays the project's own procedure.

## Install the skill

Give your agent this request:

> Install the next-milestone skill from
> <https://github.com/rotnov/skills/tree/main/skills/next-milestone> for this
> client, globally for Claude Code and Codex with the skills CLI 1.7.4 or newer (skills@>=1.7.4),
> then read its SKILL.md.

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
