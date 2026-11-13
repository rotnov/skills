# Evidence and Migration

## Canonical finding

| Fields | Meaning |
| --- | --- |
| `finding_id`, `revision` | Stable identity and specific content revision. |
| `origin_island`, `origin_task`, `problem_id` | Producing program/task and addressed formulation. |
| `execution_provenance` | Producing model/host/tool configuration epoch. |
| `claim`, `scope`, `assumptions` | Exact assertion and applicability. |
| `evidence_refs`, `artifact_refs` | Sources, observations, commands and authorized locations of full supporting material. |
| `evidence_basis` | Inspected documentation, attributed implementation report, original measurement/proof, or our reproduction; identify who established what. |
| `counterevidence`, `limitations`, `open_obligations` | Failure conditions, missing checks and unresolved transfer/acceptance requirements. |
| `evidence_status` | Hypothesis, supported within scope, reproduced within scope, contradicted within scope, or unresolved. |
| `derived_from`, `shared_dependencies` | Earlier findings and common evidence or configuration roots. |
| `next_test` | Smallest useful discriminating test. |

Keep one canonical record and version corrections. Sharing or summarizing a
hypothesis cannot upgrade its evidence status. Preserve qualifications when
compressing findings; restore a narrowed claim if a summary dropped assumptions.

For documentary findings, inspect the decisive article, paper, reference, or
provided source before accepting an excerpt or island summary. Record what was
actually read and the procedure, result, version, and conditions that support the
claim. Follow decision-changing references within the allowance; identify any
unread artifact precisely. A search snippet or link alone is a lead.

An implementation author's concrete report is positive evidence at its stated
scope, even without our own replication. Attribute it and assess its methods and
limitations; neither promote it to our verified result nor erase it as "no
evidence." Missing documentation does not establish impossibility. An explicit
limitation or counterexample can contradict a scoped claim. Reconcile differing
sources by their statements and conditions; recency alone proves no supersession.

## Transfer and artifact access

A migration names the finding revision, `recipient_island`, `transfer_reason`,
`expected_use`, and recipient `disposition`: test, adapt, decline, or defer.
Record what changed locally and any descendant finding. Transfer useful
mechanisms, negative results, and counterexamples as well as leading candidates.
An unverified idea can travel as a hypothesis with a proposed test.

Prefer one or two relevant packets per recipient at a checkpoint; include more
when needed to preserve decisive assumptions or counterevidence. A digest or
unread citation is an index, not proof. Retrieve the full authorized artifact
needed to inspect a decisive step, or retain the verification gap. A packet
does not grant access to private or out-of-scope material.

## Revised assignment

When an adopted finding changes work, issue a `follow_up_brief`:

| Fields | Meaning |
| --- | --- |
| `brief_revision`, `previous_brief` | Changed task/charter and the revision it supersedes. |
| `target_island`, `problem_id` | Recipient and exact formulation. |
| `seed_findings` | Finding IDs/revisions actually adopted. |
| `changed_assumptions` | Preserved, revised, rejected and unresolved assumptions. |
| `next_probe`, `acceptance_test` | Changed investigation and observable progress criterion. |
| `artifact_access` | Authorized complete artifacts and access limits. |
| `allocation_decision` | Actor, rationale, inherited authority, changed allocation and remaining allowance. |

A synthesizer proposes; the responsible coordinator issues within existing
authority. Ordinary authorized tasks need no repeated approval. Preserve the
sequence: finding → revised brief → issued task → observed execution/result.
Summary delivery alone is not completed reseeding. Declining or deferring an
import is valid when accompanied by a reason.

## Lineage, contradictions and novelty

Separate context isolation, methodological diversity, and independent evidence.
Copies of experiment X through several islands still have one experimental root.
A separately executed replication adds evidence only for what was actually
repeated; shared inputs, code, weights, and imported findings remain visible.

When a premise is contradicted, review dependent conclusions and notify affected
recipients through authorized channels. Retain unaffected evidence. Hybrid work
records both parent lineages. Reconcile a reproduced minority counterexample by
scope and evidence, not by counting agreeing agents. Do not combine incompatible
assumptions into an apparently universal conclusion.

Before claiming novelty, priority, independent discovery, or superiority, inspect
permitted prior/concurrent work and compare exact statements, assumptions,
methods, versions, and evaluation conditions. Record source dates and known
exposure. Distinguish reuse, changed formulation, extension, reproduction, and
unresolved priority. No recorded access does not prove absence from training.
Narrow or omit unsupported claims. Skip this review when no such claim is made;
it grants neither extra access nor publication authority.
