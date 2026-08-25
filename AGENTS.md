# Repository Instructions

## Skill format

- Store publishable skills at `skills/<skill-name>/SKILL.md`.
- Treat the current
  [Agent Skills specification](https://agentskills.io/specification) as the
  normative format contract. The `name` must match the directory, and the
  `description` must say what the skill does and when to use it.
- Treat [skills.sh](https://skills.sh/) and the pinned `skills` CLI as the
  publication and installation compatibility target. A skill is incomplete
  until the CLI can discover and copy-install it for both Codex and Claude
  Code from a clean temporary project.
- Keep the canonical workflow platform-neutral. Codex- or Claude-specific
  metadata may improve presentation but must not change behavior.
- Keep `SKILL.md` concise and move detailed material into one-level-deep
  `references/` files.
- Make scripts non-interactive, self-contained where practical, safe for
  untrusted input, and explicit about dependencies.
- Do not merge a skill that only passes a repository-local parser. It must also
  pass the pinned official `agentskills/agentskills` `skills-ref` validator and
  the pinned skills.sh CLI smoke test.
- Keep those same pinned gates in `.pre-commit-config.yaml`. Do not replace the
  immutable `skills-ref` revision or `skills@1.5.20` CLI version with a movable
  branch, tag, or unversioned package.

## Codex and Claude Code

- Every new or changed skill must be discoverable and usable through both
  Codex and Claude Code.
- Use the shared `SKILL.md` as the behavior contract. Do not maintain separate
  copies of instructions for the two clients.
- Test the primary success, failure, and fallback paths without assuming a
  client-specific connector.

## Token efficiency and skill independence

- Keep `description` as a short routing contract: say only what the skill does
  and when to use it. Put workflow steps, feature inventories, and edge cases
  in the body or references instead of frontmatter.
- Keep `SKILL.md` limited to the core workflow, decisions, and reference
  routing needed on every invocation. Move detailed guidance into
  one-level-deep `references/` files and state the exact condition for reading
  each file. Never instruct an agent to preload every reference.
- Do not duplicate normative specifications, generic tool documentation, or
  the same guidance across the body and references. Link to the authoritative
  source or keep one canonical local explanation.
- Each publishable skill must remain independently installable and runnable
  when only its own directory is copy-installed. Do not depend at runtime on
  sibling skills, repository-root files, `.agents/`, `.claude/`, or local
  evolution overlays.
- Keep every required script, reference, and asset inside the skill directory.
  Declare external executables and network requirements explicitly, and make
  failure or fallback behavior self-contained.
- Smoke-test every skill as an individual clean copy-install for both Codex
  and Claude Code. Installing all repository skills together is not evidence
  of independence.

## Pull requests and pre-commit

- Merge every repository change through a pull request. Never push directly to
  the remote default branch.
- Run `pre-commit run --all-files` before pushing and again after resolving
  conflicts or changing the pull-request head.
- Do not use `--no-verify`, `SKIP`, hook deletion, or a local configuration
  override to bypass a failing hook. Fix the cause and rerun the complete hook
  set.
- Do not merge while pre-commit or any required pull-request check is missing,
  skipped, cancelled, pending, or failing.

## iEvo evolution

- Route durable repository conventions to the project overlay with
  `ievo:evo`.
- Route corrections specific to one skill to
  `.ievo/evolution/skills/<skill-name>.md`.
- Before creating or changing `skills/<skill-name>/`, read the project overlay
  and that skill's overlay when they exist.
- Never embed iEvo loader directives, hidden instructions, or local
  `.ievo/evolution/` paths in a publishable `SKILL.md`. Evolution overlays are
  authoring inputs, not part of the installed skill's runtime contract.
- Keep overlays separate from the published skill body. Promote an evolution
  into the body only after review, tests, and applicability checks.
- Never post, comment, push, or open an upstream issue solely because an
  overlay was captured. Upstream sharing requires explicit user approval.

## Validation

Before committing:

```bash
uv lock --check
uv sync --frozen
uv run --frozen python scripts/validate_skills.py
uv run --frozen python -m unittest discover -s tests -v
./scripts/check-agent-skills-spec.sh
./scripts/check-skills-cli.sh
uv run --frozen pre-commit run --all-files
```

<!-- ievo:start -->
**Before applying the instructions below**, read `.ievo/evolution/project.md` if it exists, and apply ALL rules from its sections IN ADDITION to the project's instructions.
<!-- ievo:end -->
