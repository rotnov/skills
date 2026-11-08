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

### Two-call setup

For a worker that joins by a new invitation, run one command from the approved
project directory, in the owner's configured state:

```text
Q session worker spawn-plan <slug> --role <role> (--open | --github-user <login> | --github-id <id>)
  [--project-ref <project>] [--lifetime bounded --ttl-seconds N | --until-owner-stop]
  [--coordinator-role <role> [--dispatch-mode native-subagent] | --accept-tasks-from-members
   [--standing-grants-from <role>] [--trust-members]] [--knowledge search|read|propose ...]
  [--update-maintenance] [--worker-state <dir>] [--cwd <dir>] [--request-id <id>] [--shell bash|powershell]
```

Each flag maps 1:1 to the scope key below (`--update-maintenance` is
`"update": true`); an omitted flag leaves its key out. The command builds the
accept scope from this state's own service config (origin and the publishable
key's SHA-256), checks it exactly as prepare does, then issues the single-use
invitation (open, or GitHub for the named login) and prepares its slot. A
`--cwd` other than the current directory, an invalid scope, a worker state
pinned to another service, a reused open state (`anonymous_state_conflict`) or
an existing binding refuses before any invitation exists. The worker state is
this state for GitHub, and a fresh private state under it for `--open`. It
spawns nothing and returns:

- `slot_id` and `expires_at` (the 120-second attest deadline);
- `spawn_prompt`: the complete text for the parent's native Agent tool, with
  the worker brief and one bootstrap command, `session worker join <slot>`;
- `attest_command`, with a `<native-handle>` placeholder;
- `join_argv` (the exact argv) and `join_command`, rendered for `--shell`:
  `bash` (the default) is Claude Code's Bash tool, which on Windows is Git
  Bash; `powershell` uses the `&` call operator. The command first changes
  to the scope's bound directory, `join_cwd` (`cd <dir> &&`, or
  `Set-Location -LiteralPath '<dir>';`), because a native subagent's shell
  starts in the chat's project directory. Every worker command still checks
  that directory;
- `invitation`, `worker_state` and `connect_flags`.

The parent then makes two calls: it spawns one native background worker with
`spawn_prompt`, and runs `attest_command` with the handle its tool returned.
The prompt holds no secret: the publishable key stays in the slot record and
the bearer in the capability file.

