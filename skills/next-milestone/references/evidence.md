# Evidence rules

Read this when judging whether a milestone criterion is met, when ticking a
criterion, or when recording a not-met, amended, or withdrawn criterion.

## Description format

A milestone description holds a scope line and a numbered acceptance
checklist written as a Markdown task list, followed by dated notes:

```markdown
Scope: Parser handles the full 3.x grammar with stable diagnostics.

Accept:
1. - [x] Round-trip test suite passes on Linux and macOS. Evidence:
   https://github.com/OWNER/REPO/actions/runs/123456789 (2026-01-15).
2. - [ ] Startup latency at most 200 ms, median of 5 cold runs on the CI
   runner.

Notes:
- 2026-01-15: criterion 2 not met. Measured 340 ms; profiling attributes
  120 ms to eager plugin loading (#42).
```

Keep criterion numbers stable; never renumber after work has started.

## What counts as met

A criterion is met only when:

- it is ticked and cites concrete evidence inline: a CI run URL, a merged pull
  request or commit, a named test, or a measurement with its method, inputs,
  and result;
- the evidence still holds on the current default branch: the commit or pull
  request is merged and not reverted, the test still exists and passes in the
  latest relevant CI run, the measurement reproduces or the latest run of the
  same method still meets the threshold, and dependent issues are closed as
  completed, not reopened.

A tick without evidence, or with evidence that no longer holds, is not met:
untick it and add a dated note explaining why. A milestone is met only when
every criterion is met or explicitly recorded as not met under the rules
below.

Check cheap evidence first: `git merge-base --is-ancestor` for commits, the
tracker for pull-request and issue state, CI runs by id, and tests by search.
Re-run an expensive measurement only when the recorded result is missing,
stale relative to relevant code changes, or disputed.

## Not met, amended, or withdrawn

Never edit criterion text silently. Add a dated note under `Notes:` that keeps
the original text visible and states:

- not met: the measured value, the target, the measurement method, and the
  analysis of the gap;
- amended: the new wording, why the original was wrong (a measurement, an
  upstream limitation, a superseding decision), and the evidence;
- withdrawn: why it no longer applies, with evidence, and where any deferred
  work now lives (an issue in a later milestone).

Mark an amended or withdrawn criterion in place (for example append
"(amended 2026-01-15, see Notes)") rather than deleting it. Moving a
threshold only because the current result misses it is not a valid
amendment; record "not met" instead.

A milestone may close with a criterion recorded as not met only when the note
states why closing is still right (for example the gap moved to a named issue
in the next milestone). Otherwise keep working it.

## When the description gets too long

Keep the description to the scope, the checklist, and recent notes. When
adding a note would push it past about 2,000 characters, or the API rejects
the update, open one tracking issue titled `Milestone notes: <milestone
title>`, assign it to the milestone, pin it if the repository allows, move the
existing notes into its body, and add new notes as dated comments. Replace the
description's `Notes:` section with a single line linking the issue. Tick
evidence stays inline on the checklist either way.
