# Root-cause tracing and the change gate

Read this when tracing a generalisable finding in step 3, or when a pattern
qualifies for a change in step 4.

## Trace to the earliest preventable point

Start at the symptom, find its immediate cause, then keep asking what produced
that behavior, re-reading the cited code, instruction, brief, review, or tool
output at every hop. Do not stop where the problem surfaced, at the reviewer
who missed it, or at the agent that followed an earlier instruction. Stop at
the earliest point where a different enforceable decision would have prevented
the error.

Report the symptom, the backward chain, the owner of the earliest point, and a
confidence. Recommend one owner only at medium or high confidence; if two
origins are equally plausible, show both and let the user choose. Never turn
an uncertain diagnosis into a rule.

## Pick the owner

| Owner | Typical location |
| ----- | ---------------- |
| Project-wide rule | the project's instruction file (`AGENTS.md`, `CLAUDE.md`, or equivalent) |
| A workflow step | the topic skill that runs that step |
| A mechanical check | pre-commit hooks, scripts, lint, tests, CI workflows |
| A brief or prompt template | the skill or file that writes the brief |
| An external tool | none locally; record and report upstream only with user approval |
| Discipline or an outside constraint | none; informational only, no edit |

Prefer an existing topic skill over growing the project-wide file, and a
mechanical check over prose.

## Audit existing coverage

Search the owner and its neighbours for the topic, then answer:

1. Does a current rule already prohibit the error?
2. Is the wording ambiguous, contradictory, duplicated, or on the wrong
   trigger?
3. Would a stricter rule conflict with another workflow?
4. Can compliance be checked mechanically or by a precise review test?

Choose one outcome: **add** (nothing covers it), **replace** (the rule is
incomplete or ambiguous), **consolidate** (overlapping rules can drift), or
**no edit** (the rule was adequate and merely ignored). Replacing or
consolidating keeps every existing requirement.

## Draft the lesson

Make it general enough to stop the pattern, actionable without the original
conversation, testable, and minimal. Keep canonical commands, paths, and skill
names that are part of the reusable contract; drop incident-only ids, and never
carry personal data, credentials, or internal host names into it.

## Gate

Before editing, show the owner of the target file (the maintainer, or the user
who invoked the skill):

- the trace, confidence, and owner;
- existing overlap or contradiction;
- the exact proposed diff and the chosen outcome;
- why weaker wording would not prevent recurrence;
- a repository-wide search for other instances and what it found.

Edit only after explicit approval of the diff; approval of the lesson alone
does not authorize deleting or merging existing rules. Then ship it through the
project's normal review and pull-request flow, naming the pattern key and
setting the pattern's status to `resolved` with the pull request's link.
