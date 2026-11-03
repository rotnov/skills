# GitHub access

Read this when listing, ordering, creating, editing, or closing milestones, or
when listing a milestone's issues.

## Setup

Resolve the repository from the `origin` remote:

```bash
gh repo view --json nameWithOwner -q .nameWithOwner
```

Use the result as `OWNER/REPO` below. An equivalent GitHub connector may run
the same REST calls.

## Ordering

```bash
gh api --paginate "repos/OWNER/REPO/milestones?state=open&per_page=100" \
  --jq '.[] | [.number, (.due_on // ""), .title] | @tsv'
```

Order open milestones by:

1. due date ascending; milestones with a due date come before those without;
2. then semantic version parsed from the title (`v1.2` or `v1.2.3`, compared
   numerically, missing patch as 0);
3. then titles with no version, by milestone number.

If due dates contradict version order (v0.4 due before v0.3), follow the due
date and report the inconsistency. Closed milestones are history: do not
reconsider them unless current work shows a ticked criterion's evidence no
longer holds; then add a dated note to that milestone and open an issue in
the active milestone for the regression.

## Reading and editing a description

Read:

```bash
gh api repos/OWNER/REPO/milestones/NUMBER --jq .description
```

The update replaces the whole description, and the milestone API offers no
conditional update (no ETag or `If-Match` precondition), so a maintainer's
concurrent edit can be overwritten. Guard against it:

1. Keep the exact description text your edit is based on.
2. Immediately before writing, fetch the description again and compare it with
   that base text.
3. If it changed, re-apply your edit on top of the new text and compare again;
   if your edit conflicts with the new text, stop and report the conflict
   instead of writing.
4. Write the full new description from a file, never by interpolating
   untrusted text into a shell string:

```bash
gh api -X PATCH repos/OWNER/REPO/milestones/NUMBER -F description=@desc.md
```

Re-read after writing only to verify the update landed; this does not protect
against a concurrent edit, and a small race window remains between the last
fetch and the write.

## Creating

```bash
gh api -X POST repos/OWNER/REPO/milestones -f title='v0.4 — Short name' \
  -F description=@desc.md
```

Add `-f due_on=YYYY-MM-DDT00:00:00Z` only when the project uses due dates.

## Issues

```bash
gh issue list --milestone 'v0.4 — Short name' --state all --limit 500 \
  --json number,title,state
gh issue create --title 'Title' --body-file body.md --milestone 'v0.4 — Short name'
gh issue edit NUMBER --milestone 'v0.5 — Next name'
```

## Closing

```bash
gh api -X PATCH repos/OWNER/REPO/milestones/NUMBER -f state=closed
```

## When GitHub is unavailable

If `gh` is missing, unauthenticated, lacks permission, hits a rate limit that
does not clear, or the remote is not on GitHub: stop. Report the step that
could not run, the error, and the command a maintainer can run. Do not invent
milestone numbers, URLs, or criteria state, and do not create a local roadmap
file as a substitute.
