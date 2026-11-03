# I Have an Issue

Research how upstream and comparable open-source projects encountered, fixed,
or failed to fix a software problem. The skill turns GitHub issues,
discussions, pull requests, commits, releases, source, and tests into a
version-aware prevention or diagnosis brief.

## Install the skill

Paste this into your agent:

> Set up I Have an Issue for me: fetch and follow this guide
> <https://github.com/rotnov/skills/tree/main/skills/i-have-an-issue>

Or install it yourself:

```bash
npx --yes "skills@>=1.7.4" add rotnov/skills --skill i-have-an-issue -a claude-code codex --global --copy -y
```

## Requirements

Network access and at least one way to inspect public source history: a native
GitHub connector, `gh`, a browser, or Python 3 for the bundled standard-library
search fallback. Authentication is optional for public searches but raises
GitHub API limits.

## Use

Ask your agent, for example:

> Has anyone hit this error upstream? Find prior art and the gotchas.

For agent instructions, see [SKILL.md](SKILL.md).

## Setup guide for agents

You are an AI agent asked to set up this skill.

1. Install it globally for Claude Code and Codex with the skills CLI 1.7.4 or newer:
   `npx --yes "skills@>=1.7.4" add rotnov/skills --skill i-have-an-issue -a claude-code codex --global --copy -y`.
2. Read the installed [SKILL.md](SKILL.md).
3. Check the requirements above and report anything missing with the exact fix.
4. Tell the user it is ready and give one example request from the Use section.
