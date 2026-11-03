# Quorum LLM Wiki Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task.

**Goal:** Implement issue59's retained-source, revisioned-page wiki atop existing scoped knowledge.
**Architecture:** Private immutable sources; existing knowledge entries/revisions/CAS hold pages. Central write and visibility guards cover every legacy and new RPC.
**Tech Stack:** Python3.12, PostgreSQL17, existing optional pgvector, pinned uv0.11.7 and skills1.5.20.
**Spec:** docs/specs/2026-11-03-quorum-llm-wiki.md

## Global Constraints

- Preserve historical migration bytes; additive migration 20261103075741_llm_wiki.sql only.
- Original native binding and existing space scope checked before data and cache access.
- No live DDL, private-chat ingestion, embeddings, extra model sessions or provider spending.
- Sources body <=8192 UTF-8 bytes; page body <=8192; title <=200; source/reference and excerpt scope <=512; author <=200; <=8 sources/related/conflict links.
- Catalog/search limit1–10, context_budget512–6000 serialized characters; source/read slices1–4096.
- All repository gates before commits/push; independently reviewed PR and required exact-head CI before merge.

## Review Focus

- Legacy write bypass and omission of evidence cannot strip wiki metadata.
- Withdrawal/purge cannot leak source-derived excerpts or hidden target IDs in any projection.
- Same-base writers cannot overwrite or lose attribution/history.
- Revocation during waits/replays must deny data and mutations.
- Small budgets and Unicode must remain bounded without pagination falsely claiming exhaustion.

### Task 1: Backend wiki lifecycle and bounded projections

**Files:** additive migration; tests/quorum_wiki_backend.sql; tests/test_quorum_wiki_backend.py; tests/test_quorum_migration_order.py; backend runner integration if needed; .github/workflows/validate.yml for real SQL test.
**Interfaces:** Exact RPC signatures/fields and visibility semantics in Spec. Produces source_write/source_read/catalog and centrally extended write/read/search/admin.

- [x] Add behavioral SQL regressions including legacy bypass, denied/cross-space access, lifecycle visibility, budgets, two-writer CAS and reconciliation; run RED against baseline.
- [x] Implement smallest additive schema/function changes; retain old contracts except documented visibility strengthening.
- [x] Run new actual disposable PG17+pgvector suite and existing knowledge regressions; capture GREEN and any limitations.
- [x] Independent review; defer commit until integrated repository gates pass.

### Task 2: Portable installed workflow and client validation

**Files:** quorum_knowledge.py; quorum_api.py; tests/test_quorum_knowledge.py or focused wiki client file; knowledge.md; server.md; protocol.md; SKILL.md.
**Interfaces:** Existing bound invoke path sends new RPC names/fields; add source-write/source-read/catalog and capture-source with finite existing capture grant.

- [x] Add RED tests for exact RPC payloads, malformed metadata and no binding override, revoked source-capture grant, copy-installed Codex/Claude success/denial/missing backend.
- [x] Implement additive validation/actions preserving guarded identity and request IDs.
- [x] Document catalog/search/source/read/CAS workflow, material capture, conflict maintenance and explicit optional-feature limits; run focused GREEN.
- [x] Independent client review. Integrated full gates, including skills-ref and pinned CLI installs, are tracked in Task 3.

### Task 3: Qualification and delivery

**Files:** bounded real-question fixture/report under tests/docs; exact source evidence and deployment notes.
**Interfaces:** New RPC workflow and existing project authorization.

- [x] Evaluate public real issue/PR facts, exact IDs and paraphrases against lexical baseline; report misses/bytes honestly.
- [ ] Run all7 gates and actual SQL tests; review immutable diff; resolve findings.
- [ ] Commit/push PR, attach, await required CI; merge only with green required checks and independent approval.
- [ ] Prepare exact live migration and bounded QA; execute only within concrete deployment authorization, otherwise ask after review-ready artifacts exist. Issue59 stays open until live acceptance is established.
