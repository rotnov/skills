# Quorum project knowledge implementation plan

Issue #59, approved bounded design: `docs/specs/2026-10-30-quorum-project-knowledge.md`.
Source and synthetic tests only. No live deployment, provider calls, model download,
automatic capture, or semantic-quality claim. Migration reserved: `20261030142000`.

1. Add failing client and disposable PostgreSQL tests for exact binding/space grants,
   idempotence, revision CAS, owner verification, bounded lexical/vector retrieval,
   revocation, lifecycle invalidation and independently copied skill commands.
2. Implement private RLS project spaces, explicit attachments/individual grants,
   immutable revisions, derived pgvector rows and narrowly granted authenticated RPCs.
   Keep all authorization ahead of cached responses and transactional writes.
3. Add portable explicit commands, strict owner-assertion inputs and proposal-only
   finite capture grants pinned to binding, space, quorum, project and cwd. Keep
   provider generation absent. Document fallback and deployment requirements.
4. Run focused tests, actual pgvector PostgreSQL tests in a digest-pinned disposable
   network-none container, local clean Codex/Claude copy tests, validation and full
   unit suite. Report evidence and remaining embedding/live acceptance gaps.

Parent owns independent review, all seven publication gates, integration and PR.
No commits or changes to existing required release metadata.

## R2 review corrections

Root reproduced changed worker proof reaching capture transport and new knowledge
commands bypassing release checks. Preserve the original frozen R1 patch. Add
actual CLI/QuorumApi RED regressions, knowledge-only complete stable snapshots,
fresh local proof/cwd checks around blocking token/profile work, and a guarded
ReleaseGuard check before remote operations. Do not change existing reception or
logical-worker capabilities or grant lifecycle control. Local grant revocation
remains local. Strengthen the real CAS test to distinguish pre-operation idle
from the actual completed revision and require the exact blocking backend.

Independent client review additionally reproduced deeply nested JSON escaping as
RecursionError and nonfinite private grant expiries reaching remote calls. Add
actual CLI RED cases, reject excessive JSON structure/decode recursion locally,
and require a finite numeric expiry within the original one-day grant bound.
