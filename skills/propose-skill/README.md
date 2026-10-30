# Propose Skill

Extract a demonstrated practice from the current session, decide whether it is
specific and reusable enough to become an Agent Skill, check for duplicates,
and draft a structured proposal issue for `rotnov/skills`. Public issue writes
require an exact preview and explicit approval; raw conversation and private
material are never published.

## Install the skill

Give your agent this request:

> Install the propose-skill skill from
> <https://github.com/rotnov/skills/tree/main/skills/propose-skill> for this
> client, globally for Claude Code and Codex with the pinned skills@1.5.20 CLI,
> then read its SKILL.md.

Or install it yourself:

```bash
npx --yes skills@1.5.20 add rotnov/skills --skill propose-skill -a claude-code codex --global --copy -y
```

## Requirements

Drafting works locally. Duplicate search requires public GitHub read access;
publishing requires an authenticated GitHub tool and explicit approval of the
exact public payload.

## Use

Ask your agent, for example:

> Propose a skill from the practice we just used in this session.

For agent instructions, see [SKILL.md](SKILL.md).
