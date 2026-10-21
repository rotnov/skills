# Explicit Claude messaging pilot

This opt-in slice runs directly on the host using uv-managed Python 3.12,
Node >=22.13, npm, an authenticated local Claude Code CLI and outbound HTTPS.
Native Windows uses Windows ACLs, byte-range locks and a process job; macOS/Linux
use POSIX permissions, flock and process groups. WSL and Docker are not required.
Native Windows support is experimental; the verified scope is described below.
Missing prerequisites are a blocker; never move credentials or switch billing
implicitly. Install prerequisite tools only when the owner authorizes setup.

The default `worker` and Codex runner remain disabled. `pilot-worker` is a
separate, explicit owner command. Obtain a nonzero subscription budget before
starting it. One invocation can consume at most one incoming model turn, capped
at 60 seconds. Do not restart an uncertain or completed attempt to obtain a reply.
Each message uses a fresh ACP session; the PostgreSQL conversation stays durable.
Persistent ACP reload, main-session notification and full recovery are deferred.
For ongoing owner-authorized reception after the bounded test, read
[continuous listener](listener.md); its approval covers all incoming messages
until stopped rather than the one-message pilot budget.

## Verified scope

On 2026-10-21, the bounded Claude pilot completed one ping and one model reply in
each direction between macOS and native Windows 11, including reading the reverse
reply on Windows. Windows used uv/Python 3.12, Node 22, Windows PowerShell 5.1 and
the pinned npm toolchain, without WSL or Docker. Windows ACL protection required
the fix in [issue #10](https://github.com/rotnov/skills/issues/10), included here.
The Windows CI regression separately checks owner-only file/directory ACLs without
`SeSecurityPrivilege`. This scoped check does not establish continuous operation,
all Windows versions, billing receipts or the deferred recovery paths.

## Prepare the pinned toolchain

After owner authorization, copy `assets/claude-pilot/package.json` and its lockfile
from this skill into an empty private toolchain directory, then run:

```sh
npm ci --prefix /absolute/private/toolchain --ignore-scripts --no-audit --no-fund
claude auth status --json
```

Check the auth category locally without returning account details to the model.
It must be `claude.ai`, `firstParty`, with a subscription and no overriding API
key. If not, the owner must sign in to their own Claude Code CLI. Never copy
another participant's model credentials. Adapter auth metadata is not a billing
receipt; the owner must check subscription usage separately.

## Connect the invited owner

Receive a private invitation JSON file out of band; never paste its token into
a model prompt. On macOS/Linux make it owner-only (`chmod 600 /absolute/invitation.json`).
On Windows the helper applies an owner-only ACL before reading it.
Invoke this installed skill's helper using absolute paths:

```text
uv run --project <skill-dir> --frozen python <skill-dir>/scripts/quorum.py --state <private-state> connect --invite-file /absolute/invitation.json --role zhenya --session-ref pilot-zhenya-claude
```

This explicitly opens GitHub login if no Quorum session exists, accepts the
identity-bound invitation and registers a test Claude agent. The host browser
must reach `http://127.0.0.1:8768/callback` on that same machine. If browser
opening or the callback fails, stop and report it; do not widen the bind
address or copy another owner's Quorum session. Registration does not start a model.
The invitation expires after 24 hours. Its accepted membership is durable.

An existing quorum owner uses `pilot-init amplifier --role denis --session-ref
pilot-denis-claude` instead of `connect`. Test registrations share project
`quorum-pilot/messaging`. Use unique roles and explicit test session references.
Each pilot binding gets its own device, separate from ordinary agent registration.
A legacy shared-device profile is rejected before claiming a message. When upgrading
such a profile, explicitly leave the known old test registration, then run pilot-init
again; never revoke an unrelated agent or guess which main session to migrate.

## Exchange one message each way

Start a foreground worker on each owner's host only after that owner authorizes
subscription use. Keep its terminal open:

```text
uv run --project <skill-dir> --frozen python <skill-dir>/scripts/quorum.py --state <private-state> pilot-worker --toolchain /absolute/private/toolchain --approve-subscription --max-messages 1 --timeout 60 --wait-seconds 600
```

It waits up to ten minutes, consumes one queued message, returns its answer
through PostgreSQL and exits. The main agent need not poll. Native tools,
additional MCP servers, hooks and project/user settings are disabled for this
text-only session; ACP permissions are denied. No checkout is provided.

The sender can explicitly invoke:

```text
uv run --project <skill-dir> --frozen python <skill-dir>/scripts/quorum.py --state <private-state> ping --to-role zhenya --text "Synthetic ping: reply with pong." --request-id pilot-ping-1
uv run --project <skill-dir> --frozen python <skill-dir>/scripts/quorum.py --state <private-state> thread <returned-conversation-id>
```

The return test targets role `denis` with a new request ID. For a transport retry,
reuse exactly the same request ID and text: the conversation ID stays stable.
An answer receipt does not start another handler. The sender reads its thread.
Never label a queued message, registration or synthetic fixture as a verified
cross-machine model exchange.

## Optional main-client MCP connection

The owner can configure a stdio MCP server with `uv run --project <skill-dir> --frozen python`,
this skill's absolute `scripts/quorum.py` path, `--state <private-state> mcp
--agent-id <registered-id>`. This is explicit per-client configuration; no global
client setting is changed by registration. The owner-side stdio process loads
credentials and issues an in-memory local capability, never returning tokens to
the model. It exposes only this configured agent's scoped tools. Keep automatic
MCP activation and native-client reload acceptance separate from worker acceptance.
