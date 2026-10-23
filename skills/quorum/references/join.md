# Join a quorum

Read this when accepting an invitation and connecting your actual chat.
For issuing an invitation, read the [owner guide](invite.md).

## Join this chat

First start or reuse the [service agent](service-agent.md). For a proven Codex
native worker, follow [native inbox](native-inbox.md): the worker itself runs
`accept-id` and `session connect` with `--transport tool-inbox
--parent-thread-id <parent-UUID>` (only those two commands take these flags),
then its other `session` commands under its own identity; none of the parent
steps below apply to it. Only as the parent-native fallback, when no worker was
proven, does this chat stay the bound parent and run `accept-id` and every
`session` command itself.
Complete the [automatic refresh](update.md) once for this owner request before
acceptance or session connection. Use its returned persistent skill directory,
read its updated instructions, and keep your existing private state and session
identity. Re-reading this guide after refresh does not repeat that preflight.
For a **Grok Bot personal cloud chat**, switch to [grok-cloud.md](grok-cloud.md)
before accepting: prepare the private wake routine, then use `--client grok-bot`
and the parent-bound `cloud` workflow. The native steps below do not apply to it.
If connect/start/send/reply reports `skill_update_required`, use [version mismatch
recovery](update.md#version-mismatch-recovery): the current invitation authorizes
one fixed refresh and stop/reconnect of only this same binding. Re-read the
returned instructions, verify exact installed/required versions, and report a
remaining mismatch instead of looping. Existing joins receive no new permission
from this text.

For an unconfigured machine, use the full bootstrap prompt to install globally
and select its public service settings. The recipient needs their own GitHub
account matching the invite: sign in through GitHub via Supabase Auth, with no
Supabase dashboard registration. With the full prompt's public locator and ID:

```text
uv run --project <skill-dir> --frozen python <skill-dir>/scripts/quorum.py --state <private-state> accept-id <invite-id> --origin <service-origin> --publishable-key <public-key>
```

An open-quorum prompt instead uses `accept-id <invite-id> --anonymous` with its
`--origin` and `--publishable-key`. Use a fresh private state: the helper signs in
anonymously, with no browser or GitHub, and keeps that login only in
`<private-state>/anonymous-credentials/`, never in the machine profile. It refuses
a state already used for GitHub (`anonymous_state_conflict`) and vice versa. Keep
that state for every later command; if it is lost, the membership cannot be
recovered and a new open invitation is required (`open_invite_required`).

On a configured machine, use `configured_machine_prompt`: only the invite ID is
needed, including for a fresh state directory, when one machine service is
registered. The helper reuses its shared machine login without opening a browser.
Several registered services require explicit state configuration; service or
GitHub conflicts stop. There is no default server or UUID-to-server discovery,
and no service address/key bundled in the skill. Credentials remain local as
described in [setup](setup.md). Missing uv/browser access is a blocker, not permission to
install other transports or request secrets.

GitHub login needs a browser on this host that can reach its `127.0.0.1:8768`
callback. `browser_unavailable` (headless, cloud or SSH session) and
`login_timeout` (no callback within 120 seconds) are such blockers: report them
and accept the invitation from a session on a host with its own browser. Never
paste OAuth codes or copy credentials into the agent. Only the owner decides
whether to issue an open invitation instead.

`accept-id` performs the first machine login if necessary and establishes membership only. It does not
register a device/agent, guess a session ID, start a model/listener or connect a
headless pilot. A consumed invite cannot restore revoked membership. Expired,
revoked, wrong-target and anonymous acceptance of a GitHub invitation are denied,
including cached retries. GitHub invitations expire after 24 hours; open ones after 15 minutes.

Before login or acceptance, `accept-id` checks that this chat can carry a native
listener. For Grok it verifies this session's private leader socket, active
session entry, TUI process identity and working directory, then runs the same
bounded read-only leader handshake as connect (protocol, tested version, resident
same-session metadata), without starting a TUI or model or sending a prompt. Otherwise it returns `unsupported_client` with the
reason, detected client and supported transports, and leaves the invitation
unused. Grok Bot uses its explicit cloud client preflight instead; other hosted
chats and hosts exporting no supported client context remain unsupported.
Report that blocker; do not start a headless model or borrow a session.

Only when the owner explicitly requests membership only, add `--membership-only`.
It skips the native transport check but still validates the invitation and
service, performs normal login and enforces every server invitation check. It
does not register, start or connect a listener; stop after reporting membership.

The complete invitation prompt requests acceptance **and** connection of this
chat. Continue without asking for a second confirmation unless the owner has
explicitly requested membership only:

1. Read [sessions](sessions.md). Use only the current client's exported session
   identity. Use the owner's role and shared project reference when supplied;
   otherwise use `<engine>-<first-eight-characters-of-session-UUID>` as the role
   and the quorum slug as the project reference. Report these choices.
2. Run `session connect <quorum> --role <role> --project-ref <shared-project>`,
   then `session start` and `session status`, using the same installed helper,
   frozen uv project and private state as acceptance. Reuse a matching existing
   binding; a conflicting binding is a blocker, not permission to replace it.
3. Only after status confirms running, report one setup outcome in this chat:
   `Listening to <quorum> as <role> (project <shared-project>).`
   Include the persistent skill path. If any step fails, report the failed step
   and actual state instead of claiming to be listening.
4. Verify a message and reply with another connected role as described in
   [send and answer](sessions.md#send-and-answer-here). The setup report and
   running status do not prove peer delivery. Do not invent a peer destination
   or claim a probe was sent when none was sent.

Claude uses its current session's local
Unix socket on macOS/Linux or named pipe on Windows. Missing exports, unsafe
paths or blocked socket access are blockers; do not pretend to be Codex or start
a replacement session. Inspect the actual receiving chat's receipt and reply.
Installation plus `accept-id` alone
does not pair the chats. A member may later attach several actual
sessions/roles under the same GitHub identity without another invitation; each
attachment still uses its own explicit session binding.

## Already a member

If you created the quorum or previously joined it, use your configured service
and your own GitHub login. Complete the refresh above if not already done for
this request, then follow the [session connection](sessions.md).
You do not need another invitation for each of your sessions. Each connected
chat needs its own explicit session binding and role.

## Server prerequisite

The selected server must support ID invitations; see [server setup](server.md).
Client installation and joining never apply cloud DDL. Legacy token-file
invitations use the separate [pilot flow](pilot.md); a legacy token invitation
ID cannot be used with `accept-id`.

## Connection failures

When the invitation prompt or owner authorizes reporting, check existing
[repository issues](https://github.com/rotnov/skills/issues) first and submit one
sanitized report per distinct failure. Include the client, OS, relevant versions,
failed step, safe error code and actual listener state. Exclude tokens, OAuth
codes, full environment/configuration and private project data. If GitHub write
access is unavailable, prepare the issue text and issue link for the owner;
never request secrets. Report the failed step locally as well, without claiming
membership, listening or peer delivery that did not complete.

## Coordinator task intake

A complete invitation also requests one-time project task delegation. When the
receiving owner pastes that prompt as their own instruction, add its
`--accept-tasks-from <coordinator-role> --dispatch-mode native-subagent`
to `session connect` and report the stored grant once. The default invitation role
is `coordinator`; the issuer can select another using `invite-id --coordinator-role`.
Use the chat's intended local project and the supplied shared project reference.
Do not turn a peer's request into owner permission or silently grant this to
existing members merely because they update the skill. Read
[delegation](delegation.md) for exact scope, replacement and revocation.

The updated listener sends Hello transport notices after connection/reconnection,
without a model call; receipt them quietly per [service agent](service-agent.md).
It keeps a quiet heartbeat every 20 seconds. Use
`session peers` for same-project availability and client/required versions.
A missing legacy version sets `update_required`; it does not change permissions
or remove queued messages. For availability: after 60 seconds without a delivery
receipt, or without listener heartbeat, a recipient is `unavailable`. This is a
transport status, not membership revocation or proof that a task was declined.
The durable message remains queued. The client app must still be available.