The worker's `Q session worker join <slot>` runs await-binding, `accept-id`
(with `--anonymous --origin --publishable-key` for open access), `connect`
with the scope's `connect_flags`, and the first `receive --wait-seconds 0`.
Each step runs through its ordinary command and checks. It returns
`capability_file`, the `receive` result and `wait_command`, and the worker goes
straight to the [background wait](#background-wait). A failed step returns its
own code with `join_step`; a retry skips an accept whose membership is pinned.

### Manual setup

The fallback for create and adopt scopes or a hand-written brief. The parent writes one scope object (UTF-8 JSON, at most 16 KiB, no unknown or
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
to false and excludes `coordinator_role`), `standing_grants_from` (a role; needs
`accept_tasks_from_members`), `trust_members` (boolean; needs
`accept_tasks_from_members`), `dispatch_mode` (`native-subagent`
needs a coordinator; member intake is inline), `operations` (a subset only) and the permission lifetime:

- An invitation with member intake and standing grants (`--accept-tasks-from-members
  --dispatch-mode inline` and `--standing-grants-from <role>`) maps to
  `"accept_tasks_from_members": true, "standing_grants_from": "<role>"` with no
  `coordinator_role`. A current invitation's `--trust-members` adds
  `"trust_members": true` ([member trust](delegation.md#member-trust)). `coordinator_role` alone stays coordinator-only intake. A
  binding persists one delegation policy, so it cannot be combined with member
  intake.

- `knowledge` (create or accept only): a nonempty subset of
  `["search","read","propose"]` for a project wiki rule the owner wants this
  member to follow. It adds the `knowledge` operation for exactly those commands;
  anything else, including `verify` or `admin`, is `scope_invalid`. Omission keeps
  the legacy scope with no knowledge. See
  [Claude worker knowledge](knowledge.md#claude-worker-knowledge).

- `update` (create or accept only): `true` adds the `update` operation, so the
  worker runs `Q update <slug> --worker-capability <capability-file> ...` as its
  own agent, only on this chat's explicit brief and never on a peer's request.
  The server still decides owner-only operations (`owner_required`), and a
  refresh still needs `--owner-refresh`. Omission or `false` keeps the legacy
  scope. Adopt scopes already carry `update`; see
  [rights by mode](#rights-by-mode).

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
deadline), a `bootstrap_command` and `connect_flags`, the exact intake flags the
worker's `session connect` must pass for this scope. Pending has no server, membership or
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
Q session --worker-capability <capability-file> wait [--max-seconds 1..7080]
Q session --worker-capability <capability-file> received|park <message-id> --then-wait [--max-seconds 1..7080]
Q session --worker-capability <capability-file> reply <message-id> --text-stdin --then-wait [--max-seconds 1..7080]
Q session --worker-capability <capability-file> receive --wait-seconds 0
Q session --worker-capability <capability-file> status
Q session --worker-capability <capability-file> stop
```

With a scope's `knowledge` key, the connected worker also runs the
[knowledge](knowledge.md#claude-worker-knowledge) `search`, `read` and
`write` (propose) commands with `--worker-capability <capability-file>` appended;
with `"update": true` it also runs `update`.

### Rights by mode

| Scope | knowledge | update |
| --- | --- | --- |
| create, accept | only with the `knowledge` key: `search`, `read`, `write` as `propose` | only with `"update": true` |
| adopt | every command, `--owner-action` where required | always |

The difference is intentional. An adopt worker takes over this chat's own
owner-connected binding, so it keeps the owner maintenance that binding had. A
create or accept worker is its own member binding: its knowledge is the member
subset (verification, administration, audit, catalog, sources, index and
capture stay with an owner binding), and `update` is an explicit opt-in. Either
way the server's ACL and owner checks decide every call. An accept worker
prepared without these keys gains them with the owner's
[rebind](#rebind-after-a-parent-restart) opt-in, without a new invitation.

Every argument must equal the approved scope, including an `accept-id --quorum`
(the scope's `quorum_slug`), `--access open`,
`--accept-tasks-from`, `--accept-tasks-from-members`, `--trust-members` and
`--dispatch-mode` when the scope sets them. `--exclusive-role` works as on the parent route (see
[sessions](sessions.md)); an adopt scope's parent agent is not a conflict.
`--supersede` is refused with `supersede_not_in_scope`: no worker scope may
retire another agent, so the owner runs `leave` first. `--anonymous` is accepted only with an open accept scope,
which in turn refuses a GitHub accept; it needs the explicit pinned public
service and keeps the new anonymous login in that state's
`anonymous-credentials/`, as the ordinary open join does. A state already used
for GitHub is refused with `anonymous_state_conflict`. Member intake requires `accept_tasks_from_members=true` in
the original owner scope and the matching connect flag. Before member task work,
the same worker runs `session --worker-capability <capability-file> task-check
<message-id>`; an earlier envelope or status is insufficient. Legacy capabilities
cannot add member scope or run member checks. A scope with `standing_grants_from`
also needs the matching connect `--standing-grants-from <role>`. Its connect
stores the same member grant that `session connect` writes, and it adds
`standing-check`; see [standing owner authorizations](delegation.md#standing-owner-authorizations). The owner-held set in
[delegation](delegation.md) still applies (the hard stops under member trust,
otherwise the member exclusions); a stopped/expired capability denies a
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
worker waits with the [background wait](#background-wait), not the receive loop.
It stops only at the brief's limit, on an owner stop or a fatal error, reporting
`brief_deadline`, `brief_count`, `owner_stop` or `fatal:<code>` to the parent.
Prepare and rebind return that contract as `worker_brief`; the parent passes it
to the worker with the `bootstrap_command` and its own limit. The
capability is rechecked before each service operation, after each blocking
return and before disclosure. A worker receipt means the worker consumed the
message, never that the parent chat saw it. Messages that need the owner's
decision follow [awaiting the owner](native-inbox.md#awaiting-the-owner) with
`session --worker-capability <capability-file> park|hold|awaiting ...`; `park`,
`hold`, `awaiting list`, `awaiting dismiss` and `mark` use the scope's `received`
operation and `awaiting resolve` its `reply` operation, so no scope changes. Grants are only those resolved for
this scope at connect; nothing is inherited from the parent.

### Background wait

Once connected, the worker runs `receive --wait-seconds 0` once, for its Hello
outcome and any waiting message. It then starts `wait_command` (from any receive
result, or `wait` in the commands above) and ends its turn. An empty wait uses
no model turn, and the command's exit wakes the worker. Every model turn re-reads
the worker's whole context, so the route keeps turns to the minimum:

- Start each wait-route command with Claude Code's Monitor tool, `timeout_ms`
  1800000. Its one compact result line arrives inside the event that wakes the
  worker.
- Without Monitor, use Bash `run_in_background`, never a shell `&`. Its
  completion notice holds only the output file path, so the worker reads that
  file on each wake, which costs one more turn.
- An offer's line carries what the worker needs, so there is no `receive` call.
  The worker builds each resolve command from fixed templates, never from the
  line, where `Q` is the words of `wait_command` before `session` and `<cap>` its
  capability file:

  ```text
  Q session --worker-capability <cap> received|park <id> --then-wait
  Q session --worker-capability <cap> reply <id> --text-stdin --then-wait
  ```

  Each resolves the offer, then waits in the same command.

#### Compact line and full result

Monitor shows at most 500 characters of one event, so stdout, which is the
event, gets one compact JSON line of at most 450 characters with the
essentials first. The full result, with the envelope, its notice and its
commands, goes first to stderr: Monitor sends stderr only to its output file,
and Bash writes both to its own. A second stdout line is avoided on purpose:
Monitor batches lines within 200 ms into one event, so it would be truncated
with the first, or would cost a second wake.

- The line holds `state`, `next_step`, `continue` and `awaiting_owner`, and
  `stop_reason`, `code` or a short `resolved` (`message_id`, `state`,
  `received`) when they apply. It never holds the Hello summary or a command.
- An offer's line adds `id`, `sender_role`, `reply_to` and `notification`, and
  `priority` when it is `urgent`.
- `full: false` on an offer means the line also carries `body`, and the brief
  alone covers the message: a participant message under no matching
  delegation, or a member's terminal reply or notification.
- `full: true` means the worker reads the output file's full result and
  follows the envelope's notice before it resolves the offer. That holds for a
  body that does not fit, a matching delegation, member trust, a maintenance
  proposal, or `delivery_alerts` that do not fit. If no output file is
  readable, `receive --wait-seconds 0` offers the same message again with its
  envelope.

| `state` | `next_step` | Worker's next step |
| --- | --- | --- |
| `offer_available` | `handle_then_wait` | Handle the message (from the output file when `full` is true), run one resolve template the same way, end the turn |
| `alert_available` | `handle_then_wait` | Act on `delivery_alerts` (from the output file when `full` is true), start `wait`, end the turn |
| `rearm` | `wait` | Start `wait`, end the turn |
| `stopped` | `report_stop` | Report `stop_reason` `owner_stop` |
| `orphaned` | `report_stop` | The process that started it is gone. Report `fatal:wait_orphaned` if anything reads this |
| `wait_failed` | `end_turn` or `report_stop` | Only after `--then-wait`: `resolved` holds the step's own result; `worker_already_running` ends the turn, anything else reports `stop_reason` |

The full result also has `transport`, and an offer or alert result carries the
first unreported Hello outcome. Only `offer_available` carries a body, inside
`envelope`, the same envelope `receive` discloses. A `--then-wait` result adds
`resolved`. Any other exit is a fixed-code failure (`fatal:<code>`), except two:

- `worker_already_running`: a wait already runs on this binding (it takes the same
  locks as `receive`), or a `receive` does. End the turn.
- No result line: the host stopped the command or it crashed. Run `receive
  --wait-seconds 0`, then start `wait`.

Per message the worker therefore takes two model turns: the wake runs one
resolve command, and its start result ends the turn (three with the Bash
output read, or when `full` is true). A message the worker answers only after project work takes those
turns plus the work. A refused receipt, reply or park exits at once with its
error and starts no wait. `--then-wait` needs the scope's `receive` operation,
checked before the step runs, and is refused on every non-worker route.

While `wait` runs, the worker has no turn, so `receive` is not called. `wait`
keeps the heartbeat, the Realtime subscription with its safety scan, and the
same `offer` step as `receive`. Quiet transport notices and parking an elapsed
offer therefore use no model turn. It commits an offer locally before it exits,
and the line and full result are written only after the final authority check,
as in `receive`. Neither is a receipt: only the worker's own receipt, reply or
park command is, after the body reached it, and a lost line or crash offers the
same message again. Resolve each offer before starting a plain `wait`: an open
offer ends it at once, except one under an explicit `session hold`, which wakes
nobody until the hold ends and `wait` parks it. A status alert ends `wait`, a
routine status does not. A service outage is waited out, also at its start,
with `presence: unreachable`, since each exit costs a turn; a release mismatch
stays a fatal code. A server-side failure (`coordination_unavailable` or a
generic `service_error`) cools down as the listener does ([service
outages](resume.md#service-outages)): presence `unavailable` with the code, no heartbeat or Hello, rechecks
after 1, 5, 15 and 60 minutes, then hourly, each resubscribing and reconciling
first. `session stop` ends it within a second. If `--max-seconds` ends during a
cooldown, `wait` returns `rearm` with that `code`. A refusal with its own name
stays `fatal:<code>`.

`--max-seconds` defaults to 1680, two minutes under the 30 minutes after which
Monitor always, and Claude Code [background
commands](https://code.claude.com/docs/en/tools-reference.md#time-limit-for-background-commands)
in unattended sessions by default, stop a command. After `--then-wait` the bound
counts from that command's start. Each `rearm` costs two model turns: start the
next wait, then end the turn. Under Bash, `--max-seconds` may go up to 7080
when the command starts with `timeout` 7200000, Claude Code's 2-hour maximum,
so idle hours cost one rearm every two hours; local terminal, desktop and VS
Code sessions set no limit. For a finite brief, pass at most the seconds left to
its deadline, so `rearm` wakes the worker to report `brief_deadline`; a `-p` run
with a text prompt defaults to 10 minutes, so pass 540 there. The slot deadline
and capability are rechecked at each heartbeat, every 20 seconds.

A killed `wait` releases its locks. Status then shows `listener: offline`, peers
see the heartbeat age out and messages stay durable; the next `wait` or `receive`
continues. `wait` exits as `orphaned` when any of the five processes above it
ends, which reaches past an interpreter launcher, `uv run` and the client's
shells. A client further up, or one this user may not query, is not watched, and
the binding can then look online until the bound ends the process. An ancestor
ending while the client lives, such as a closed terminal behind a `nohup` client,
also ends the wait; the worker then reports it. Between waits, while the worker
handles a message, the binding has no heartbeat, as between receives. An older
brief's `receive` loop still works, and a client without background commands
(`CLAUDE_CODE_DISABLE_BACKGROUND_TASKS`) keeps it. A foreground subagent
cannot use this route: Claude Code stops its commands when its run ends.

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
   first connect checks the parent binding again. If the scope names no intake
   key (`coordinator_role`, `accept_tasks_from_members`, `standing_grants_from`,
   `trust_members`, `dispatch_mode`), it takes this chat's recorded intake
   policy, such as `--accept-tasks-from-members` with its standing grants and
   member trust. The worker's connect then stores the same kind of grant for its
   own agent (nothing is copied), so its first envelope carries that delegation.
   Pass the result's `connect_flags` to the worker's connect; a connect without
   them is refused with `scope_mismatch`. Explicit intake keys are kept as given.
3. Attest the worker and let it connect and `receive` as above. Its join names
   `adopt.agent_id` as the one own agent allowed to share the role meanwhile; a
   service with the role-assignment extension refuses any other own holder with
   `role_held_by_own_agent`.
4. Once the worker has received and receipted an envelope, retire this chat's
   agent so the role names one live agent: `Q leave --request-id <stable-ID>
   --json-stdin` with `{"agent_id":"<adopt.agent_id>"}`. Until then role sends
   fail with `recipient_ambiguous_or_missing` and send nothing; retry them with
   the same request ID. `session peers` then lists only the worker for the role.

### One-command move

The worker is the default route for a Claude parent binding. `session connect`
and `session status` on that binding return `reception_route`: `default` is
`claude-worker`, `route` is the route serving reception now, and `next_step`
is `move` (with `move_command`), `attest`, `fallback` (with `fallback_steps`
and `fallback_reason`) or null. Codex and Grok bindings get no such field and
keep their route.

When `next_step` is `move`, offer the move once in the setup outcome, or run it
when the owner asks to connect or recover this chat's reception. It widens
nothing: the same login, membership, role, project and intake policy. Run it
from this chat in the binding's directory:

```text
Q session worker adopt-plan [--shell bash|powershell] [--lifetime bounded --ttl-seconds N]
```

It checks steps 1 and 2 above first (binding, directory, service, an earlier
adopting worker) and refuses without touching reception. Then it stops this
chat's reception like `session stop`, prepares the adopt slot (until owner
stop by default) and returns the same `spawn_prompt`, `join_command` and
`attest_command` as [spawn-plan](#two-call-setup), plus `verify_command`,
`retire_command` and the fallback steps. Spawn the worker and attest it.

Retire this chat's agent only after the worker has proven reception (step 4),
never merely because its agent is visible. Run `retire_command`
(`session worker adopt-retire <slot-uuid>`) once the worker has connected.
The first run sends one probe from this chat's agent to the worker's agent
and returns `state: probe_sent`. Later runs return `probe_pending` until the
worker's own ledger shows that probe receipted or answered. Only then does
the command leave this chat's agent and return `retired: true`. Until then
both agents stay, nothing is retired, and this chat stays the fallback.

### Parent reception as the fallback

This chat's own parent reception stays the fallback, restored with
`session start` from the same binding:

- `worker_capability_missing`: the owner proof is unavailable here, so no
  move is offered and parent reception continues.
- An `adopt-plan` failure after its stop returns `parent_reception: stopped`
  and `fallback_steps`.
- `worker_attestation_failed`: the slot's 120-second attest deadline passed.
- An attested worker is the route only once it has connected with live
  reception. Until then `next_step` is `join_pending`. If it has no agent five
  minutes after attestation, `fallback_reason` becomes `worker_join_failed`,
  even on an until-owner-stop slot. Both states carry `fallback_steps`, which
  stop the slot first so it cannot connect later, and `recovery_steps`
  (stop, then `adopt-plan` again).
- `worker_unavailable`: the worker stopped or its permission expired. If it
  ever connected, `fallback_steps` first retires its agent with
  `session worker stop <slot-uuid> --leave`.

Once the slot is terminal, with no worker agent left live, and this chat's
reception is enabled again, the fallback is complete: `next_step` returns to
`move`. A `cwd_mismatch` refusal names the bound directory as `expected_cwd`;
run the command again from there.

The owner's request to move is what authorizes that one fallback start. Once
`adopt-retire` has returned `retired: true`, this chat's agent is gone; recovery then is the
worker's [rebind](#rebind-after-a-parent-restart), and parent reception again
needs a new owner-approved `session connect`.

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

## Rebind after a parent restart

When Claude Code restarts and the chat resumes with the same
`CLAUDE_CODE_SESSION_ID`, its background worker is gone, but the slot and its
agent remain. Do not prepare a new accept or adopt scope, and do not ask for a
new invitation. In the resumed chat, from the scope's project directory, run:

```text
Q session worker rebind <slot-uuid>
```

Use any slot of that worker; the command follows earlier rebinds to the current
one. It makes one authenticated `agent_status` read and returns a pending
successor `slot_id`, its `bootstrap_command`, `connect_flags` and `rebind` (the
previous slot, the unchanged `session_ref` and `agent_id`). Then start one native
worker and attest its handle as in [owner-parent setup](#owner-parent-setup).
The new handle may equal the old one. The new worker runs `await-binding` and
then `receive` directly: the binding is already connected, and a `connect` with
the `connect_flags` is optional.

What stays: the agent and its session reference, role, project, quorum,
membership, delegation (including member trust and standing grants), scope
(including `knowledge`), the actor ledger with offered, parked and queued
deliveries, and the permission deadline, which is never extended. Nothing is
registered, accepted or invited, and no second agent appears, so role sends never
become `recipient_ambiguous_or_missing`. The old worker keeps serving until the
attestation. The attestation then hands over in one locked step: the old slot
turns terminal with reason `rebound` and `rebound_to` the successor. Every later
or in-flight command of the old handle is refused with
`native_worker_slot_terminal` reason `rebound`, because each command rechecks
its slot and the persisted actor binding before each service call and before
disclosure. The new worker's first receive says Hello with a fresh presence
epoch.

The rebind is refused with:

- `session_mismatch` for another chat (session ID);
- `native_worker_slot_terminal` for a stopped or expired slot;
- `reception_stopped` when the binding's reception is disabled;
- `agent_revoked` when the agent or membership was revoked at the service;
- `target_pending` when the worker never connected;
- `slot_pending` when the slot is not attested;
- `rebind_pending` when another successor is still pending. Each binding
  reserves one pending successor. Repeating the same rebind, for example after
  a lost answer, returns that same successor. A different request, such as one
  adding opt-ins, is refused until the successor is attested or expires, and an
  expired successor is replaced.

Attest refuses `worker_already_running` while the old worker is inside a
receive. Nothing changes; retry when that call has returned. A refused attest
also asks a running `wait` to exit, so a retry a few seconds later passes, even
when that wait outlived the old client.

For a create or accept scope only, the owner may add the member opt-ins in
the same step: `--knowledge search|read|propose` (repeatable) and
`--update-maintenance`. Nothing else in the scope can change.

An interrupted attestation is completed by repeating the same attest. If its
successor expires first, run `rebind` again on the old slot. Stopping a
superseded slot never touches its successor; `stop --leave` on it is refused, so
retire the agent through the current slot. Stopping an unfinished successor
stops the agent's reception.

### Fallback for installs without rebind

An older installed skill has no `session worker rebind`. Prefer the
[automatic refresh](update.md). If refreshing is impossible, this manual
sequence recovers without a new invitation. Role sends are ambiguous from step 1
until step 4, so keep that window short:

1. In the resumed chat, with the same state, run `Q session connect <slug>
   --role <role> --project-ref <project>`, adding the old scope's intake flags
   (for example `--accept-tasks-from-members --dispatch-mode inline`). Do not run
   `session start`.
2. Prepare an `adopt` scope with the same quorum, role, project and directory,
   then attest, `await-binding`, connect (with the result's `connect_flags`) and
   `receive` in the new worker.
3. Run `Q session worker stop <old-slot-uuid>`, then `leave` with the old
   worker's agent ID.
4. After the new worker has receipted its first envelope, `leave` with this
   chat's own agent ID (`adopt.agent_id`). Its result shows
   `local_binding: retired`.

Never leave this chat's agent before the new worker receives, and never leave
the new worker's agent.

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
liveness. Its `session` part (worker capability status and the parent's
`session worker status <slot-uuid>`) carries the same `pending_receipts`,
`awaiting_owner` and active `reception_hold` as a Codex worker's status, never
a message body. A new native handle gets this actor's binding only through
[rebind](#rebind-after-a-parent-restart). Otherwise a new handle needs a new slot
and inherits nothing, and a bound handle cannot be bound to another slot.

## Parent fallback

A chat that already has a parent binding keeps it as the fallback for its
worker; see [parent reception as the fallback](#parent-reception-as-the-fallback).
For a new join: if the client has no native background worker, capacity or attestation
receipt, keep the invitation unused and report that blocker once per
[join without a worker](join.md#without-a-worker). The parent route in
[sessions](sessions.md) shows raw envelopes and queue cards; it is a legacy
opt-in without `--require-worker-inbox` only when the owner requests it unprompted, never offered. Label it as parent reception, never as a worker inbox.
Use no replacement model, session or headless handler, and do not copy exports
or change provider configuration. `native_worker_unavailable`,
`native_worker_binding_denied` and `native_inbox_transport_conflict` before
acceptance leave the invitation unused.
