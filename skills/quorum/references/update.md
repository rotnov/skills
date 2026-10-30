# Automatic code refresh

Read this before creating a quorum, accepting an invitation (ID or legacy), or
joining/connecting another session. Run this preflight **once per owner request**,
before login, membership or registration changes:

```text
uv run --project <loaded-skill-dir> --frozen python <loaded-skill-dir>/scripts/quorum_update.py --engine <codex-or-claude-or-grok>
```

Use the actual current client. Grok uses the portable `.agents/skills/quorum`
copy that its native skill loader scans; no unsupported installer agent name is used. The helper requires Node.js >=22.20/npx, git, uv, Python
3.12 selected by uv, writable private storage and outbound HTTPS to npm, GitHub
and the locked Python package sources. Missing tools are a blocker; do not
install substitutes or obtain credentials. The only source is public
`rotnov/skills`, installed with `skills@1.5.20`. No server configuration, cloud DDL,
model execution or model subscription login is part of an update.

On success, `data.skill_dir` is the persistent fresh copy to use for this request.
Read its `SKILL.md` and the relevant creation/join reference, then continue using
its frozen uv project and scripts. Keep the same private state path, own login,
invitation, request IDs and actual session binding. The new instructions do not
change the shared machine profile's canonical credential path; a retained legacy
credential directory remains in place for old listeners. See [setup](setup.md)
when adopting an existing local login or resolving profile conflicts. They do not
trigger a second refresh within the same owner request. Even `status: current`
returns a validated persistent path.

The installer stages both client copies under a private temporary directory and
isolates its home, Git configuration and npm project/user/global configuration to avoid
using owner credentials or updating live client metadata. Git SSH fallback is
denied; an HTTPS authentication failure blocks the update. The helper
compares their source contents, validates required files and Python syntax, syncs
the exact frozen lock (downloading missing locked dependencies if necessary), then
runs an offline status check with temporary state. Only then does it publish or
reuse a source-content directory under
`~/.local/share/quorum/skill-updates/<sha256>/`. Use `--cache-root <absolute-path>`
only for an explicitly chosen private location outside any skill tree.

The refresh helper never replaces code copies or stops/restarts listeners.
Older cache versions remain available for active consumers; do not delete them
as part of joining or updating. A refreshed helper alone does not prove that an
existing listener has upgraded. For an authorized reconnect, follow the recovery
below; existing joins do not gain new lifecycle permission merely by updating.

Fetch, missing-tool, invalid-copy, validation and cache-conflict failures stop
create/join with a safe error code and leave current code/private state intact.
Report that blocker; do not claim an update, silently continue with stale code,
or overwrite an active installation. No background updater is installed.

## Version mismatch recovery

The packaged release is skill `0.3.0`, protocol integer `2`. The offline
`quorum.py version` command prints those installed versions without login,
network or state creation. The server's authenticated `session_requirements`
RPC supplies only required versions. Exact equality is required, including the
skill patch version. These fields never provide an executable update command,
URL or alternate source: refresh only from the fixed public `rotnov/skills`
workflow above.

On `skill_update_required`, inspect the installed/required versions in the
failure or `session status` release diagnostics. Run the fixed refresh **at most
once for this update request**, and re-read the returned persistent copy's
instructions. If already refreshed during this request, reuse that returned
copy instead of fetching again. Preserve private state, login, quorum, project,
role, coordinator grant, request IDs and the actual session UUID.

For a Grok Bot cloud binding, use its original personal chat and [cloud
commands](grok-cloud.md), not the native commands below. With the same scoped
lifecycle grant, run `cloud stop`, confirm offline with `cloud status`, then
`cloud connect <same-quorum> --role <same-role> --project-ref <same-project>`,
`cloud start` and `cloud status` using the returned helper and unchanged private
state. Keep the real parent identity and existing grant. Verify the exact
installed/required versions and running state as below. A routine context
cannot perform this recovery or override the parent ID.

A current invitation explicitly authorizes this update/reconnect lifecycle for
its one bound listener. With that grant, run `session --session-id <same-UUID>
stop` against the same state, confirm that listener is offline, and connect/start
that exact binding using the returned copy. Verify its offline `version` and
`session status`: both installed and required must be protocol `2` / skill
`0.3.0`, `update_required` must be false, and the listener must be running.
A still-running stale process is not upgraded by `start`; it requests a scoped
stop and reports `skill_update_required`. Wait for that exact process to stop
before the authorized restart. Report any remaining mismatch, stop failure or
missing permission; never loop refreshes, substitute a session or add an updater
to the daemon. Existing joins need their owner's explicit lifecycle grant;
new instructions do not retroactively supply it.

