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
other-accessible; they are never chmodded. On Windows the same proof reads the
owner and DACL from handles opened relative to their verified parent, never
through a junction or other reparse point: private directories need a protected
DACL with no inherited rule, and they and their files may grant access only to
the current user (directly or through OWNER RIGHTS, as Python's `mkdir(mode=0o700)`
writes), SYSTEM or Administrators. Ancestors must be owned by the user,
SYSTEM, Administrators or TrustedInstaller, and nobody else may delete, rename
or re-permission them; AppContainer package and capability rules count as the
user's own, but another account (such as a sandbox user group) does not, so
keep the state directory under the user profile, not `%TEMP%`. The state directory must be on a local NTFS or ReFS
drive. No ACL is changed; new private directories and files get an owner-only
DACL when created. In an elevated session, objects owned by Administrators (that
session's default owner) count as the user's. Where neither proof is available,
every command fails closed with `native_worker_unavailable` reason
`ownership_unverified`; on Windows that means the installed copy predates this
proof (run the [automatic refresh](update.md)) or `advapi32` cannot load.

On Windows, an existing state directory made by Git Bash `mkdir -p`, PowerShell
`New-Item` or Explorer has an inherited, unprotected DACL, so the proof refuses
it as reason `path_unsafe` and leaves it unchanged. Pass a path that does not
exist yet (`session worker prepare` creates it owner-only), or create it with
Python `os.mkdir(path, 0o700)`. To keep an existing state directory, stop its
worker commands and replace its inherited rules with one owner rule:

```powershell
$sid = [Security.Principal.WindowsIdentity]::GetCurrent().User.Value
icacls "<private-state>" /inheritance:r /grant:r "*${sid}:(OI)(CI)F"
```

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
On Windows, the verified directories stay open without delete sharing while a
database is in use, so they cannot be renamed or deleted until it closes. NTFS
reserves `:`, so a logical actor's directory there is
`sessions/claude-worker-<uuid>`; the actor reference itself is unchanged.

## Owner-parent setup

`Q` is `uv run --project <skill-dir> --frozen python <skill-dir>/scripts/quorum.py --state <private-state>`.
The parent writes one scope object (UTF-8 JSON, at most 16 KiB, no unknown or
duplicate keys) to stdin from its approved project directory:

```json
{"mode": "create", "service": {"origin": "https://<service>", "publishable_key_sha256": "<64 lowercase hex>"},
 "quorum_slug": "<slug>", "access": "github", "role": "<role>", "project_ref": "<project>", "cwd": "<absolute project dir>"}
```

For joining, use `"mode": "accept"` with `"invite_id"` instead of `access`.
For an open (anonymous) invitation, the accept scope also sets
`"access": "open"`; omitted or `"github"` keeps the GitHub login. Prepare an
open scope in a fresh private state (no GitHub `config.json` or credentials).
To hand this chat's existing parent-route binding to a worker, see
[adopting this chat's binding](#adopting-this-chats-binding).
Optional keys: `coordinator_role`, `accept_tasks_from_members` (boolean; defaults
to false and excludes `coordinator_role`), `dispatch_mode` (`native-subagent`
needs a coordinator; member intake is inline), `operations` (a subset only) and the permission lifetime:

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
Q accept-id <invite-id> --anonymous --origin <origin> --publishable-key <key> --transport tool-inbox --worker-capability <capability-file> --require-worker-inbox
Q session --worker-capability <capability-file> connect <slug> --role <role> --project-ref <project> --transport tool-inbox --require-worker-inbox
Q session --worker-capability <capability-file> receive --wait-seconds 30
Q session --worker-capability <capability-file> status
Q session --worker-capability <capability-file> stop
```

Every argument must equal the approved scope, including `--access open`,
`--accept-tasks-from`, `--accept-tasks-from-members` and `--dispatch-mode` when
the scope sets them. `--anonymous` is accepted only with an open accept scope,
which in turn refuses a GitHub accept; it needs the explicit pinned public
service and keeps the new anonymous login in that state's
`anonymous-credentials/`, as the ordinary open join does. A state already used
for GitHub is refused with `anonymous_state_conflict`. Member intake requires `accept_tasks_from_members=true` in
the original owner scope and the matching connect flag. Before member task work,
the same worker runs `session --worker-capability <capability-file> task-check
<message-id>`; an earlier envelope or status is insufficient. Legacy capabilities
cannot add member scope or run member checks. All owner-held exclusions in
[delegation](delegation.md) still apply; a stopped/expired capability denies a
check even when its read-only RPC is in flight. Create
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

## Adopting this chat's binding

A chat that already joined with `session connect` (parent route) can move its
routine reception to one worker with `"mode": "adopt"` and neither `access` nor
`invite_id`; `quorum_slug`, `role`, `project_ref` and `cwd` must equal that
binding. There is no invitation and no new identity: the worker uses the same
state, login and membership, and registers its own tool-inbox session under
the same role (never this chat's session ID). Its operations are connect,
receive, receipt, reply, task-check, status, stop, `knowledge` and `update`;
never create or accept. Prefer `"lifetime": "until-owner-stop"` so owner
maintenance stays available.

1. In this chat run `Q session stop` and confirm `Q session status` shows the
   listener offline.
2. Prepare the adopt scope in this chat with this same `--state`. Prepare reads
   this chat's own binding: its quorum ID becomes the worker's pinned target and
   the result's `adopt.agent_id` names this chat's agent. It refuses with reason
   `parent_binding_missing` (no such binding for this chat in this state),
   `target_mismatch` or `scope_mismatch` (another quorum, role, project or
   directory), `service_mismatch`, `actor_claimed` (another adopting worker is
   still live) or `parent_binding_running` (reception still on). The worker's
   first connect checks the parent binding again.
3. Attest the worker and let it connect and `receive` as above.
4. Once the worker has received and receipted an envelope, retire this chat's
   agent so the role names one live agent: `Q leave --request-id <stable-ID>
   --json-stdin` with `{"agent_id":"<adopt.agent_id>"}`. Until then role sends
   fail with `recipient_ambiguous_or_missing` and send nothing; retry them with
   the same request ID. `session peers` then lists only the worker for the role.

Do not restart this chat's reception for that quorum afterwards. Invitations
(`invite-id`) still run in this chat: they use the login, not an agent. Owner
operations bound to an agent move to the worker, because the retired agent is
refused: the worker runs `Q knowledge <action> --json-stdin --worker-capability
<capability-file>` (with `--owner-action` where required) and `Q update <slug>
--worker-capability <capability-file> ...` only on this chat's explicit brief,
never on a peer's request. The scope pins the quorum; a stopped or expired
capability refuses them.

To replace an adopting worker that ever connected, first retire its own agent;
stop or expiry alone leaves it registered under the role. Until then a new adopt
prepare refuses with reason `previous_worker_agent_active`, naming the slot and
agent. In this chat run `Q session worker stop <slot-uuid> --leave` (adopt
slots only; it stops the slot and runs `agent_leave` for that worker's agent),
or the existing `leave` for that `agent_id`. Only a confirmed leave releases the
claim. If a leave's answer was lost, `Q session worker verify-retired <slot-uuid>`
(and `stop --leave` itself) confirms it with an authenticated read of the pinned
quorum and role. Errors or unclear answers keep the claim. If the worker's
registration answer was lost, prepare refuses with
`previous_worker_registration_unresolved`; `stop --leave` and `verify-retired`
first replay that registration with its stable request ID to learn the agent,
then retire it. A request that never arrived registers the agent then, and
`stop --leave` retires it. Expiry never leaves on its own. A worker that never connected releases
its claim once it is stopped or expired.

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
receipt, keep the invitation unused and report that blocker once per
[join without a worker](join.md#without-a-worker). The parent route in
[sessions](sessions.md) shows raw envelopes and queue cards; it is a legacy
opt-in without `--require-worker-inbox` only when the owner requests it unprompted, never offered. Label it as parent reception, never as a worker inbox.
Use no replacement model, session or headless handler, and do not copy exports
or change provider configuration. `native_worker_unavailable`,
`native_worker_binding_denied` and `native_inbox_transport_conflict` before
acceptance leave the invitation unused.
