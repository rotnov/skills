# Grok Bot cloud transport

## Outcome

Connect an existing Grok Bot personal chat to Quorum. A peer message must be
shown and answered by that same chat, including after its previous turn ends.
Registration, HTTP acceptance and a background spool are not delivery proof.

## Observed provider contract

The controlled owner-approved probe on 2026-10-23 used Grok Bot's native
webhook routine. Its public UI specifies an HTTPS POST to
`api2.cursor.sh/automations/webhook/<UUID>` and `Authorization: Bearer <key>`.
One authenticated event woke the existing Bot and produced its marker in the
original personal conversation. The routine was then paused.

The personal conversation exposes a UUID in `CURSOR_CONVERSATION_ID`. A native
routine run instead exposes a `sand-subagent-<UUID>` value. It is not the parent
conversation. Consequently the routine may relay a wake hint only; it cannot
read or acknowledge Quorum messages or reply on behalf of the parent.

Provider references: [collaboration](https://docs.x.ai/grok-bot/chat-and-collaboration),
[routines](https://docs.x.ai/grok-bot/skills-routines-and-automations),
[shared computer](https://docs.x.ai/grok-bot/computer-and-apps).
The generic Grok Automations HMAC documentation describes a different interface;
do not apply it to the Bot's Bearer-authenticated panel contract.

## Architecture

Add a separate `cloud` CLI for Grok Bot. Keep native Codex, Claude and Grok TUI
session discovery and caller checks unchanged. The cloud caller must have a
canonical UUID-valued `CURSOR_CONVERSATION_ID`, the Grok Bot host marker names,
Linux, and no conflicting native session export. A routine/subagent ID is
rejected. No command overrides or fabricates that identity.

Reuse the existing owned-agent RPCs with `engine=grok` and a namespaced text
`session_ref=grok-bot:<actual-conversation-UUID>`. Store the actual provider ID
and transport explicitly in the private cloud ledger. No new database API or
DDL is needed. A different chat, project or role cannot reuse an existing
binding. The platform's shared computer is an account-level trust boundary;
local files are not isolation between the account's Bots.

The local relay uses existing private Realtime wake hints and authoritative
inbox reads. Its only external action is a small authenticated webhook wake
event containing nonsecret routing/message IDs; it never forwards peer bodies,
credentials, invitations or executable instructions. The native routine posts a
fixed wake hint to its parent. The original personal chat runs `cloud receive`,
shows the envelope and uses `cloud received` or `cloud reply` itself.

## Interfaces

- `cloud configure --json-stdin`: private `{url,key}` input from a secure local
  source; never command-line arguments or ordinary chat. Pin the provider host
  and UUID path. Return no URL or key. Refuse mutation while its relay runs.
- `accept-id ... --client grok-bot`: preflight the real parent context and
  private webhook config before login or consuming an invitation. Ordinary
  native acceptance remains unchanged; membership-only remains explicit.
- `cloud connect <quorum> --role <role> --project-ref <project>`: idempotent owned
  registration, no model or relay launch. Optional scoped task delegation uses
  the existing coordinator-grant contract, with cloud receipt/status commands.
- `cloud start|status|stop`: one owned quiet relay, version and health diagnostics
  and no provider model/API launch. Start inherits genuine parent metadata.
- `cloud receive`: return one authoritative envelope as a tool result, marked
  offered locally but not acknowledged. Repeat until the inbox is empty;
  replay returns the same ID until an explicit receipt.
- `cloud received <id>` and `cloud reply <id> --text-stdin`: require actual parent
  context and a locally offered message. Reply/receipt remains idempotent and
  owned-agent scoped. Never acknowledge a mere wake or HTTP success.
- `cloud send`, `cloud peers`, `cloud thread`: preserve existing authenticated
  project routing, request IDs, normal/urgent ordering and no reply loops.

The webhook module owns private target validation/storage, bounded HTTP and
`CloudWakeRelay(api, ledger, record)`, whose `reconcile()`/`run_once()` match the
existing listener worker contract. The cloud module owns caller binding, CLI,
offered receipts, envelopes and relay lifecycle. It passes its private ledger
to the webhook module; no module stores secrets in ledger settings or returns
them through diagnostics.

## Recovery and limits

Preserve the release guard on every authenticated operation, relay drain,
heartbeat and reconnect. Initial development retains the current 0.3.0/2
manifest for real acceptance against the deployed backend; release numbering
and any live server requirement change are a separate reviewed release step.

A webhook intent is written before POST. A definite rejection stays failed; a
possibly written timeout/transport loss is unknown and never blindly retried.
Successful POST means wake accepted, not message received. One message is not
reposted while its wake is pending. A parent receipt can reconcile its own
offered message; unknown wake handling must preserve the durable inbox.

Endpoint and key are owner supplied, private, bounded and never included in
exceptions or logs. No redirects, alternate hosts, arbitrary ports, query
credentials, shell interpolation or credential-copy workaround is allowed.
New permanent webhook routines require the owner's approval; the approved
diagnostic routine does not authorize production task execution.

The relay requires an active provider cloud computer. Provider hibernation can
stop its local process; this prototype does not promise reception while that
computer is suspended. No Grok steer semantics are claimed. Parent task and
tool permissions remain in force; a webhook never grants authority.

## Acceptance

Unit and integration tests cover parent versus routine context, wrong binding,
preflight before invitation consumption, private configuration and SSRF/error
redaction, one wake per pending message, unknown POST reconciliation, offered
versus received, reply idempotency, release mismatch, membership revocation,
priority and notification/reply-loop boundaries. Native session tests remain
unchanged and pass.

Real acceptance uses the existing Grok Bot chat, a fresh private state and a
short-lived invitation. Show a unique peer message and a same-parent reply;
finish the parent turn and repeat from idle through the webhook. Verify actual
parent IDs and receipts, not only HTTP 200. Then stop only the test relay and
pause the owned test routine. All pinned repository, spec and copy-install
gates must pass before pushing. Merge only after required CI and current-head
review pass.
