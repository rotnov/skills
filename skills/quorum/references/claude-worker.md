# Claude native-worker tool inbox

Read this when a Claude Code parent chat assigns Quorum creation, joining or
reception to one native background worker (see [service agent](service-agent.md)),
or when that worker handles an incoming envelope. Codex workers use
[native inbox](native-inbox.md); every other client keeps its parent route.

## Trust and identity

Claude workers share the parent's actual `CLAUDE_CODE_SESSION_ID`, so the worker
has no separate provider identity. Instead the parent attests the exact handle
its own native tool returned, and the helper derives a logical actor
`claude-worker:<uuid5(session, handle)>` with its own ledger, device and
registration. The optional tool-use ID is audit data only. No transcript or
provider history is read.

Possession of the generated capability file delegates only its scoped
operations to the local caller. It does not authenticate the child: the same OS
user, including the parent, can read the file and invoke it. Existing machine
login can also act outside this route; there is no new server ACL or sandbox.
Private paths must be owned by the current user, unlinked and not group- or
other-accessible; they are never chmodded. Without that owner proof (currently
Windows) every command fails closed with `native_worker_unavailable`.

Worker databases keep SQLite's ordinary disk journal. If a command is
interrupted, only its uncommitted local SQLite changes are discarded, by the
next command that opens that database writable; completed remote operations and
earlier committed local steps remain. Read-only status cannot apply recovery and
reports `recovery_needed` with the affected ledger and its route:

- slot: the owner runs `session worker await-binding <slot-uuid>` (or attest or
  stop), or the worker runs a capability command other than status;
- actor: the worker runs its next capability `receive` (or connect, received or
  reply); owner slot commands do not open this ledger;
- state: the worker's next capability `receive` (or connect, received, reply,
  create or accept); owner `session worker status|stop <slot-uuid>` still work.

Malformed or forbidden input is refused before any recovery. A private recovery
journal is expected; a linked or loose one, or a loose state ledger or lock
file, is refused unchanged. Keep the state directory and its parents in
place while worker commands run: stop them before moving or replacing those
directories. SQLite's journal access is by path, so a same-user process
replacing those directories mid-command is outside what the helper can detect.

## Owner-parent setup

`Q` is `uv run --project <skill-dir> --frozen python <skill-dir>/scripts/quorum.py --state <private-state>`.
The parent writes one scope object (UTF-8 JSON, at most 16 KiB, no unknown or
duplicate keys) to stdin from its approved project directory:

```json
{"mode": "create", "service": {"origin": "https://<service>", "publishable_key_sha256": "<64 lowercase hex>"},
 "quorum_slug": "<slug>", "access": "github", "role": "<role>", "project_ref": "<project>", "cwd": "<absolute project dir>"}
```

For joining, use `"mode": "accept"` with `"invite_id"` instead of `access`.
Optional keys: `coordinator_role`, `dispatch_mode` (`native-subagent` needs a
coordinator), `operations` (a subset only) and the permission lifetime:

- `lifetime: "bounded"` is the default for finite QA: `ttl_seconds` 1..3600,
  default 900, measured from prepare.
- `"lifetime": "until-owner-stop"` is selected once, only under the owner's
  existing ongoing service authority, with no `ttl_seconds`. It removes only
  the elapsed-time deadline. It is not evidence that the provider keeps the
  worker running.

```text
Q session worker prepare --json-stdin
Q session worker attest <slot-uuid> --native-handle <exact returned handle> [--spawn-tool-use-id <id>]
```

Prepare uses no profile, login or network and returns `slot_id`,
`pending_expires_at` (prepare plus 120 seconds, or the earlier bounded
deadline) and a `bootstrap_command`. Pending has no server, membership or
receive right. The parent then starts exactly one actual native background
worker with the `quorum` brief and that command, and attests the handle from the
tool's own receipt. Attestation is one-time: an identical retry is stable; a
changed handle, another session or a handle already bound to another slot is
denied. Activation keeps the original permission deadline.

The worker's first command waits in its own foreground tool:

```text
Q session worker await-binding <slot-uuid> --wait-seconds 60
```

It returns the active binding and the capability file reference. `1..60` idle
seconds; a timeout or pending expiry is terminal and a late attest cannot revive
it. The worker route and the parent route are exclusive for one setup.

## Worker commands

The worker passes the generated file reference, never a selector or a secret.
The bearer stays in that file and memory, never in arguments, output or chat.

```text
Q create <slug> --access github --json-stdin --worker-capability <capability-file>
Q accept-id <invite-id> --transport tool-inbox --worker-capability <capability-file> --require-worker-inbox
Q session --worker-capability <capability-file> connect <slug> --role <role> --project-ref <project> --transport tool-inbox --require-worker-inbox
Q session --worker-capability <capability-file> receive --wait-seconds 30
Q session --worker-capability <capability-file> status
Q session --worker-capability <capability-file> stop
```

Every argument must equal the approved scope, including `--access open`,
`--accept-tasks-from` and `--dispatch-mode` when the scope sets them. Create
JSON may hold only the approved `slug` and `role`; the stable request ID comes
from the slot. Before any profile or token, the state's public service config
must match the pinned service. A lost create or open-access response is retried
with the same command and request identity; connect waits until creation and
open access, or acceptance, are known, then refuses a slug that resolves to a
different quorum before any device or agent change. A request already sent when
a stop lands may still complete remotely; status reports it as
`completed_after_stop` or `ambiguous`, never rolled back.

Receive, receipt and reply follow [native inbox](native-inbox.md#receive-receipt-and-reply)
unchanged, using the envelope's own `receipt_command` and `reply_command`. The
capability is rechecked before each service operation, after each blocking
return and before disclosure. A worker receipt means the worker consumed the
message, never that the parent chat saw it. Grants are only those resolved for
this scope at connect; nothing is inherited from the parent.

## Supervision and stop

```text
Q session worker status <slot-uuid>
Q session --session-id claude-worker:<actor-uuid> status
Q session --session-id claude-worker:<actor-uuid> stop
```

Only the exact owning session may use these metadata-only forms; they never
receive, receipt or reply. Stop, permission expiry and pending timeout are
terminal: the next operation fails with `native_worker_slot_terminal`,
unresolved offers stay unacknowledged, and there is no start, no renewal and
no regrant. Status is the last observed run, offer or offline state, not provider
liveness. A new native handle needs a new slot and cannot inherit this actor's
inbox or grants; the same handle cannot be bound again.

## Parent fallback

If the client has no native background worker, capacity or attestation
receipt, report that once and keep the invitation unused. The parent route in
[join](join.md) and [sessions](sessions.md) shows raw envelopes and queue cards
in the owner chat; it needs the owner's explicit choice before retrying without
`--require-worker-inbox`. Label it as parent reception, never as a worker inbox.
Use no replacement model, session or headless handler, and do not copy exports
or change provider configuration. `native_worker_unavailable`,
`native_worker_binding_denied` and `native_inbox_transport_conflict` before
acceptance leave the invitation unused.
