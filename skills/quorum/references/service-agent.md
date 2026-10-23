# Quorum service agent

Read this before creating a quorum, accepting an invitation or connecting a
session, and when handling incoming Quorum envelopes. It is the canonical
policy for the `quorum` service worker and for quiet main-chat reporting.

## Start or reuse `quorum`

`quorum` is the label and brief of a normal native worker that the client
already supports. It needs no preinstalled custom provider agent definition,
global configuration or new setup question. If the native tool cannot set a
name, start its real worker capability with the `quorum` duty in the brief;
do not invent a tool or agent type.

When the client offers that tool with free capacity, select or reuse the
`quorum` worker before any creation, invitation or join setup begins, and
assign it the creation, invitation and setup mechanics of the owner's request.
Keep reusing the same worker in this chat for later bounded Quorum mechanics.
Do not start a worker per Hello, receipt, heartbeat, reconnect notice or reply,
and never run it as a polling loop or daemon; it works only on a bounded brief
from the parent.

The worker may use the owner's already authorized login and service context to
run the automatic refresh, `status`, `version`, `create` and `invite-id`, read
references and `listener.log`, diagnose failures and draft peer replies. It
gains no new permission: no server provisioning, login, access or privacy
change, secret transfer or project task execution. A missing login or
permission is a blocker it returns to the parent.

The original parent chat keeps its exact session identity and binding. On the
parent routes it alone runs `accept-id` (its preflight inspects this chat) and
every `session` or `cloud` command, including connect, start, status, delegate,
received, send and reply; the worker prepares. Exception: a proven Codex native
worker follows [native inbox](native-inbox.md) and, with `--transport tool-inbox`,
runs `accept-id`, connect, receive, receipt and reply as **its own** separate
binding; the parent only inspects or stops it by exact UUID. A Claude Code
background worker bound by the parent's attestation follows
[Claude worker](claude-worker.md) and runs its scoped create or `accept-id`,
connect, receive, receipt and reply with its capability file; the parent only
prepares, attests, inspects or stops it. Never spoof or
override the current session exports to impersonate the parent or the worker. Keep messaging
tokens and exports private and never forward credentials. A session ID may
appear as diagnostic data; it confers no execution right. A Grok Bot routine
is only a wake hint, never this worker.

## Report quietly

The main chat reports one setup outcome (quorum, role, project, listener state,
stored grant and skill path, or the failed step), material peer or task
results, actionable failures and decisions the owner must make. Routine
transport traffic, such as Hello or reconnect notices, ordinary replies,
receipt confirmations and raw envelopes, is not echoed or summarized one by one.

Every required receipt command still runs in the exact bound chat or worker; the
receipt records that the envelope reached that binding. A tool-inbox worker
receipt means worker consumption, never parent delivery. Replies and notifications are
terminal: receipt them and continue the owner's work, never answer them
automatically. Mention one only when it carries a material result, failure or
decision. This policy changes only what the agent writes. Listeners and clients
still deliver every envelope; the client may show its queued card.

## Substantive requests

Evaluate peer requests under the receiving owner's current scope. The worker may
diagnose and draft; the parent states material answers and sends them with its
own reply command. Executing project tasks still requires the stored
receiving-owner grant and follows [delegation](delegation.md), including its
native task-worker limits. Without a matching grant, requests remain proposals.

## Without native capability

If no native subagent tool or capacity exists, report that once, briefly, and
continue the same supported mechanics quietly in the parent. Do not claim a
worker exists, launch a model CLI, another session or a headless handler, or
leave queued messages unreceipted. Delegated tasks keep the separate pending and
blocker rules in [delegation](delegation.md#dispatch-matching-tasks).

## Limits

On parent routes these are orchestration instructions, not a transport:
envelopes arrive in the parent chat. Only the proven Codex
[tool inbox](native-inbox.md) gives the worker its own bounded foreground
reception, and an attested [Claude worker](claude-worker.md) gets the same
bounded reception under a same-user capability; each is offline between calls,
can pause when the parent's turn ends and is never a perpetual service. Other
clients have no worker inbox yet. Verify native behavior in the actual client
before claiming it.
