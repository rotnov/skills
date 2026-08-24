# Portable active-learning skill distillation

**Date:** 2026-08-24
**Status:** approved for implementation

## Problem

The current active-learning package combines a useful, deterministic recording engine
with assumptions from the repository where it originated. Those assumptions name
specialized helper workflows, repository hierarchies, installation layouts, and task
handoff mechanics that are not guaranteed to exist for a public skill consumer. The
package also includes evaluation results produced for that older contract, so they no
longer constitute valid evidence after portability changes.

The public package needs a self-contained behavior contract that works in both Codex
and Claude Code without requiring any other skill. It should retain the recorder's
safety properties and make optional use of project-provided authoring or import tools
without depending on them.

## Decision

Keep the Git-local recorder and distill the instruction layer into a portable Agent
Skills workflow:

```text
start -> observe -> end -> extract -> resolve owner -> approve -> update -> validate
```

The public user-facing operations are:

- `active-learning start` — open one recording for the current Git worktree;
- `active-learning resume` — explicitly reconnect a later task to an open recording;
- `active-learning end` — claim a stable snapshot, evaluate lessons, and finish only
  after approved updates are validated.

Recorder subcommands remain internal implementation details.

## Package structure

The distilled package contains:

- `SKILL.md` — the portable lifecycle, lesson filter, owner resolution, approval gate,
  and validation contract;
- `scripts/recording.py` — deterministic Git-local state management, unchanged except
  for changes required by verified portability defects;
- `evals/evals.json` — a small set of current, portable behavioral scenarios;
- repository tests for the instruction contract and recorder lifecycle.

Historical benchmark output and prior iteration artifacts are removed. They measured a
different contract and must not be presented as evidence for the distilled version.

## Recording lifecycle

The recorder stores ephemeral state beneath the current worktree's resolved Git
metadata. It never writes tracked project files. One recording may be open per
worktree, and a duplicate start fails without replacing state.

The skill records only concise, agent-authored checkpoints for reusable decisions,
corrections, failures and verified recoveries, and repeatable procedures. It never
copies transcripts, secrets, source bodies, or raw external content. Safe tokens are
transported as logical argv values through a structured command API or a correctly
quoted shell-string fallback.

Every invocation uses `uv run --no-project` and resolves `recording.py` relative to the
loaded `SKILL.md`. This prevents host-project dependency resolution and avoids assuming
an installation directory.

Cross-task continuation is explicit because an installed skill cannot guarantee an
always-on startup hook. A user invokes `active-learning resume` in the later task; the
skill checks recorder status and resumes only an open recording. An end snapshot has a
separate bearer-claim lifecycle and is never recovered silently.

## Lesson and owner resolution

Each retained lesson is evaluated independently. Invocation history is evidence, not
ownership. The agent compares a lesson's trigger, responsibility, side effects, and
boundary against:

1. editable skills in the current Git repository;
2. skills owned by another identifiable Git repository;
3. installed user or plugin skills as discovery-only sources.

The canonical owner path comes from repository evidence and routing, never from the
path of a loaded or installed copy. Similar names do not establish ownership.

The dispositions are:

| Result | Disposition |
| --- | --- |
| One editable owner in the current repository | Propose an in-place canonical update. |
| Owner in another repository | Propose an update in that repository's own checkout and keep the source recording resumable until verification. |
| Installed-only owner with authoritative origin | Propose bringing the pinned source into the project, preserving provenance, then adapting it. |
| Installed-only owner without authoritative origin | Ask for the source repository and path; do not edit the cache or invent a replacement. |
| Several plausible owners | Ask the user to select or refine the boundary. |
| No owner | Propose a new Agent Skills-compatible project skill. |
| No reusable lesson | Make no skill change. |

If the project documents an import or skill-authoring workflow, the agent may use it.
Otherwise it performs the approved work directly according to the Agent Skills
specification and the repository's own validation rules. Optional helpers never become
runtime dependencies of this skill.

## Authorization and failure behavior

Before any durable mutation, present:

| Lesson | Evidence | Owner | Disposition | Proposed change |
| --- | --- | --- | --- | --- |

Wait for explicit approval. Approval covers only the listed changes and does not grant
permission for unrelated writes, external publication, or repository creation.

Missing provenance, unavailable capabilities, ambiguous ownership, failed tests, and
failed validation leave the snapshot resumable. The workflow never weakens validation,
edits installed caches, silently creates a competing skill, or closes a changed
recording revision.

## Validation design

The public repository must test the recorder rather than relying on the source
repository's test suite. Tests use the Python standard library and cover:

- worktree isolation and absence of tracked recording files;
- duplicate start and hostile-token rejection;
- malformed, stale, and oversized state;
- symlink and directory-swap defenses;
- prepare, resume, adopt, reopen, and compare-and-close behavior;
- claim and revision mismatches;
- behavior inside an incompatible host Python project;
- instruction portability for both argv and shell-string command tools;
- absence of project-specific helper, hierarchy, and private-repository assumptions
  from every published package file.

The final gate is the complete repository unit suite, the pinned Agent Skills reference
validator, the pinned skills CLI copy-install smoke for Codex and Claude Code, the full
pre-commit suite, GitHub Actions, and a review of the final pull-request head.

## Non-goals

- Training or modifying an underlying model.
- Automatic activation in a later task without a user request.
- Defining a universal repository layout for project skills.
- Editing user or plugin caches as durable project state.
- Requiring a particular import, authoring, review, or task-handoff helper.
