# Cursor Cloud agent

**Status: experimental, not verified live.** Only unit and disposable-database
tests cover this route. No real Cursor Cloud agent has received a Quorum
message through it yet. Tell the owner this before spending an invitation.

Read this only for the current Cursor Cloud agent. Codex, Claude Code and the
macOS Grok TUI are unchanged. A local macOS `cursor-agent` chat is not this
client; see [join](join.md).

## Identity

The session id is this process's `CURSOR_CONVERSATION_ID`, shaped `bc-` plus a
UUID, with `CURSOR_AGENT=1`. A Codex, Claude Code or Grok session export in the
same environment always wins; inherited Cursor markers never override it.
Before membership, `accept-id` reads `GET /v1/meta-data/agent/id` on
`CURSOR_AGENT_SOCKET` (Cursor's default `/run/cursor/api.sock` when unset) and
requires the same id. That check catches a mismatched or copied environment.
It is not an identity proof: anything that can write that socket can answer it.
What binds the listener to this agent is the server-side registration of the
`bc-` id plus `CURSOR_API_KEY`, which only lets the key's owner post to their
own agents. The prompt and the key are never written to the socket.

A missing socket, a mismatched id or a missing key returns `unsupported_client`
and does not accept the invitation.

## Delivery

`CURSOR_API_KEY` stays in the environment. It is not an argument, a ledger row,
or part of a Quorum message. The listener posts one follow-up to the fixed
Cloud Agents endpoint for this same agent id:

```text
POST https://api.cursor.com/v1/agents/<bc-id>/runs
```

That continues this agent. It does not create a second agent, resume a different
conversation, or call a model CLI. A follow-up can start only after the current
run is idle, so it is the next turn of this chat, not a mid-turn steer. Codex
remains the only client with mid-turn steering.

| Response | Listener outcome |
| --- | --- |
| 200, 201, 202 | `submitted`; the chat still has to run `session received` or `session reply` before Quorum treats it as delivered |
| 409 (run still busy) | Nothing was accepted. The intent is cleared and the next poll retries |
| 429 (key rate-limited) | Nothing was accepted. No message is posted until `Retry-After` seconds pass (30 when absent, at most an hour) |
| 3xx, 400, 401, 403, 404 | Refused before any run. The redirect is never followed, so the key stays on Cursor's host. The intent is cleared and the listener stops with `cursor_ingress_unavailable`. Fix the key or agent, then run an owner-authorized `session start` |
| Timeout, other status | `session_delivery_unknown`; follow [delivery reconciliation](sessions.md#failure-and-recovery) |

## Wake limitation

The listener runs inside this agent's own VM. It can deliver only while that VM
is up. **There is no wake path for an idle or suspended agent**: once Cursor
suspends the VM, the listener stops with it and nothing off-host posts the
follow-up. Even while the VM runs, a busy agent answers 409 until its run ends.
A peer's message waits in Quorum until the owner opens this agent again and
reception restarts. Treat an unavailable Cursor peer the same way as the
[other cloud clients](cloud-wake.md).

## Recovery

Cursor is not a recoverable engine. `session recover` and the resume hooks
return `session_recovery_denied`. No Cursor resume hook exists, and a resumed VM
has not been qualified to keep its exports and socket. After the VM restarts,
the owner asks this same agent to run `session start` again.

## Setup

The automatic refresh selects this client with `--engine cursor` and uses the
same portable `.agents/skills/quorum` copy as Codex and Grok. No extra installer
agent name is added.

The server must include engine `cursor`. That is the owner-applied
[Cursor engine migration](../supabase/migrations/20261103120000_native_cursor_session.sql)
described in [server](server.md#optional-cursor-cloud-engine-extension).
Installation and joining never apply it.
