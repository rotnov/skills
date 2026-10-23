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
