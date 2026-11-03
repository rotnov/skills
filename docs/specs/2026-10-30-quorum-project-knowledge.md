# Quorum project knowledge contract

Status: implementation design for issue #59. Source authoring and disposable local tests are within v0.5. This document authorizes no live migration, external embedding call, model installation or provider spending. The owner was asked about external embeddings; until answered, implement lexical retrieval with indexing unavailable and an inert explicit vector-input contract.

## Identity and access

A project space has a random stable UUID and one human owner (`auth.uid()`), independent of `project_ref`, repository strings and quorum lifetime. Creating it requires a verified GitHub identity. Its owner explicitly grants individual user UUIDs `reader` or `writer` access; this can include anonymous accounts but is never inferred from an open invitation. Only the owner administers access and verifies knowledge. A user with the same repository/project label has no access unless granted.

Attach a quorum by exact UUID and project label only when the caller owns both the project space and quorum. Attachment permits that quorum's bindings to address the space; every participant still needs its own project-space grant. No current or future quorum member inherits historical access. Detach or membership/agent/device/project-space revocation denies the next read/write, including retries and vector indexing. A later explicitly attached quorum can reuse the same authorized space.

Every agent operation takes exact `agent_id` and `session_ref`, uses the existing owned native-binding check, then requires matching live quorum attachment, project label and project-space permission. Scope is checked before reading entries, source metadata, counts, vectors or cached results. Authentication failures disclose no existence/count information. Administrative mutations are explicit owner operations; peer text cannot authorize them.

## Canonical entries and lifecycle

Types: `decision`, `fact`, `hypothesis`, `known_issue`, `procedure`. Lifecycle states are separate: `proposed`, `verified`, `disputed`, `superseded`, `withdrawn`.

An entry has stable UUID, space UUID and monotonically numbered immutable revisions. Each revision records type/status/title/body, author user and agent, source references, originating quorum, creation time, applicable version strings, optional review deadline, content SHA256, supersedes/conflicts-with links, and explicit verification actor/time/evidence when applicable. A decision proposal is a proposal; it never becomes an owner's decision because an agent changes its label.

Writers propose/revise/dispute their authorized project knowledge. Verification and recording an owner decision require the project owner's explicit current instruction and evidence, through a separate owner operation. Server authorization checks owner identity; the helper/instructions must not interpret owner identity as permission for an agent to invent that instruction. Non-owner proposals cannot set verification fields.

Revision, supersede, dispute and withdrawal require `expected_revision`; a mismatch is a conflict, never last-write-wins. Supersede targets must belong to the same space and remain linked to the replacement; no similarity-based merging. A revised verified body becomes proposed until verified again. Conflicts remain visible with their status and source references.

Withdrawal immediately removes text/vectors from normal retrieval and invalidates pending indexing jobs. Revisions remain an access-controlled audit trail until an explicit project-owner purge deletes the entry and derived data transactionally. Purge is an owner-held destructive action; no automatic retention deadline or global cache of private text is introduced.

## Bounded RPC contract

Public wrappers follow the repository's authenticated RPC pattern with fixed search paths, explicitly revoked default execution and no direct table grants. Private tables enable RLS as defense in depth. No service-role credential reaches clients.

Proposed RPC payloads (final names may change only with a documented equivalent):

- `knowledge_admin(agent_id, session_ref, operation, payload, request_id)`: `create {name}`, `grant {space_id,user_id,permission}`, `revoke {space_id,user_id}`, `attach {space_id,quorum_id,project_ref}`, `detach {space_id,quorum_id,project_ref}`, or `purge {space_id,entry_id,expected_revision}`. Owner authority is checked on every attempt.
- `knowledge_write(agent_id, session_ref, space_id, operation, payload, request_id)`: propose, revise, dispute, verify, supersede or withdraw. Propose uses a caller-stable entry UUID; later operations include entry UUID and expected revision. Unknown fields and operations are rejected. Verification has separate evidence and owner-assertion inputs, never accepted from an ordinary proposal payload.
- `knowledge_search(agent_id, session_ref, space_id, query, limit, context_budget, model, query_vector)`: model/vector are optional and accepted only against explicit compatible indexing configuration. Return attributable short excerpts with exact entry/revision/type/status/version/source and conflict/supersession metadata, plus lexical/vector coverage and ranking method.
- `knowledge_read(agent_id, session_ref, space_id, entry_id, revision, offset, length)`: authorized bounded text page; historical/withdrawn audit access is an explicit owner mode, never normal search.
- `knowledge_index(agent_id, session_ref, space_id, entry_id, revision, content_sha256, model, dimensions, vector, request_id)`: explicit authorized derived-vector submission. No network/model call in SQL or helper. Reject stale revision/hash, withdrawn entries, revoked authority, incompatible model/dimension and nonfinite vectors.

