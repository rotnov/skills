# Quorum owner update implementation plan

Baseline: `04e2e924b4f22953cd5a1c3bf9d5151107cd6619`; issue #69.

The owner explicitly started v0.5. Root approved the bounded audited design;
routine implementation decisions are delegated. No live updater, authentication,
native lifecycle, backend deployment, required-release change, GitHub write or
commit is authorized in this task.

## Task 1: Process-specific source evidence

TDD a source snapshot stable for one Python process and publish it with the
winning listener run. Preserve package release compatibility separately. A new
CLI reports the active run's marker and cannot attribute its own source to it.
Files: quorum_release.py, quorum_listener.py, quorum_native_inbox.py and focused
runtime-source tests. Expected: source mutation and stale-run cases are truthful;
existing release ownership tests stay green.

## Task 2: Explicit journaled update and recipient authority

TDD `update` owner commands that reuse canonical refresh, journal the validated
target before fan-out, resume an uncertain request without refetch, and report
queued/received/refreshed/runtime states separately. Add an explicit receiving-
owner apply path that refreshes locally without fan-out; it never automatically
restarts a runtime. A peer notice alone only proposes the operation. Existing
grants are never widened. Files: new quorum_updates.py, quorum.py, quorum_api.py,
and tests/test_quorum_updates.py. Expected: failure, changed source/same version,
retry and no-recursion tests pass without network or native clients.

## Task 3: Narrow all-quorum maintenance contract

TDD actual disposable PostgreSQL behavior for request/status/report RPCs, typed
maintenance metadata, deduplication per target/recipient and durable phases.
The eligible set spans projects/engines with no session_peers limit. Pin reviewed
function dependencies; do not modify old migration bytes or required releases.
Migration: 20261030140000_owner_update_maintenance.sql (reserved with root).
Files: new migration/backend SQL, backend runner and order assertions as needed.
Expected: actual PostgreSQL tests validate all-quorum coverage, repeated/lost
requests, revocation, malformed target and phase/report ownership.

## Task 4: Documentation and handoff

Document fixed source, explicit apply authority, source-versus-runtime evidence,
the deployment requirement and unqualified actual-client acceptance. Run focused
tests, then the full unit suite. Root owns all seven gates, independent review,
PR and any separately authorized deployment. No partial source result is issue
completion.

## Shared interfaces and review focus

- Task 1 snapshot feeds Task 2 runtime evidence; hash and package version remain
  separate, and markers must match the live run/lock.
- Task 2 sends only bounded immutable target data to Task 3; the server never
  treats caller-reported source or notice metadata as installation authority.
- Receiving-owner apply consumes a notice target and never requests fan-out.
- No changes to onboarding, task-delegation authority or existing capability
  operation allowlists. Root integrates those independently.
- Review authorization after blocking operations, immutable retry bodies,
  unavailable extension behavior, receipt meaning and stopped/expired runtimes.

## R2 review corrections

Independent review of frozen patch0249f9a279b33929d03c445e04db251ceaa9edb70e85d8e0b2ce0d02b6041ccb
reproduced notice refresh/report replay with a new request ID and stale SQL
authorization after a blocked cached request, target or report lock. Root
explicitly authorized the bounded corrections in this tree; no deployment.

- Receiver journal pins canonical notice ID, target and exact receiving binding;
  every invocation still needs owner consent and source revalidation. Completed
  refresh/report is reused across request IDs, and uncertain report retries one
  stable notice-scoped ID without another installer run.
- Mutating maintenance paths take quorum first, then protocol locks and current
  agent/device/membership SHARE locks in stable order; authorization is reread
  after waits and held through commit. Typed receipt adds a post-legacy-write
  check; ordinary receipts keep the reviewed path. Actual PostgreSQL forced
  interleavings cover four waits and all three authority-row revocations.
- Keep original frozen artifacts; root reviews the R2 delta plus final full
  candidate and owns all seven gates, CI union, PR and deployment decisions.
