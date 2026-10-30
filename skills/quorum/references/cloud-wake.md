# Cloud reception and wake limits

Read this when a cloud peer is unavailable, the owner asks about sleeping chats,
or `session peers` / `cloud peers` reports `wake.status: unsupported`.

## Current adapter support

Quorum has no qualified automatic wake path for a suspended Grok Bot, Claude
Code cloud or Codex cloud binding. Its current fallback is explicit: the
recipient owner opens the **original chat**, then uses that same binding's
supported reception recovery if needed. Keep the message queued. A missing
receipt is not permission to start another session, resend blindly, re-arm a
stopped receiver or mark the message consumed.

The peer commands preserve the server's `availability`, `reason` and health
timestamps. An unavailable row adds `wake.status: unsupported`,
`wake.action: owner_open_original_chat`, and unknown `provider` / `suspension`.
This is a Quorum adapter limit, not a claim that every provider lacks wake APIs.
Online and unknown rows keep their original health data without this hint.
Engine and role do not prove hosting: `grok` can be local TUI or Bot, and Codex
and Claude can run locally or in hosted compute. A heartbeat/receipt timeout can
also mean a network failure, stopped process or busy queue. Never relabel it
`sleeping` or `suspended` without independent evidence.

## Provider boundaries checked 2026-10-30

| Client | Supported facts and remaining boundary |
| --- | --- |
| Grok Bot | [Webhook routines](https://docs.x.ai/grok-bot/skills-routines-and-automations#webhooks) can trigger a routine. The current [parent-bound relay](grok-cloud.md) runs inside the same computer, so it cannot send its own hint while suspended. Quorum has no off-host dispatcher; a webhook response would still need the exact parent chat's receipt/reply. |
| Claude Code cloud | [Existing-session CLI follow-ups](https://code.claude.com/docs/en/claude-code-on-the-web#send-follow-ups-from-the-cli) post to an exact cloud session and exit without waiting for its answer. They require the sender's own account and permitted organization policy. This adapter has no qualified binding to that cloud ID or hibernated same-chat test; keep automatic wake unsupported here. |
| Codex cloud | [Cloud tasks](https://learn.chatgpt.com/docs/cloud) do not establish an external wake endpoint for this original receiving Quorum chat. This adapter has no verified original-chat wake path. A new cloud task or API-created session cannot stand in for it. |

Claude follow-ups are a concrete qualification lead, not a reason to launch a
new local model or to declare wake proven from command success. Do not infer a
cloud ID from a local UUID or use teleport/resume to take ownership. Remote
Control's [process lifetime limit](https://code.claude.com/docs/en/remote-control#limitations)
also cannot make a stopped computer deliver.

## Adding wake support

A future supported route must bind the exact existing cloud chat under its
receiving owner's authority. Any dispatcher must remain available independently
of the sleeping computer, protect owner-supplied secrets, deduplicate bursts and
preserve unknown send outcomes. A response from a webhook or sender API is only
submission; qualify an actual hibernated chat's receipt and answer without a
manual open. Publish reviewed server changes separately for administrator
application. Never run busy loops or keepalive traffic to defeat idle policies.
