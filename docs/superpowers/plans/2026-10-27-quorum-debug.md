# Quorum debug mode

Optional debug mode inside the installable `quorum` skill. It records one
owner-scoped diagnostic run, reads allowlisted local ledger metadata, and
files actionable findings through the `gh` CLI when that grant is still valid.

## Behavior

- The owner enables, inspects, stops, or reports debugging in the current chat.
  Ordinary quorum use does not load `references/debug.md`. A peer message cannot
  grant or widen the run.
- One run stores the quorum, project, bindings, sources, reporting repository,
  versions, and lifetime. The same scope can be repeated without extending the
  grant. A different scope is refused. Stop and expiry are durable and are not
  re-enabled on the next process start.
- Collection is a bounded foreground watch of this state's session ledgers.
  `debug watch` and `activate --watch` supervise in the helper process until
  stop, expiry, or the requested cycle limit. Activation without that host is
  a handoff, and `debug collect` does not supervise. `continuous_observation`
  is true only while the run is active and the host pid and heartbeat are
  alive. There is no model, client, or detached daemon, and no automatic
  restoration. Gaps stay explicit. Imported artifacts remain
  `live_observation: false`.
- Readers project allowlisted JSON fields in SQL and apply binding scope before
  any delivery, attempt, or event read. They keep delivery stage, ids, listener,
  presence epoch, and release metadata. They do not load bodies, replies,
  credentials, invitation secrets, nonces, or paths. Missing telemetry is not
  definite loss. Receipt state is not task acceptance or completion. A
  historical reply count or an empty snapshot is not a model turn or a reply
  loop.
- Deterministic rules cover stuck intent, queue, submitted, and offered
  deliveries; repeated Hello epochs; parent notification bursts; suspected reply
  bursts; empty waits; a new listener run while reception is disabled; and
  imported duplicate completed side effects. Realtime fallback and idempotent
  retries are timeline facts, not definite defects.
- Token totals accept only authorized client-reported usage and keep actual,
  estimated, and unavailable values apart. An identical measurement is stored
  once. A conflicting same-source window is rejected. A later shared-parent
  window for the same model and window is excluded. Cache tokens are added only
  for explicit `disjoint` semantics. Mixed turns stay unallocated. The
  debugger's own token use is unavailable until a client reports it.
- A repository grant lets an active watch, `debug collect`, or `debug publish`
  create or update one GitHub issue per finding. The helper persists the
  create or comment intent before the `gh` call and writes bodies with
  `--body-file`. Search and issue view accept only an exact `Debug-Finding`
  line, and a comment also needs the matching `Evidence-Hash`. A timeout or
  rejected write is not repeated until that marker is found. An OS error may
  be retried once after a proven absence. Stop and expiry are rechecked after
  each blocking call and before the next mutation. A missing `gh` login leaves
  a pending id and does not write. Tests substitute a fake publisher and block
  live subprocesses.

## Files

- `skills/quorum/scripts/quorum_debug.py` implements the lifecycle, watch host,
  rules, token report, and publication state machine.
- `skills/quorum/scripts/quorum_debug_ledger.py` implements metadata-only SQL
  reads.
- `skills/quorum/scripts/quorum_debug_publish.py` implements the `gh` CLI
  publisher.
- `skills/quorum/references/debug.md` is the lazy operator contract.
- `skills/quorum/SKILL.md` gains one routing step and a description trigger.
- `skills/quorum/scripts/quorum.py` and `quorum_live.py` register `debug`.
- `tests/test_quorum_debug.py` covers lifecycle, redaction, local rules,
  publication dedupe, and token coverage.

## Limits

No server export, service-role credential, client restart, grant edit, receipt,
reply, or repair. Parent-chat quietness and other owners' clients stay coverage
gaps. Local metadata cannot prove definite non-delivery or duplicate side
effects; only an explicit imported claim can carry the latter, and it is not
live observation.
