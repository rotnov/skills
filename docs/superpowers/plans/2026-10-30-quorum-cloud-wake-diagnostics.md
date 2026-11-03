# Cloud wake diagnostics implementation plan

> **For agentic workers:** Use superpowers:executing-plans inline and superpowers:test-driven-development for behavior changes.

**Goal:** Meet #53's explicit diagnostic alternative where Quorum has no qualified automatic wake path for the original cloud chat.

**Architecture:** Add a read-only response annotation to native and Grok Bot peer views. Preserve authenticated server health and receipts. Do not infer hosting provider or suspension from engine, role or a timeout. Explain each cloud adapter's actual boundary in one conditionally routed reference.

**Tech Stack:** Existing Python CLI and unittest; no new dependency or backend change.

**Spec:** [#53](https://github.com/rotnov/skills/issues/53), including its unsupported-diagnostic acceptance alternative. Current provider audit is private evidence, not live wake proof.

## Global constraints

- No webhook, secret, wake POST, keep-awake loop, replacement session or model call.
- Preserve original online/unknown/unavailable and reason fields; unknown hosting stays unknown.
- A message remains queued; never ACK it to hide unavailable reception.
- Claude's documented existing-cloud-session follow-up is an investigation lead, not qualified hibernated Quorum wake.
- All seven gates, independent review and exact-head CI before merge.

## Review focus

Check both peer commands, authentication denial, unchanged response data, no speculative sleeping/provider classification and truthful documentation.

## Task 1: Behavioral peer diagnostics

1. Write CLI tests using the real dispatchers with synthetic authenticated APIs and existing exact native/cloud bindings.
2. Observe missing wake guidance for unavailable peers in both routes.
3. Add one pure shared annotation helper; unavailable rows get fixed `unsupported`, unknown provider/suspension and owner-open guidance. Other rows remain unchanged.
4. Denied membership still fails before peer disclosure; no wake, receipt, send or background process occurs.

## Task 2: Provider boundaries

1. Add `references/cloud-wake.md` with current official Grok Bot, Claude cloud and Codex cloud sources.
2. Route to it only for unavailable peers or sleeping-cloud questions.
3. Explain that stale health cannot identify hibernation, and opening/recovering the original bound chat is the current fallback.

## Task 3: Qualify source

Run focused suites and all seven repository gates. Obtain independent immutable-head review, create a PR and require all exact-head CI. Report source diagnostics separately from any unperformed provider wake test.
