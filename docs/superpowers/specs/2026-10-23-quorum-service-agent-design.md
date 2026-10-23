# Quorum service agent and quiet reporting

## Owner intent

Creation, join and ongoing Quorum mechanics belong to a native subagent named
`quorum`. Routine transport traffic must not generate chat replies or repeated
status narration in the owner's main conversation. The owner explicitly asked
to put this policy in the skill, creation workflow and invitation prompt.

## Chosen approach

Update the installed behavior contract and the generated incoming/invitation
instructions together. Create or reuse one native `quorum` service worker for
authorized setup and bounded maintenance. Keep the original parent as the exact
session identity for binding, receipts and Quorum sends/replies. This reuses the
existing adapters and does not add a provider daemon or persistent model loop.

An independent hidden inbox would require a supported client delivery endpoint
and a different receipt/lifecycle contract; instructions alone cannot create
one. Dropping notifications in the listener and calling them UI-delivered would
misrepresent receipts. Both alternatives are outside this change.

## Behavior

- On create/join, start or reuse native `quorum` before Quorum setup when the
  client provides a native subagent capability. Creation can be executed by the
  worker using the already authorized owner/service context; no server setup,
  login, secret transfer or new action permission is implied.
- The service worker prepares or performs allowed non-identity-bound mechanics,
  diagnoses status and prepares peer replies. The original parent alone runs
  identity-bound connect/start/received/send/reply commands from its own tools.
- Reuse the service worker for bounded work; no worker per Hello, receipt,
  heartbeat or reconnect notice, no model polling loop. Existing project task
  intake grants and native task-worker limits remain authoritative.
- The main conversation reports a single setup outcome and material results,
  actionable failures or decisions needed from its owner. Routine transport
  events and raw envelopes are not echoed or summarized individually. Actual
  required receipt commands still run, and reply-loop protection remains.
- With no native capability or capacity, give one concise capability diagnostic
  and keep supported mechanics quiet in the parent; never substitute another
  model/session or lose queued work. Do not claim a worker or hidden route exists.
- This is an orchestration/reporting policy. The client may still display an
  inbound envelope in its queue; actual independent native-worker routing and
  delivery during parent inactivity are not established by this change.

## Files and acceptance

Keep `SKILL.md` concise and use one canonical one-level reference for service
worker duties, reporting and fallback. Route creation, join, invitations and
delegation to it. Align native and cloud incoming instructions and generated
invitations without modifying authentication, grants, IDs, release versions,
server SQL, receipt implementation or delivery routes.

Verify existing and adjusted invitation/envelope tests plus independent behavior
scenarios for create, join, routine notices, substantive replies and absent
native tools. Run all pinned repository gates and independent current-head
review. Ship through a PR; merge only when required checks are green.
