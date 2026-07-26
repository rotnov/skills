# rotnov/skills

Portable [Agent Skills](https://agentskills.io/) for evidence-driven software
engineering.

## Available skills

### `i-have-an-issue`

Research how upstream and comparable open-source projects encountered, fixed,
or failed to fix a software problem. It turns GitHub issues, discussions, pull
requests, commits, releases, source, and tests into a version-aware prevention
or diagnosis brief.

Install it with the [skills CLI](https://skills.sh/):

```bash
npx skills add rotnov/skills --skill i-have-an-issue
```

List every skill in this repository:

```bash
npx skills add rotnov/skills --list
```

Use it without installing:

```bash
npx skills use rotnov/skills@i-have-an-issue
```

### `propose-skill`

Extract a demonstrated practice from the current session, decide whether it is
specific and reusable enough to become an Agent Skill, check for duplicates,
and draft a structured proposal issue for `rotnov/skills`. Public issue writes
require an exact preview and explicit approval; raw conversation and private
material are never published.

Install it with the [skills CLI](https://skills.sh/):

```bash
npx skills add rotnov/skills --skill propose-skill
```

Use it without installing:

```bash
npx skills use rotnov/skills@propose-skill
```

## Compatibility

The canonical behavior lives in standard `SKILL.md` files and works with
Agent Skills-compatible clients, including Codex and Claude Code. Optional
client metadata does not change the behavior contract.

`i-have-an-issue` requires network access and at least one way to inspect
public source history: a native GitHub connector, `gh`, a browser, or Python 3
for its standard-library search fallback. Authentication is optional for
public searches but raises GitHub API limits.

`propose-skill` can draft locally. Duplicate search requires public GitHub
read access; publishing requires an authenticated GitHub tool and explicit
approval of the exact public payload.

## Evolution

This repository supports project-wide and per-skill
[iEvo](https://github.com/ievo-ai/skills) overlays for authoring. Local lessons
live under `.ievo/evolution/`; repository instructions load them while a skill
is being maintained. Published skill bodies deliberately contain no hidden
overlay loader or machine-local path.

Run `ievo:evo` with an explicit skill target when a correction should persist.
Local overlays do not post or push anything upstream automatically. Promote a
lesson into the public skill only after reviewing its evidence, tests, and
cross-client behavior.

## Development

Validate the repository:

```bash
python3 scripts/validate_skills.py
python3 -m unittest discover -s tests -v
./scripts/check-agent-skills-spec.sh
./scripts/check-skills-cli.sh
pre-commit run --all-files
```

Skills follow the
[Agent Skills specification](https://agentskills.io/specification).
