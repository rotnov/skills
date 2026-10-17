# Delivery routes and failures

## Claude to Codex

The worker that `send --to codex` starts tries routes in this order
(`--codex-mode auto`, the default):

| Route | Used when | Answer comes from |
| --- | --- | --- |
| `daemon` | The shared Codex app-server daemon socket exists (`~/.codex/app-server-control/app-server-control.sock`). A thread nobody hosts is loaded with the topic's sandbox and `approvalPolicy: never`; a thread the daemon already hosts keeps its own settings. Waits while another turn runs. | `turn/completed` events |
| `exec` | No daemon. Runs `codex exec resume UUID` with the topic's sandbox. Waits while the thread log shows a running turn. | the `-o` last-message file |
| `queue` | The thread is hosted by another app-server, usually the Codex desktop app (`daemon` or `exec` reports "active writer"). Runs `codex queue --thread UUID`. | the thread log, once that turn ends (up to 6 hours) |

A queued message runs after Codex's current turn. In an idle desktop thread it
waits until the user sends it from the queue; the Claude session receives a
"queued" notice immediately. One message is processed at a time per topic, so
later messages wait behind a queued one.

Pin a route with `pair set TOPIC --codex-mode daemon|exec|queue`. A pinned
`daemon` or `exec` reports a desktop-hosted thread as a failure instead of
queueing. Change the sandbox for bridge-started turns with
`pair set TOPIC --codex-sandbox workspace-write`; it is always `read-only` or
`workspace-write`, and is also pinned as the matching permission profile
(`default_permissions` `:read-only` or `:workspace`), which Codex 0.138+ prefers
over the sandbox mode. Extra `--codex-arg` values may only choose the model:
`-m MODEL`, or `-c` with `model`, `model_reasoning_effort`,
`model_reasoning_summary` or `model_verbosity`. Any other value is dropped.

## Steer

`send TOPIC --to codex --steer --text TEXT` adds the message to the active turn
of a daemon-hosted thread. It is refused, and nothing is sent, when there is no
daemon, the thread is not loaded there, or no turn is running. Threads open in
the Codex desktop app cannot be steered from outside; send normally instead.
Steering a turn the bridge started returns the result with that turn's reply;
steering another turn returns that turn's final answer as a separate reply.

## Codex to Claude

`send TOPIC --to claude` writes one frame to the Claude session's inbox socket.
`--priority next` (default) starts a turn in an idle session or arrives between
tool calls; `--priority later` waits until the session's current turn ends.

## Protocol refresh

The first message to a thread, every handshake, and the first message after a
context compaction in the Codex thread log or a `pair rename` carry the full
protocol. Other messages carry a one-line reminder. `pair handshake TOPIC`
resends it, for example to tell Codex a new topic name right away.

## Failure notices

| Notice | Meaning and next step |
| --- | --- |
| "Delivery to Codex failed ... stayed busy" | The thread kept working for 15 minutes; resend later. The message is in `failed/`. |
| "... hosted by another app-server" with a pinned route | Use `auto` or `queue` for desktop threads. |
| "no answer within ... h" | A queued message was not answered; check the desktop queue. |
| "Claude session socket unreachable" | The Claude session restarted or ended; run `pair refresh` in it, then `deliver TOPIC`. |
| "steer not possible" | See the steer conditions above. |
| "has a delivery in progress" | A live worker owns the topic; retry after its reply. Markers left by a killed worker are cleared automatically. |
| "another pair change is in progress" | A pair create, rename, removal or setting change is running; retry. |
| "session identity is missing" | Run the command inside the paired session: Claude Code's Bash tool or the Codex thread's shell. |
| "too long for the Codex desktop queue" | `codex queue` takes the message on its command line (about 32,000 characters on Windows, 128 KB elsewhere); write the content to a file and send its path. |
| "rate limit" | 20 messages per sender per 10 minutes per topic; batch them. |

Inspect with `inbox TOPIC` and `read TOPIC ID`. Messages are JSON files under
`$AGENT_BRIDGE_HOME/pairs/TOPIC/mail/`; `log.jsonl` records events without text.
A steer waits in `steering/`, outside the delivery queue, until the daemon
accepts or refuses it. A Codex answer longer than 200,000 characters is saved in
full under `long/` and reaches Claude shortened, with that path.
`deliver TOPIC` retries pending deliveries; `watch` keeps delivering for all
topics. `pair remove TOPIC`, and `pair create --force` on an existing topic, move
the whole topic directory under `$AGENT_BRIDGE_HOME/retired/`, so a new binding
with that name starts with an empty mailbox.
