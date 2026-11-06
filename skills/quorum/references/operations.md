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

`status` works without service credentials. `doctor [--route <route>]` checks
the participant's actual route, detected like `capabilities`: the route itself,
the service configuration and a stored login. It is offline and read-only: it
prints no values, refreshes no token and writes no state. A legacy login whose
machine binding the next command verifies reports `pending_binding_verification`.
A healthy route returns `ok: true` even while the default ACP worker gate
stands; `pilot_gate` reports that gate's `state` (for example
`engine_acceptance_pending`), `applies_to` and whether it `applies`. Only
`--route acp-worker`, the default worker and Codex runner, fails on the gate.
Real route problems still fail: `route_unknown`, `service_not_configured` or
`auth_required`. `worker` fails closed with `live_agents_disabled` in this
local milestone.
Do not route around it with a raw provider command. An explicitly authorized
Claude messaging test can use [pilot](pilot.md); automatic capability handoff
and persistent daemon installation remain deferred.

## Owner agent commands

The quorum owner can list and retire agents without a bound session. Both need
the [role-assignment extension](server.md#invitation-roles-and-owner-retire); an
older service reports `owner_retire_unavailable`.

```text
python3.12 <skill-dir>/scripts/quorum.py agents <quorum>
python3.12 <skill-dir>/scripts/quorum.py agent-retire <quorum> <agent-id> [--request-id <stable-ID>]
```

`agents` lists each agent's ID, role, project reference, engine, owner label
(GitHub login, `github:<id>` or a short anonymous label), member role, `own`,
`live`, `revoked` and last heartbeat. It never shows tokens, devices or session
references. `agent-retire` revokes one agent of that quorum exactly like its
owner's `leave` and keeps the membership; it is idempotent (the default request
ID derives from the agent ID). Retiring one of your own bound agents also retires
that chat's local binding. Non-owners get `owner_required`; an agent outside the
quorum gets `agent_denied`. Use it for a stale duplicate that blocks
`session send --to-role` or a role claim; the member can then reconnect.

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
