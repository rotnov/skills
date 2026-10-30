# Learning

Run a bounded learning loop over a real work session. The skill records
concise checkpoints outside the working tree, extracts reusable lessons,
resolves each lesson to its canonical project skill, and applies only
user-approved changes. Use `learning flush` to apply accumulated lessons and
keep the recording open, or `learning end` to apply them and stop recording.

## Install the skill

Give your agent this request:

> Install the learning skill from
> <https://github.com/rotnov/skills/tree/main/skills/learning> for this client,
> globally for Claude Code and Codex with the pinned skills@1.5.20 CLI,
> then read its SKILL.md.

Or install it yourself:

```bash
npx --yes skills@1.5.20 add rotnov/skills --skill learning -a claude-code codex --global --copy -y
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
