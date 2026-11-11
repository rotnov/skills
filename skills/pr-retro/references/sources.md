# Source map

Read this when locating the sources of a pull request's chain in step 1.

Placeholders: `<home>` is the user's home directory (`~` in a POSIX shell,
`%USERPROFILE%` on Windows), `<o>/<r>` the repository, `<n>` the pull-request
number, `<branch>` its head branch, `<session id>` a transcript id. Read only;
never paste what you read into any output.

## GitHub: the pull request and its rounds

```bash
gh pr view <n> -R <o>/<r> --json number,title,headRefName,baseRefName,createdAt,mergedAt,body,commits,reviews,comments,statusCheckRollup
gh api repos/<o>/<r>/pulls/<n>/reviews --paginate     # reviews, with commit_id per round
gh api repos/<o>/<r>/pulls/<n>/comments --paginate    # inline review comments
gh api repos/<o>/<r>/issues/<n>/comments --paginate   # conversation comments, bot summaries
gh api repos/<o>/<r>/issues/<n>/timeline --paginate   # commits, reviews, branch updates, cross-references
gh api repos/<o>/<r>/actions/runs -X GET -f branch=<branch> -f per_page=100 --paginate \
  --jq '.workflow_runs[] | {id, name, event, conclusion, head_sha, created_at, run_attempt, pull_requests: [.pull_requests[].number]}'
gh run view <id> -R <o>/<r> --attempt <k> --json attempt,conclusion,headSha,createdAt,jobs   # each earlier attempt
gh run view <id> -R <o>/<r> --attempt <k> --log-failed   # failed-step log of that attempt
```

- **Rounds**: one per reviewed head; pair each review's `commit_id` with the
  commits list.
- **Verdicts**: some reviewers post their verdict as a `COMMENTED` review (for
  example when the same account opens and merges); read the review body, not
  `reviewDecision` alone.
- **Review bots**: identify them by account type `Bot` or a `[bot]` login. A
  bot's findings are usually inline comments; a bot summary comment naming the
  reviewed commit is the reliable sign of which head it reviewed.
- **CI**: count runs only from the paginated call above; a capped list such as
  `gh run list --limit` silently drops older runs. A branch can serve several
  pull requests, so keep a run when its `pull_requests` names `<n>`, or else when
  its `head_sha` is one of this pull request's commits and its `created_at` falls
  between the pull request's `createdAt` and `mergedAt`. `run_attempt` above 1
  is a rerun; a failure followed by a success on the same head is a rerun or a
  flake; a new head after a failure is a fix. A merge commit from the base
  branch in the commits list is a branch update. For every
  run with `run_attempt` above 1, read attempts 1 to `run_attempt - 1` and their
  failed-step logs to tell an avoidable error (a test, lint, or build failure
  in the change) from an infrastructure flake (runner, network, or service
  outage). Cite by run id plus attempt; never paste log text.
- **Brief**: a `Refs`, `Fixes`, or `Closes` line in the body names the issue;
  read it with `gh issue view`.
- **Dates**: date a rule or change by when it reached the base branch (the
  pull request's `mergedAt` or the first-parent merge), never by commit author
  time.

## Claude Code transcripts

One JSONL file per session under
`<home>/.claude/projects/<encoded path>/<session id>.jsonl`.

- **Path encoding**: take the session's working directory and replace every
  character that is not a letter or digit with `-`. POSIX `/a/b.c` becomes
  `-a-b-c`; Windows `C:\a\b.c` becomes `C--a-b-c`. Worktrees get their own
  directory, encoded the same way; list the directories that start with the
  repository's encoded path to see them all.
- **Fields**: `type` (`user`, `assistant`, `system`, ...), `timestamp`,
  `sessionId`, `cwd`, `gitBranch`, `isSidechain`, and `message.content` blocks
  (`text`, `tool_use`, `tool_result`). Some versions add lines that name a
  linked pull request or the session title; use them when present.
- **Subagents**: in `<session id>/subagents/`, `agent-<id>.jsonl` holds the
  subagent transcript and, when present, `agent-<id>.meta.json` holds its type,
  description, the parent's tool-call id, and its spawn depth. Follow the
  tool-call id back to the parent's agent call to find the brief it got.
- **Messages to a running subagent** may appear as attachment or queued-command
  lines rather than `user` lines; count them as steers.
- **Evidence**: cite `<session id>` plus the 0-based line number.

Layouts change between client versions. If a field or folder named here is
missing, list the directory, read one line's keys, and adapt; report the
difference as a source-map gap.

## Codex transcripts

Sessions live under `<home>/.codex/sessions/<YYYY>/<MM>/<DD>/rollout-*.jsonl`
(older versions may use a flat folder). Each line is a JSON event with a
timestamp; the first line usually carries session metadata such as the
working directory. Cite the file name plus the 0-based line number.

## Mapping a pull request to its sessions

Search transcripts for the pull-request URL and the head branch:

```bash
grep -l "github.com/<o>/<r>/pull/<n>" <home>/.claude/projects/*/*.jsonl
grep -l "<branch>" <home>/.claude/projects/*/*.jsonl <home>/.claude/projects/*/*/subagents/*.jsonl
grep -rl "<branch>" <home>/.codex/sessions/
```

The author is the session (or subagent) whose working branch is the head
branch and that pushed or opened the pull request. Other hits are reviewers or
sessions that discussed it; read only their lines around the pull request.
A subagent that did the work may be the only file on the head branch while its
parent sits on another branch.

Exclude the running retro: its own session file, its subagents (their briefs
name the branch), and any file whose first line is later than the merge time.

## Desktop session tools (optional)

Some clients expose session tools (listing linked or child sessions, searching
transcripts, reading another session's events). Use them when available to find
child sessions and messages exchanged with other sessions; otherwise use the
files above and report child sessions as not located if they cannot be found.

## Fallbacks

- No `gh` or no access: stop and report; GitHub is the backbone of the chain.
- No transcripts on this machine (the work ran elsewhere or in the cloud):
  continue from GitHub alone, mark the author session as not located, and tag
  only what GitHub shows.
- A transcript is partial (rotated, compacted, or truncated): use what is
  there and say which span is missing.
