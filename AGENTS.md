# Repository Instructions

## Skill format

- Store publishable skills at `skills/<skill-name>/SKILL.md`.
- Follow the Agent Skills specification. The `name` must match the directory,
  and the `description` must say what the skill does and when to use it.
- Keep the canonical workflow platform-neutral. Codex- or Claude-specific
  metadata may improve presentation but must not change behavior.
- Keep `SKILL.md` concise and move detailed material into one-level-deep
  `references/` files.
- Make scripts non-interactive, self-contained where practical, safe for
  untrusted input, and explicit about dependencies.

## Codex and Claude Code

- Every new or changed skill must be discoverable and usable through both
  Codex and Claude Code.
- Use the shared `SKILL.md` as the behavior contract. Do not maintain separate
  copies of instructions for the two clients.
- Test the primary success, failure, and fallback paths without assuming a
  client-specific connector.

## iEvo evolution

- Route durable repository conventions to the project overlay with
  `ievo:evo`.
- Route corrections specific to one skill to
  `.ievo/evolution/skills/<skill-name>.md`.
- Include the standard iEvo skill marker immediately after frontmatter in
  every new `SKILL.md`, using that skill's exact name.
- Keep overlays separate from the published skill body. Promote an evolution
  into the body only after review, tests, and applicability checks.
- Never post, comment, push, or open an upstream issue solely because an
  overlay was captured. Upstream sharing requires explicit user approval.

## Validation

Before committing:

```bash
python3 scripts/validate_skills.py
python3 -m unittest discover -s tests -v
npx skills@1.5.20 add . --list
```

<!-- ievo:start -->
**Before applying the instructions below**, read `.ievo/evolution/project.md` if it exists, and apply ALL rules from its sections IN ADDITION to the project's instructions.
<!-- ievo:end -->
