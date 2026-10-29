# Recover reception after a CLI resume

Read this only when an already connected **Codex, supported macOS Grok or
Claude Code** conversation resumes or its listener stopped, or its owner requests
automatic recovery for that binding. This page does not apply to cloud chats or
native worker tool inboxes.
For a native Claude chat, reconnecting first preserves the old endpoint until
start or authorized recovery stops and rebinds its listener.
See [Claude transport recovery](sessions.md#claude-code--macos-linux-and-windows)
and [delivery reconciliation](sessions.md#failure-and-recovery) for definite
no-write outcomes and owner-attested absence of an ambiguous attempt. Do not
infer absence from a dead socket or replay an unknown intent.

From the same resumed chat's shell tool, with the same installed skill and private
state, run:

```text
uv run --project <skill-dir> --frozen python <skill-dir>/scripts/quorum.py --state <private-state> session recover
```

Recovery reuses the registered agent, quorum, role, project, delegation and
delivery ledger. It validates this exact UUID and working directory, the service
binding and the current native transport. A valid Grok resume may change its
PID, process birth time and explicit private leader socket. A Claude Code chat
may adopt its own current messaging socket and token exports under the same
`CLAUDE_CODE_SESSION_ID`, with the same endpoint guards as `session start`;
another chat's environment or a stored token is never used. The old listener
stops before the new transport is stored; repeated recovery reuses the live
generation. It never starts, resumes, loads or changes a provider conversation.

Only a successful explicit `session start` with this helper records reception
authority. Older installations need that one initial start from their bound chat;
recovery never infers authority from an old process or registration. `session stop`
revokes recovery atomically even when the listener is already dead. A stopped,
expired, changed or service-revoked binding cannot recover. Only a fresh explicit
owner-authorized start can enable reception again. Recovery never extends a
grant's lifetime or creates another grant. It rechecks authority after blocking
startup work and inside the recovered relay before submitting or acknowledging
messages. Service calls recheck after token resolution and immediately before
dispatch; Realtime token refresh and heartbeats use the same grant. An expired
unconfirmed child is stopped without changing the grant.

An uncertain `intent` remains `session_delivery_unknown`: inspect the actual chat
before existing manual reconciliation. Pending `submitted` messages retain their
receipt requirement and are not replayed. Queue admission and successful recovery
are separate from a message arriving in the actual resumed chat; verify a peer
message and receipt/reply there before claiming runtime acceptance.

## Service outages

A running native listener waits out a network failure or HTTP 408, 429, 500,
502, 503 or 504 without a command. Status shows `state: degraded` with the code
and presence `unreachable`; it retries after 10, 20, 40 and then every 60
seconds, reconciling before any further inbox or receipt call. Authentication or
permission denial, other HTTP errors, release mismatch, stop and revoked or
expired authority stay terminal. Every native run, not only a recovered one,
rechecks its reception authority before token resolution, each service call and
each chat submission, so a stop, expiry, revocation or binding change during an
outage ends it without a later dispatch. A new run started by `session start`
works on that start's authority for at most 90 seconds until the start records
reception after readiness. A degraded start, including one that finds an
already running degraded listener, is not reported as started and enables no
reception (`listener_start_failed` or `listener_not_ready`). Use `session
recover` only after the listener stopped, for example after `listener_exited` or
a terminal error.

## Optional provider startup hook

A copy-installed skill cannot register a startup hook by itself. Without an
owner-installed, trusted hook, use the single recovery command above. There is no
OS startup service or hidden restart loop. Logout, reboot or deletion of the
private state still requires the provider and state to be available again.

For an owner who explicitly requests hook setup, add a **SessionStart** command
handler with matcher `resume`, timeout **180 seconds**, and this command, using
absolute, shell-quoted paths and the actual installed skill copy:

```text
uv run --project '<skill-dir>' --frozen python '<skill-dir>/scripts/quorum_resume.py' --state '<private-state>' --client codex
```

Use `--client grok` for Grok. Claude Code has no startup hook here; run the
recovery command from the same Claude chat. Merge the handler into existing configuration;
never replace unrelated hooks. Setup/configuration is a separate owner action,
not part of connect, start, recover or skill installation. Keep the provider's
normal permissions and trust review; do not disable a policy to make it run.

- **Codex:** use the provider's [documented hooks](https://learn.chatgpt.com/docs/hooks)
  in the intended project `.codex/hooks.json` or another explicitly selected
  configuration layer. Codex requires review and trust of the exact hook
  definition. The adapter uses the provider's documented `session_id` and `cwd`
  payload; an existing conflicting session export is refused.
- **Grok:** use its [documented hooks](https://github.com/xai-org/grok-build/blob/2bdd1d6a6369de0e8c68132ea4539e9abd9e14a8/crates/codegen/xai-grok-pager/docs/user-guide/10-hooks.md)
  in the intended project `.grok/hooks/` or another explicitly selected layer.
  Project hooks require the provider's trust review. The adapter requires the
  hook runner's own `GROK_SESSION_ID` and the owner's explicit
  `GROK_LEADER_SOCKET`; missing exports are a capability blocker. The exact
  [Grok version and process guards](grok.md) still apply; a hook does not qualify
  an untested provider version or promote an ordinary TUI to leader mode.

The adapter reads only a bounded provider lifecycle payload, accepts **resume**
events only, and fails closed for other clients, forks, new sessions, ambiguous
exports or a different binding. Success is silent; a fixed failure notice asks
for inspection from that same chat. No auth repair, secret copying, model turn,
foreign-session operation or global configuration edit is performed.

Source/fixture coverage proves the recovery and hook contracts, not that every
provider build emits its hook after every restart. If a hook is absent, skipped,
late or lacks exports, report that capability limit and use the same-chat
recovery command. Confirm the actual hook plus post-resume message/receipt once
in the owner's exact provider version before reporting automatic UI recovery.

A bounded native Codex **0.162.1** probe confirmed one specific limit: `/quit`
disconnected the frontend while leaving its thread resident in the app server;
`codex resume <same-UUID>` reattached without another `SessionStart` event.
A surviving UUID-bound listener can continue on this path. If that listener
died while the frontend was detached, use the recovery command; a startup hook
cannot guarantee an automatic wake on this reattach. Do not restart a shared
daemon or disturb another chat to manufacture a startup event. This probe
verified lifecycle behavior only, without Quorum registration or delivery.
