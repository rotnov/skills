# Defining a milestone

Read this when the repository has no open milestone, or when deferred work
needs a next milestone to move to.

## Exactly one ahead

Create exactly one new milestone after the last existing one. Never plan two
versions ahead.

## Deriving it

Derive the milestone from evidence, not imagination:

- open issues with no milestone;
- criteria recorded as not met or deferred in earlier milestones;
- goals stated in the README or project documentation, including a
  `ROADMAP.md` if one exists (as context only);
- known limitations documented in the tree.

Version it by the project's existing scheme: the next minor after the last
milestone or release unless the work clearly warrants another increment.

## Writing it

Create it as described in [GitHub access](github.md) with a description in
the format from [the evidence rules](evidence.md):

```markdown
Scope: One sentence on what this milestone delivers.

Accept:
1. - [ ] A verifiable criterion and how it will be checked (test, CI job, or
   measurement method and threshold).
2. - [ ] Another criterion.
```

Every criterion must be checkable under the evidence rules; reject vague items
such as "improve performance" without a method and threshold. Assign the
issues the milestone covers to it.

If the remaining open work does not justify a coherent milestone, do not pad
one. Stop with "no further milestone" and list what remains.
