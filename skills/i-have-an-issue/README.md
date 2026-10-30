# I Have an Issue

Research how upstream and comparable open-source projects encountered, fixed,
or failed to fix a software problem. The skill turns GitHub issues,
discussions, pull requests, commits, releases, source, and tests into a
version-aware prevention or diagnosis brief.

## Install the skill

Give your agent this request:

> Install the i-have-an-issue skill from
> <https://github.com/rotnov/skills/tree/main/skills/i-have-an-issue> for this
> client, globally for Claude Code and Codex with the skills CLI 1.7.4 or newer (skills@>=1.7.4),
> then read its SKILL.md.

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
