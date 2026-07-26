# Skill proposal issue format

Use this format for a new issue in `rotnov/skills`. Omit optional proposed-file
blocks when they add noise, but keep every required heading.

## Title

```text
[Skill proposal] <skill-name>: <verifiable outcome>
```

Use a proposed kebab-case skill name. Describe the outcome, not the session or
the implementation technology.

## Body

````markdown
## Summary

<!-- What reusable capability should exist, and who benefits? -->

## Session evidence

<!-- Required. A sanitized summary of the demonstrated practice or correction.
Do not include raw conversation, private code, identifying paths, or secrets. -->

## Why this should be a skill

<!-- Explain recurrence, non-obvious value, portability, and why project
instructions or ordinary documentation are insufficient. -->

## Trigger contract

### Use when

- `<example user request>`

### Do not use when

- `<nearby request that belongs elsewhere>`

## Proposed workflow

1. `<first observable step>`
2. `<next observable step>`

### Stop conditions

- `<condition that ends or blocks the workflow safely>`

## Output contract

<!-- Describe the result, including any files, links, or external writes. -->

## Safety and privacy

<!-- State trust boundaries, sanitization, consent gates, and destructive or
public side effects. Write "No external writes" when applicable. -->

## Proposed bundle

```text
skills/<skill-name>/
├── SKILL.md
├── agents/
│   └── openai.yaml
└── evals/
    └── evals.json
```

<!-- Add references/, scripts/, or assets/ only when justified. Never attach a
ZIP as the primary review artifact. -->

<details>
<summary>Optional short proposed files</summary>

```markdown
<!-- Include small text files only when reviewers need exact wording. -->
```

</details>

## Evaluation scenarios

| Scenario | Prompt or setup | Expected behavior |
| --- | --- | --- |
| Success | `<realistic request>` | `<observable outcome>` |
| Failure/edge | `<missing or conflicting input>` | `<safe handling>` |
| Non-trigger | `<nearby but out-of-scope request>` | `<skill stays inactive>` |
| Side-effect gate | `<write-capable request, if relevant>` | `<preview and consent behavior>` |

## Dependencies and portability

<!-- State network/tool requirements and expected Codex and Claude Code paths.
Identify any capability that needs a documented fallback. -->

## Acceptance criteria

- [ ] The skill passes the official Agent Skills validator.
- [ ] The skill is discoverable and installable with the pinned skills CLI.
- [ ] The primary success, failure, non-trigger, and safety paths are tested.
- [ ] Codex and Claude Code expose equivalent behavior or a safe fallback.
- [ ] Documentation describes current behavior and dependencies.

## Provenance

Derived from a sanitized practice observed in the current user-authorized
session. No raw conversation, credentials, private source, or identifying local
paths are included.
````

## Inline-content boundary

Keep the issue independently understandable and reviewable. Include short text
files inline when exact wording is the proposal. If the prospective skill needs
substantial code, generated files, binaries, or many long resources, include
only the file tree, interfaces, representative excerpts, and testing strategy.
Create the complete implementation in a separate pull request after the
proposal is accepted.

An archive is not a review substitute: it hides diffs, complicates security
inspection, and cannot be created as a normal GitHub issue attachment by common
CLI/API workflows. Do not publish a gist, release artifact, or third-party
upload as a workaround without a separate explicit user request and approval.
