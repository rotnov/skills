# Learning

Run a bounded learning loop over a real work session. The skill records
concise checkpoints outside the working tree, extracts reusable lessons,
resolves each lesson to its canonical project skill, and applies only
user-approved changes. Use `learning flush` to apply accumulated lessons and
keep the recording open, or `learning end` to apply them and stop recording.

## Install the skill

Paste this into your agent:

> Set up Learning for me: fetch and follow this guide
> <https://github.com/rotnov/skills/tree/main/skills/learning>

Or install it yourself:

```bash
npx --yes "skills@>=1.7.4" add rotnov/skills --skill learning -a claude-code codex --global --copy -y
```

## Requirements

Git, `uv`, and a platform with safe no-follow directory-descriptor operations
for recording mutations. Recorder commands select Python 3.12 through `uv`,
isolated from the host project's dependency metadata.

## Use

Ask your agent, for example:

> learning start

Continue in a later task with `learning resume`. For agent instructions, see
[SKILL.md](SKILL.md).

## Setup guide for agents

You are an AI agent asked to set up this skill.

1. Install it globally for Claude Code and Codex with the skills CLI 1.7.4 or newer:
   `npx --yes "skills@>=1.7.4" add rotnov/skills --skill learning -a claude-code codex --global --copy -y`.
2. Read the installed [SKILL.md](SKILL.md).
3. Check the requirements above and report anything missing with the exact fix.
4. Tell the user it is ready and give one example request from the Use section.
