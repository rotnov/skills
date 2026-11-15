# Session Mistakes

Learn from the mistakes your coding agents actually made. The skill reads the
local session histories of Claude Code, Codex, Grok and Cursor, keeps only the
messages you typed, and has a cheap model flag the ones that signal an agent
mistake: a correction, "I already asked", a revert, a repeated request. It
then counts the mistakes by type and harness, and turns the most frequent
ones into skill proposals backed by with-and-without evals.

## Install the skill

Paste this into your agent:

```text
Set up Session Mistakes for me: fetch and follow this guide https://github.com/rotnov/skills/blob/main/skills/session-mistakes/README.md
```

Or install it yourself:

```bash
npx --yes "skills@>=1.7.4" add rotnov/skills --skill session-mistakes -a claude-code codex --global --copy -y
```

## Requirements

- Python 3.9 or newer; the scripts use the standard library only.
- A labeling model you already have access to. By default the scripts call
  the signed-in `claude` CLI with `claude-sonnet-5-5`; `--backend api` uses
  `ANTHROPIC_API_KEY`, and `--labeler-cmd` plugs in any other model.
- Optional: `zstd`, to read Codex's compressed session archives; the signed-in
  `codex` CLI, to run skill comparisons with Codex as the agent.

## Privacy

Session histories hold personal data, secrets and client code. Parsing,
storage and counting are local. The only outbound traffic is the labeling
call: your message, your previous message and the end of the agent's reply,
with secrets and e-mail addresses stripped first. `label --dry-run` prints
exactly what would be sent and sends nothing.

Raw text is stored in `~/.local/share/session-mistakes/` (change it with
`--data-dir` or `SESSION_MISTAKES_HOME`), outside any repository. Only the
`table` and `rank` outputs are anonymized, and the anonymizer cannot know
every client or product name, so read a report before you share it.

## Use

Ask your agent, for example:

> Mine my session histories for agent mistakes and show me the table by
> harness.
>
> Take the top three mistakes and propose skills for them, with evals.

Or run the scripts yourself from the skill's `scripts/` folder:

```bash
python3 mistakes.py collect
python3 mistakes.py label --dry-run
python3 mistakes.py label --max-messages 500
python3 mistakes.py table --out ./report
python3 mistakes.py rank
python3 proposals.py coverage --category false-claim --installed
python3 proposals.py compare --skill ../../my-skill --scenarios scenarios.json
```

Runs are incremental: unchanged transcript files are not parsed again and a
labeled message is never sent twice.

For agent instructions, see [SKILL.md](SKILL.md).

## Setup guide for agents

You are an AI agent asked to set up this skill.

1. Install it globally for Claude Code and Codex with the skills CLI 1.7.4 or newer:
   `npx --yes "skills@>=1.7.4" add rotnov/skills --skill session-mistakes -a claude-code codex --global --copy -y`.
2. Read the installed [SKILL.md](SKILL.md).
3. Check the requirements: `python3 --version` (3.9 or newer), and that a
   labeling model is reachable (`claude --version` and a signed-in account, or
   another model the user names). Report anything missing with the exact fix;
   do not buy or sign up for anything.
4. Run `python3 <skill directory>/scripts/mistakes.py collect` and tell the
   user how many human messages were found per harness.
5. Tell the user it is ready and give one example request from the Use section.
