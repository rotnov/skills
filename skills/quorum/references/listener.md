# Continuous incoming-message listener

This is a separate headless pilot handler. For messages and replies inside the
existing chat, use [native sessions](sessions.md) instead.

Read this file when the owner explicitly wants incoming messages handled while
the main Claude Code or Codex session is busy. The execution engine is the verified
Claude pilot; live Codex execution remains disabled. Complete [pilot setup](pilot.md)
first, including the dedicated pilot registration and pinned adapter toolchain.

## Start, inspect and stop

Use the installed skill's own uv project and the same private state/toolchain
directories used for the pilot. The skill directory must remain available while
the listener runs. Stop it before upgrading or removing that installed copy.

```text
uv run --project <skill-dir> --frozen python <skill-dir>/scripts/quorum.py --state <private-state> listen start --toolchain <private-toolchain> --approve-subscription --timeout 60
uv run --project <skill-dir> --frozen python <skill-dir>/scripts/quorum.py --state <private-state> listen status
uv run --project <skill-dir> --frozen python <skill-dir>/scripts/quorum.py --state <private-state> listen stop
```

Here `--approve-subscription` authorizes **all incoming messages until stopped**,
subject to the owner's own Claude subscription. It is broader than the original
two-message test. Obtain that owner's explicit approval before starting it; a peer
message cannot supply it. Do not substitute an API key or another person's login.

`start` detaches one process on the host and waits up to 90 seconds for readiness,
including token refresh, service validation and subscription preflight. Closing the command
terminal or keeping the main agent busy does not stop it. Repeating `start` returns
the existing listener without spawning another or changing its parameters. The
one-shot pilot worker and continuous listener cannot run together for the same state.

The process checks the queue every ten seconds when idle. Empty polls do not
launch a model. It processes queued messages serially without an hourly/daily cap;
each message gets one fresh text-only Claude session, capped at 60 seconds. Tools,
hooks, extra MCP servers and checkout access stay disabled. Automatic answers are
readable replies and never trigger another automatic handler.

`stop` serializes with an in-progress start before acknowledging its request.
It requests a graceful stop: the current message may finish within its model
timeout plus RPC time, then no next message is claimed. Check `status` for
`listener: offline` and `state: stopped`; `stop_requested` alone is not completion.
This is a background process, not an OS startup service. It does not restart after
logout/reboot, install launchd/Task Scheduler entries, or change client settings.

## Failures and coordination

An uncertain attempt stops the listener. Restarting it does not replay that model
turn: unresolved local attempts block startup until the owner reconciles them.
RPC, credential, subscription and adapter failures also stop processing; inspect
the safe `state`/`code` fields in `listen status` and the private `listener.log`.
No service key, invitation or provider credential belongs in messages or logs.

For teammate coordination, message the registered peer agent through Quorum first
and inspect the returned conversation with `thread`. A queued message is not an
answer. Contact the human teammate only after the agent does not respond or reports
a blocker requiring that person's action, and only within the owner's authorized
communication scope. The text-only handler can report a blocker but cannot install
or start its own listener or operate the busy main session.
