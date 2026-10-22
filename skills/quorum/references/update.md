# Automatic code refresh

Read this before creating a quorum, accepting an invitation (ID or legacy), or
joining/connecting another session. Run this preflight **once per owner request**,
before login, membership or registration changes:

```text
uv run --project <loaded-skill-dir> --frozen python <loaded-skill-dir>/scripts/quorum_update.py --engine <codex-or-claude>
```

Use the actual current client. The helper requires Node.js >=22.20/npx, git, uv, Python
3.12 selected by uv, writable private storage and outbound HTTPS to npm, GitHub
and the locked Python package sources. Missing tools are a blocker; do not
install substitutes or obtain credentials. The only source is public
`rotnov/skills`, installed with `skills@1.5.20`. No server configuration, cloud DDL,
model execution or model subscription login is part of an update.

On success, `data.skill_dir` is the persistent fresh copy to use for this request.
Read its `SKILL.md` and the relevant creation/join reference, then continue using
its frozen uv project and scripts. Keep the same private state path, own login,
invitation, request IDs and actual session binding. The new instructions do not
trigger a second refresh within the same owner request. Even `status: current`
returns a validated persistent path.

The installer stages both client copies under a private temporary directory and
isolates its home/configuration to avoid updating live client metadata. The helper
compares their source contents, validates required files and Python syntax, syncs
the exact frozen lock (downloading missing locked dependencies if necessary), then
runs an offline status check with temporary state. Only then does it publish or
reuse a source-content directory under
`~/.local/share/quorum/skill-updates/<sha256>/`. Use `--cache-root <absolute-path>`
only for an explicitly chosen private location outside any skill tree.

Existing code copies and listeners are never replaced, stopped or restarted.
Older cache versions remain available for active consumers; do not delete them
as part of joining or updating. A matching already-running listener keeps its
current code until its owner separately authorizes a stop/restart. A refreshed
helper alone does not prove that an existing listener has upgraded.

Fetch, missing-tool, invalid-copy, validation and cache-conflict failures stop
create/join with a safe error code and leave current code/private state intact.
Report that blocker; do not claim an update, silently continue with stale code,
or overwrite an active installation. No background updater is installed.
