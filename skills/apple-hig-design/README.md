# Apple HIG Design

Design or review an interface using Apple Human Interface Guidelines while
keeping platform recommendations, web standards, and product choices distinct.
The skill covers layout, typography, accessibility, appearance modes, and
reusable component systems without requiring a particular design editor.

## Install the skill

```text
Set up Apple HIG Design for me: fetch and follow this guide https://github.com/rotnov/skills/blob/main/skills/apple-hig-design/README.md
```

Or install it yourself:

```bash
npx --yes "skills@>=1.7.4" add rotnov/skills --skill apple-hig-design -a claude-code codex --global --copy -y
```

## Requirements

- An agent that can read the supplied design, screenshots, or source code.
- Network access through any browser or HTTP reader to verify current guidance.
  With no network, the skill can provide a provisional review and identify
  guidance it could not reverify.
- An appropriate editor or application tool for requested edits and live
  verification. Without one, it can still produce a review or design brief.

No API key, Figma connector, Apple SDK, or sibling skill is required. Actual
edits need access to the user's chosen design or code environment.

## Use

Example requests:

```text
Use apple-hig-design to review these desktop screens. Keep our brand font and identify which recommendations come from Apple and which are product choices.
```

```text
Build a light/dark design-system foundation for this web app using Apple HIG principles. Reuse our existing components and document the missing states.
```

```text
Audit this toolbar's spacing, labels, and keyboard behavior. Only change the toolbar; do not redesign the application.
```

The optional desktop numbers in the reference are examples, not Apple rules.
The skill does not certify HIG or accessibility conformance. Its portable
[evaluation scenarios](evals/evals.json) are unscored; no benchmark result is
claimed.

For the agent workflow, see [SKILL.md](SKILL.md).

## Setup guide for agents

1. Install globally for Claude Code and Codex with the newest skills CLI at
   or above the tested minimum:
   `npx --yes "skills@>=1.7.4" add rotnov/skills --skill apple-hig-design -a claude-code codex --global --copy -y`.
2. Read the installed [SKILL.md](SKILL.md).
3. Check the requirements above. Report unavailable source or editor access
   without claiming that an edit or live verification has been performed.
4. Tell the user the skill is ready and give one example from the Use section.