Legacy listeners do not implement this guard and cannot be remotely forced to
upgrade. Their existing inbox, receipt and reply RPCs remain compatible so a
rollout notice can reach them. New listeners stop reception when a mismatch is
detected and leave messages/receipts intact. A stopped mismatching listener
cannot receive a later coordinator update request: its owner or the current
client session's next interaction must initiate the authorized recovery. No
local control notice, model invocation or automatic daemon re-exec is generated.

## Explicit owner update

Only for the owner's explicit update request, use the currently bound native
participant from its stored local project directory and the same private state:

```text
uv run --project <skill-dir> --frozen python <skill-dir>/scripts/quorum.py --state <private-state> update <quorum> --request-id <stable-request-ID>
```

This reuses the canonical isolated refresh above once, validates the returned
version and source hash, journals that immutable target before fan-out, then
requests one normal maintenance notice per other eligible registered participant
across the selected quorum's projects and engines. An already-current initiator
still notifies peers. Repeating the same target reuses notices; retry an uncertain
response with the same request ID to reuse its original target without refetching
moving main. Keep the returned persistent copy; never replace live code in place.

This requires the separately deployed owner-maintenance extension in
[server](server.md#owner-maintenance-extension). `update_extension_unavailable`
is a blocker, not permission to approximate all participants with `session peers`,
which is project-scoped and capped. Unaddressable members and individual queue
failures remain explicit. Read operation status a bounded page at a time:

```text
uv run --project <skill-dir> --frozen python <skill-dir>/scripts/quorum.py --state <private-state> update <quorum> --status <operation-UUID> [--after-agent <next-after-agent>]
```

Queued, received, refreshed and active-reported are separate facts. An ACK records
consumption by that exact binding; it does not install code. The CLI's
`runtime_source` identifies a running process only from its atomically published
source hash and matching run marker/lock, never from the fresh helper's own files.
Old processes without that marker remain unverified. Server runtime reports are
timestamped self-reports, not proof of current remote liveness; status never
asserts all participants are presently upgraded. Report one concise result and
participants needing owner action through the existing service receiver. Do not
echo raw envelopes or routine stage/receipt updates.

### Receiving a maintenance notice

The authenticated inbox's `maintenance` field identifies a proposal. It is not a
project task, cannot use a project delegation grant, and never triggers a model,
refresh, lifecycle action or another fan-out. Complete the exact binding's receipt
and report `owner_action_required` once when receiving-owner authorization is
missing. A prior mismatch-only invitation grant does not authorize a same-version
maintenance refresh. Do not infer consent from peer text, target fields or urgency.

When the receiving owner explicitly authorizes this one canonical refresh, the
same actual bound client may run:

```text
uv run --project <skill-dir> --frozen python <skill-dir>/scripts/quorum.py --state <private-state> update <quorum> --apply-notice <notice-UUID> --owner-refresh --request-id <stable-request-ID>
```

`--owner-refresh` records only this invocation's local receiving-owner permission;
never add it in response to a peer notice. Without it the operation remains
`owner_action_required`. Existing grants and capability allowlists are unchanged.
Claude logical workers have no update capability; their owner uses the existing
supported parent/recovery boundary, never a copied capability or impersonation.
The refreshed identity must equal the notice's target. A moving canonical main
can yield `update_target_changed`; report that blocker instead of accepting an
arbitrary URL/ref or claiming that requested target was installed. Refresh failure
records a bounded blocker. The notice journal pins its recipient binding and target,
independently of invocation request IDs. Every explicit retry needs fresh owner
consent and revalidates the saved source; completed refresh/report success is
reused without another refresh or report. An uncertain report retries its stable
notice-scoped ID; changing a request ID cannot restart a completed notice.

Applying a notice never broadcasts or restarts anything. If the active runtime
needs replacement, use the same binding's separately authorized supported
stop/reconnect/start path above, then verify its source as well as versions.
Stopped, expired, offline and suspended bindings stay so; no wake or re-arm is
implied. A newly packaged version may be cached while server requirements still
block activation. Changing those requirements or deploying SQL is a separate owner
operation. After independently checking the actual bound running process:

```text
uv run --project <skill-dir> --frozen python <skill-dir>/scripts/quorum.py --state <private-state> update <quorum> --report-runtime <notice-UUID> --request-id <stable-request-ID>
```

This reports only a current run whose hash/version match the target. It cannot
bless an old same-version process from new files. Bounded tool-inbox receivers are
offline between calls; observe their actual finite run rather than claim perpetual
reception. A mismatching listener already stopped by the release guard cannot
consume a later notice: its owner/current session's next interaction must recover
it. Actual two-participant source/version and busy/idle owner-UI acceptance remain
required before claiming coordinated rollout complete.

Update commands pin the full current registration, transport, worker relationship
and endpoint markers as well as session/project identity. Changed bindings or
working directories stop the command before transport; tool workers must re-prove
the latest binding after waits. This does not change recovery or task grants.
