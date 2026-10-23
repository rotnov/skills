# Transport facts

Observed on macOS with Claude Code 2.1.302, codex-cli 0.162.1 and app-server
daemon 0.163.0 (2026-10-17). Undocumented items can change without notice.

## Claude Code inbox socket

- Documented: each session binds a per-user Unix socket (named pipe on
  Windows) exported as `CLAUDE_CODE_MESSAGING_SOCKET`, with a per-session token
  in `CLAUDE_CODE_MESSAGING_TOKEN`. The optional first line
  `{"type":"auth","token":"..."}` is required on native Windows. Delivery,
  holding and expiry follow the session's `crossSessionInbound` rules; a
  session that bypasses permission prompts holds peer messages for approval.
- Undocumented, observed: one frame per line,
  `{"type":"user","message":{"role":"user","content":TEXT},"priority":P}`.
  `next` is delivered at once or between tool calls; `later` is held until the
  current turn ends. The content is shown verbatim; wrapper tags inside it are
  not parsed into sender metadata.

## Codex

- `codex exec resume UUID PROMPT` continues a thread non-interactively and
  keeps its context. It fails with "thread already has an active writer" when
  another app-server, such as the desktop app, hosts the thread.
- `codex queue --thread UUID --message TEXT` adds to the thread's queue
  (`~/.codex/queue_1.sqlite`). The hosting app sends it after the current turn,
  or waits for the user in an idle thread.
- The managed daemon serves JSON-RPC as WebSocket text frames over its Unix
  control socket; `codex app-server proxy` only relays bytes. Used methods:
  `initialize`, `thread/loaded/list`, `thread/resume`, `thread/turns/list`,
  `turn/start`, `turn/steer` (requires `expectedTurnId`), `thread/unsubscribe`;
  events `turn/started`, `item/completed`, `turn/completed`. The schema comes
  from `codex app-server generate-json-schema --experimental --out DIR`.
- The desktop app runs its own app-server over stdio, not the daemon, so its
  threads accept only `codex queue` from outside.
- Codex exports `CODEX_THREAD_ID` and `CODEX_SESSION_ID` (both the thread UUID)
  to its shell commands, not to MCP server processes.
- Thread logs: `~/.codex/sessions/YYYY/MM/DD/rollout-*-UUID.jsonl`; thread
  names in `~/.codex/session_index.jsonl`. A context compaction is a top-level
  record with `"type": "compacted"` and a `timestamp`. Final answers appear as
  `response_item` messages with role `assistant` and as `event_msg`
  `agent_message`; a turn ends with `event_msg` `task_complete` or
  `turn_aborted`.
- In a `read-only` sandbox, `uv` cannot write its cache; the protocol therefore
  gives Codex an absolute interpreter path instead of a `uv` command.

## Sender checks

The bridge compares the caller's environment (`CLAUDE_CODE_SESSION_ID`,
`CODEX_THREAD_ID`) with the binding, and refuses a send as Claude from a Codex
shell. This stops mistakes and stray processes, not a hostile agent running as
the same user: such an agent can read the binding in the bridge home and set or
unset those variables. The boundaries against it are Codex's sandbox and
approvals, and the rule that bridge messages carry no user authority.

A Codex daemon started from a Claude Code session's shell passes that session's
`CLAUDE_CODE_*` variables to the commands its turns run, which can then pass the
sender check as that session; SKILL.md forbids starting it there. The Codex
processes the bridge starts itself (`exec`, `queue`) get an environment without
them.
