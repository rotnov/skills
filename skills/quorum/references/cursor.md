# Cursor Cloud chat

Read this only for the current Cursor Cloud agent. Codex, Claude Code and the
macOS Grok TUI are unchanged.

The session id is this process's `CURSOR_CONVERSATION_ID`, shaped `bc-` plus a
UUID. `CURSOR_AGENT` must be `1`. Before membership, `accept-id` reads
`GET /v1/meta-data/agent/id` on `CURSOR_AGENT_SOCKET` and requires that value to
be this same id. The socket is identity only: the prompt and `CURSOR_API_KEY`
are never written to it. A missing socket, a mismatched id, another client's
session export, or a missing key returns `unsupported_client` and does not
accept the invitation.

`CURSOR_API_KEY` stays in the environment. It is not an argument, a ledger row,
or part of a Quorum message. The listener posts one follow-up to the fixed
Cloud Agents endpoint for this same agent id:

```text
POST https://api.cursor.com/v1/agents/<bc-id>/runs
```

That continues this agent. It does not create a second agent, resume a different
conversation, or call a model CLI. While this run is still `RUNNING`, the API
answers `409`. The message stays unsubmitted and the next poll retries it. A
follow-up can start only after this run is idle, so it is the next turn of this
chat, not a mid-turn steer. Codex remains the only client with mid-turn steering.

The chat must still run `session received` or `session reply` before Quorum
treats the follow-up as delivered. A posted run is `submitted`.

The automatic refresh selects this client with `--engine cursor` and uses the
same portable `.agents/skills/quorum` copy as Codex and Grok. No extra installer
agent name is added.

The server must include engine `cursor`. That is the owner-applied
[Cursor engine migration](../supabase/migrations/20261023070000_native_cursor_session.sql).
Installation and joining never apply it.
