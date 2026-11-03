# Bounded Quorum wiki retrieval evaluation

## Scope and method

On 2026-11-03, retained the public title and description of PRs [#71](https://github.com/rotnov/skills/pull/71), [#76](https://github.com/rotnov/skills/pull/76), and [#77](https://github.com/rotnov/skills/pull/77), with author, update timestamp, URL and an explicit excerpt boundary excluding diffs and discussions. The fixture stores those exact snapshots and three proposed summaries. The snapshots describe evidence at their own dates; later live acceptance is not inferred from them.

Two isolated spaces use the same existing lexical RPC and queries: raw PR text as legacy entries, and curated wiki pages linked to retained sources. Both use limit 5 and a 3,000-character search-envelope budget. Each hit then gets one bounded page read; wiki hits also get the linked retained source. No embeddings, provider, live service or extra model was used. All entries remain proposed.

The six answerable questions and one unanswerable control were fixed before the first query run. No query or page was rewritten after observing misses. The Docker harness uses the pinned PostgreSQL 17/pgvector image, no network or published ports, and removes its disposable container.

## Observations

| Query | Expected PR | Raw hits | Wiki hits | Raw search chars | Wiki search chars |
| --- | ---: | --- | --- | ---: | ---: |
| content_sha256 | #77 | #77 | #77 | 1617 | 1920 |
| Why did knowledge index fail before HTTP? | #77 | none | none | 155 | 155 |
| Why does quiet setup refuse parent reception? | #71 | none | none | 155 | 155 |
| How can a later quorum read project history? | #76 | none | none | 155 | 155 |
| Does joining a quorum grant project knowledge? | #76 | none | #76 | 155 | 1953 |
| Do embeddings invoke a paid model automatically? | #76 | none | none | 155 | 155 |
| How many watts does the device consume? | none | none | none | 155 | 155 |

Raw text retrieved the expected evidence for **1/6** answerable questions; wiki text for **2/6**. Both returned no hit for the unanswerable control. No unexpected PR was returned. Four answerable questions missed in both variants; the raw variant also missed the project-grant question. This small corpus shows that a concise page can improve one wording, while whole-question lexical retrieval remains unreliable. Empty search is not proof that the knowledge is absent: use a bounded catalog/read or a short explicit keyword query. This run does not evaluate those fallback choices.

For `content_sha256`, search + page read used **3,583 characters** for raw text. Wiki search + page + source used **5,494 characters**, with two follow-up reads versus one. The additional wiki hit for the project-grant question used **7,354 characters** in total. These figures include serialized metadata and repeated excerpts; the 3,000-character budget applies to each search response, not the sum of subsequent reads. All measured responses in this run were ASCII, so UTF-8 byte counts equal character counts. See the JSON for exact counts and fetch counts. These results do **not** establish token savings; source provenance costs context.

## Reproduction and evidence

```bash
PATH=/tmp/uv-0.11.7:$PATH uv run --frozen python tests/quorum_wiki_evaluation.py --run-docker
```

The command requires the already available pinned Docker image; it does not pull an image. The path prefix is the local pinned-uv installation used for this run; another installation of uv 0.11.7 is equivalent. Fixture: `tests/fixtures/quorum-wiki-evaluation.json`. Full measured output: `docs/evidence/2026-11-03-quorum-wiki-retrieval.json`.

Corpus SHA-256: `1b32aa6c646a404853e35bde346e6199b61e3ba3e93ad75428d6410cd9a1d151`.

This is a reproducible three-source smoke evaluation, not broad retrieval-quality qualification, a live later-quorum answer, cross-human isolation acceptance, or evidence of semantic-search quality. No automatic source verification or owner decision was performed.
