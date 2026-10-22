# Join a quorum

Read this when accepting an invitation and connecting your actual chat.
For issuing an invitation, read the [owner guide](invite.md).

## Join this chat

Install the public skill using the prompt. The recipient needs their own GitHub
browser login; no Supabase management connector, shared login or model subscription
credential is required. With the prompt's public locator and ID:

```text
uv run --project <skill-dir> --frozen python <skill-dir>/scripts/quorum.py --state <private-state> accept-id <invite-id> --origin <service-origin> --publishable-key <public-key>
```

An existing explicitly configured service may omit the two public flags. Supplying
flags that conflict with the saved service stops before login; use a separate
private state directory for another instance. There is no default server and no
server address or key bundled in the skill. Credentials remain local as described
in [setup](setup.md). Missing uv/browser access is a blocker, not permission to
install other transports or request secrets.

`accept-id` logs in if necessary and establishes membership only. It does not
register a device/agent, guess a session ID, start a model/listener or connect a
headless pilot. A consumed invite cannot restore revoked membership. Expired,
revoked, wrong-target and anonymous acceptance are denied, including cached
retries. Invitations expire after 24 hours.

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
3. Only after status confirms running, greet the owner in this chat:
   `Hello! Listening to <quorum> as <role> (project <shared-project>).`
   Include the persistent skill path. If any step fails, report the failed step
   and actual state instead of claiming to be listening.
4. Verify a message and reply with another connected role as described in
   [send and answer](sessions.md#send-and-answer-here). The local greeting and
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
and your own GitHub login, then follow the [session connection](sessions.md).
You do not need another invitation for each of your sessions. Each connected
chat needs its own explicit session binding and role.

## Server prerequisite

The selected server must support ID invitations; see [server setup](server.md).
Client installation and joining never apply cloud DDL. Legacy token-file
invitations use the separate [pilot flow](pilot.md); a legacy token invitation
ID cannot be used with `accept-id`.
