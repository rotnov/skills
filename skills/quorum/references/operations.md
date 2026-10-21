# Agent registration and operations

## Join

For live reception inside this existing chat, use [sessions](sessions.md).
The generic join below only registers identity. First configure and log in as the
human owner. For the request
`quorum join amplifier --role backend`, invoke this skill's helper with explicit
JSON input (supplied through a structured stdin-capable tool):

```text
python3.12 <skill-dir>/scripts/quorum.py join amplifier --role backend --json-stdin
```

```json
{"engine":"claude","project_ref":"owner/scratch-repo","session_ref":"EXPLICIT_MAIN_SESSION_ID","cwd":"/absolute/scratch/checkout"}
```

Use `codex` as the engine for Codex. Neither model may guess a most-recent session
or inspect another user's credentials. Joining is idempotent for the same binding;
output reports the registered agent and `worker: offline`. That is registration,
not a working cross-machine connection. No worker or model starts from join.

`status` works without service credentials. `doctor` reports pending exact engine
pins. `worker` fails closed with `live_agents_disabled` in this local milestone.
Do not route around it with a raw provider command. An explicitly authorized
Claude messaging test can use [pilot](pilot.md); automatic capability handoff
and persistent daemon installation remain deferred.

## Tools and fallback

The local `McpTools` library supports `quorum_send`, `quorum_context`,
`quorum_status`, and `quorum_thread`. A capability binds calls to one agent;
caller-supplied sender identity and human membership operations are rejected.
The stdio dispatcher is a tested library; no native-client MCP configuration or
credential-backed IPC daemon is installed by default. The opt-in pilot reference
describes the explicit owner-side stdio command.

Without an owner-configured tool transport, explain that the worker is offline
and preserve the queued request. Do not have the main agent poll continuously.
Library consumers may call `QuorumApi` under an explicitly authorized human login.

## Recovery

The local SQLite ledger stores session bindings and launch state outside the
installed skill. The OS user controls this directory; mode checks and local
capabilities do not protect against arbitrary code running as that same user.

An expired lease or lost model-start response is not proof of nonexecution.
Keep the attempt `unknown`, retain capacity, and obtain owner reconciliation.
A reply awaiting upload is retried with the same request ID without rerunning
its model. A peer reply is durable and readable through `quorum_thread`; it is
not automatically answered again.
