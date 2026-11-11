# Pattern files

Read this when searching, creating, or updating pattern files in step 4, or
when batching entries in step 5.

## Location

Use the project's existing retro folder if it has one (search for a folder of
pattern files with a "What goes wrong" heading, or a convention in the
project's instruction file). Otherwise use `docs/retro/`. A user-named folder
wins. The files count occurrences so the guard can decide when a change is
due; they are not a knowledge base, and the lesson itself goes into the skill
or file that owns the topic.

## Pattern key

The file name is the key: `<folder>/<pattern-key>.md`.

- kebab-case, built as `<area>-<error-class>-<trigger>`, 3 to 6 words;
- stable and abstract: no names of people or sessions, no paths, hosts,
  pull-request or issue numbers, dates, or product feature names;
- `<area>` is one of `brief`, `docs`, `skill`, `review`, `ci`, `git`, `code`,
  `test`, `deploy`, or a project-defined area if the folder already uses one;
- reuse an existing key whenever its "What goes wrong" describes the same error
  and trigger, even if you would word it differently.

Synthetic examples:

- `ci-rerun-without-root-cause`: a failed check is rerun until it passes and
  the flake is never traced;
- `review-ready-claimed-while-behind`: a pull request is called merge-ready
  while its branch is behind the base;
- `brief-omits-known-constraint`: a subagent brief leaves out a constraint the
  parent already knew.

Before creating a file, list the folder and search it:

```bash
ls <folder>/
grep -il "<area>\|<error-class>\|<trigger words>" <folder>/*.md
```

## File format

```markdown
# <pattern-key>

Status: open | resolved (<change pull request link>)

## What goes wrong

<Two or three abstract sentences: the error, the trigger, and where in the workflow it happens.>

## Occurrences

- <YYYY-MM-DD>, <pull request link>: <rounds> extra review rounds, <minutes> min to fix, <n> CI reruns;
  evidence <review or comment URL>, <session id> #<line index>.
```

- One occurrence entry per pull request, dated by its merge (UTC).
- Evidence is a reference only: never transcript text, hosts, secrets,
  environment values, local paths, or personal data. If the project has a
  check for such content in published docs, run it on this folder too.

## Status field

The guard, including how a resolved pattern reopens, is defined once in
`SKILL.md` step 4. The file only records its outcome:

- the change pull request names the key and sets the status to `resolved` with
  its own link; the file and its entries stay, for history;
- an entry added after a resolving change notes that the pattern was resolved;
- reopening sets `Status: open` and keeps the resolved link in the history.

## Optional batching

When the project wants fewer pull requests:

- collect the day's entries on one branch, `retro/<YYYY-MM-DD>` (UTC), cut from
  the default branch, and open its pull request only if there are entries;
- keep one open batch at a time: once its review is requested, the branch takes
  only fixes for review findings;
- later entries wait until that batch merges, then go on
  `retro/<YYYY-MM-DD>-2` (then `-3`), so two batches never edit the same files;
  until then, hold them in the report;
- a pattern-resolving change is always its own pull request, not part of a
  batch.

Without batching, add the entries in a small pull request per retro, or leave
them uncommitted for the user if the project has no retro folder yet and the
user has not asked for one.
