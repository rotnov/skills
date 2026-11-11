# PR Retro

Run a post-merge retrospective of one merged pull request across its whole
chain: the brief or issue, the author agent's transcripts (subagents and child
sessions included), every review round, AI review bots, CI runs, and human
steers. The skill rebuilds a timeline from real timestamps, lists the errors
made along the way (self-corrected, pointed out, or found by the retro),
counts the avoidable, generalisable ones as pattern occurrences, and, once a
pattern recurs or one serious error escapes review, proposes the smallest
change in the skill or project instruction file that owns it.

## Install the skill

Paste this into your agent:

```text
Set up PR Retro for me: fetch and follow this guide https://github.com/rotnov/skills/blob/main/skills/pr-retro/README.md
```

Or install it yourself:

```bash
npx --yes "skills@>=1.7.4" add rotnov/skills --skill pr-retro -a claude-code codex --global --copy -y
```

## Requirements

Git and an authenticated `gh` CLI (or an equivalent GitHub connector) with read
access to the repository. Claude Code transcripts (under `~/.claude/projects/`)
and Codex transcripts (under `~/.codex/sessions/`) are read when they exist on
the machine; without them the retro runs from GitHub alone and says which
sources it could not locate. Pattern files default to `docs/retro/` in the
project, or follow the project's existing convention.

The skill is read-only on every source, never copies transcript text, secrets,
environment values, host names, or local paths into its outputs, and edits a
skill or instruction file only after you approve the exact diff.

## Use

Ask your agent, for example:

> Run a retro of pull request #42 that merged this morning.

For agent instructions, see [SKILL.md](SKILL.md).

## Setup guide for agents

You are an AI agent asked to set up this skill.

1. Install it globally for Claude Code and Codex with the skills CLI 1.7.4 or newer:
   `npx --yes "skills@>=1.7.4" add rotnov/skills --skill pr-retro -a claude-code codex --global --copy -y`.
2. Read the installed [SKILL.md](SKILL.md).
3. Check the requirements above (`gh auth status`, and whether transcript
   folders exist) and report anything missing with the exact fix.
4. Tell the user it is ready and give one example request from the Use section.