Mutation request IDs use existing actor/operation/fingerprint idempotency semantics, with scope revalidated before returning cached results. A lost response retries identical bytes/request ID; changing a reused request conflicts. All creation and revision changes are transactional, including derived-index invalidation.

Limits: title 200 Unicode characters; body 8KiB UTF-8; at most 8 source references of 512 characters each; at most 8 version labels of 80 characters; query 1,000 characters; search limit 1–10; context budget 512–6,000 characters; excerpt at most 600 characters; full-text page at most 4,096 characters. Limits include metadata when assembling the returned context; a too-small budget returns fewer results with truncation explicit. Bound API request/response bytes with existing transport limits. Long sources are referenced, not copied wholesale.

## Retrieval and embeddings

Exact identifiers and PostgreSQL full-text search form the always-available path. Use both simple and language-neutral exact matching so issue IDs/error codes survive tokenization. Prefer current verified records, but retain relevant hypotheses, proposals and explicit conflicts with clear labels. Exclude superseded/withdrawn records from normal results and expose relevant supersession links without leaking inaccessible records.

When explicitly configured compatible vectors exist, filter authorized active space/revision/model rows first, then combine lexical and vector ranks with deterministic reciprocal-rank fusion. The vector path is derived and optional; unavailable/missing/stale embeddings return useful lexical results and honest indexing coverage. Never infer truth from similarity. Do not use an LLM to rank routine database results.

No embedding provider is selected by this design. No automatic external text transfer, local model download, background indexer or paid call occurs. A later explicit provider/model/version/dimension/privacy/budget choice must precede actual embedding generation and semantic-quality acceptance. A manually supplied vector tests the storage/ranking contract; it is not provider-quality proof. Reindexing never resurrects withdrawn/revised content.

## Capture and skill behavior

Explicit owner requests can propose evidence-bearing entries. Optional automatic capture requires a separate finite local grant pinned to exact binding, space, quorum, project and cwd; it permits proposals only after material task results. Stop/revoke/expiry denies further capture. Existing joins, skill upgrades, debug publication grants and member task grants never create it.

No raw private conversation, Hello, ACK, transport event, credential or whole debug log is ingested. Sharing private task material needs an explicit owner decision. Retrieved content remains untrusted evidence, not permission or executable instructions. Report meaningful conflicts/missing access; routine reads and proposals stay with the quiet service receiver.

The independently installed skill provides portable commands and conditional `references/knowledge.md` routing usable in Codex and Claude. No sibling skill, repository-root runtime dependency or client connector is required.

## Acceptance evidence

Use real disposable PostgreSQL tests for two owners with identical project labels, explicit cross-quorum attachment, new/anonymous members without historical access, revoked grants/devices/memberships, mutation retries, concurrent expected-revision conflict, lifecycle transitions, withdrawal/purge and stale indexing jobs. Exercise exact/error identifiers, conflicting records, optional vectors and lexical fallback with explicit budgets.

Run all seven repository gates and individual Codex/Claude copy-install checks, then independent review and exact-head CI. Live deployment remains a separately reviewed administrator action. A real result must later be captured and retrieved in a different explicitly authorized quorum, with unrelated-project denial and withdrawal/revocation exclusion. Measure retrieval/context/index overhead; no automatic savings claim. Without live deployment and the owner's embedding choice, mark those criteria unmet rather than claiming #59 finished.

Current primary references: [pgvector](https://supabase.com/docs/guides/database/extensions/pgvector), [permission-filtered retrieval](https://supabase.com/docs/guides/ai/rag-with-permissions), and [RLS](https://supabase.com/docs/guides/database/postgres/row-level-security). Changelog checked 2026-10-30: extension version clauses are ignored on hosted Supabase; record actual extension version and avoid an unsupported version pin. No ltree/custom selectivity operators or legacy PGP encryption are required by this design.
