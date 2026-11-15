# Source map

Read this when `collect` finds no files for a harness, a harness changed its
storage format, or a new harness has to be added.

`<home>` is the user's home directory. Layouts change between client versions:
when a path or field below is missing, list the folder, read one line's keys,
adapt the adapter, and tell the user what differed.

## What counts as a human message

Only messages a person typed or dictated are labeled. Every adapter sorts each
user-role entry into one kind:

| Kind | Meaning |
| --- | --- |
| `human` | typed or dictated by the person |
| `injected` | system reminders, environment and instruction context, compaction summaries |
| `tool` | tool results |
| `automated` | hooks, schedules, heartbeats, CI and task notifications |
| `agent` | another agent: subagent briefs and reports, peer and bridge messages, programmatic runs |
| `command` | slash commands and local shell commands with no prose |
| `interrupt` | the person stopped the agent; kept as a flag on the next message |

An adapter uses the harness's structured signal first and the shared text
rules in `classify_user_text` second. The labeling model is the third line:
it answers `not_human` for anything that still slipped through.

## Claude Code

`<home>/.claude/projects/<encoded working directory>/<session id>.jsonl`, one
JSON object per line.

- Read `type` `user` and `assistant` lines; skip `isSidechain`.
- Not human: a `toolUseResult` field or a `tool_result` block; `isMeta`;
  `isCompactSummary`; an `origin.kind` other than `human` (task notifications,
  peer sessions, subagent hand-backs); an `entrypoint` that starts with `sdk`.
- Older lines carry no `origin`; the text rules decide.
- `<session id>/subagents/*.jsonl` is agent-to-agent traffic and is not read.
- Model: `message.model` on assistant lines.

## Codex

`<home>/.codex/sessions/<YYYY>/<MM>/<DD>/rollout-*.jsonl` and the flat
`<home>/.codex/archived_sessions/`. Older files in both are compressed as
`.jsonl.zst` and need the `zstd` executable; without it they are counted under
"excluded".

- Each line is `{timestamp, type, payload}`. Read `session_meta` (session id,
  working directory, `thread_source`), `turn_context` (model) and
  `response_item` payloads of type `message`.
- A session whose `thread_source` is `subagent`, or that has a
  `parent_thread_id`, is agent traffic.
- A user message lists its parts; when
  `internal_chat_message_metadata_passthrough.content_item_kinds` is present,
  only `user.text` parts are human and `user.heartbeat` is automated. Without
  it the text rules decide.
- Messages replayed after a resume repeat their `id` and are dropped.

## Grok

`<home>/.grok/sessions/<URL-encoded working directory>/<session id>/chat_history.jsonl`.

- `type` `user` with a `synthetic_reason` is injected; the person's prompt is
  wrapped in `<user_query>`.
- `prompt_context.json` with `is_non_interactive: true` marks a headless run
  driven by a program: agent traffic.
- Messages have no timestamp. The n-th prompt takes the n-th `turn_started`
  time from `events.jsonl`, else `created_at` from `summary.json`.
- Model: `model_id` on assistant entries.

## Cursor

`<home>/.cursor/projects/<encoded working directory>/agent-transcripts/<id>/<id>.jsonl`:
`role` plus `message.content`, with no timestamp and no model. It is counted
in the table's "Other" column.

## Adding a harness

In `scripts/harnesses.py`:

1. Subclass `Adapter`; set `name`.
2. `discover(home)` yields one `Source` per transcript file. Its size and
   modification time decide whether a later run reads it again.
3. `parse(source)` yields `Record` objects in order: harness, session id,
   project, timestamp, turn index, role, kind, text, and model when known.
   Use `classify_user_text` for any user text the harness does not mark.
4. Append an instance to `ADAPTERS`, and add its name to `TABLE_COLUMNS` if it
   should have its own column.
5. Raise `VERSION` so existing stores are re-read, and add a fixture and a
   test for the new adapter.
