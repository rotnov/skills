# Multi-Agent Research Candidate

Date: 2026-09-16. Status: local candidate, not a validated release.

Latest revision: the [documentary-evidence correction](documentary-regression.md)
supersedes the package digest below. That record covers the user-observed T01
synthesis issue and its three scoped behavioral regressions. Earlier validation
and usage records below describe the preceding candidate and remain historical.

## Scope and evidence

The user requested creation of the first editable package after reviewing the
baseline checkpoint. This advances authoring despite the original plan's
incomplete baseline gate; it does not mark the missing experiments complete.
The approved design remains unchanged. No additional service, API spending,
global installation, push, PR, or merge is included.

The [baseline](results.md) contains 35 passing decision-only samples across seven
cases, one blocked E01 attempt, and four unlaunched E01 repetitions. Those passing
controls are preserved as regressions, not claimed improvements. Guidance for
the remaining behaviors follows the approved design; its effectiveness remains
unestablished until controlled tests run. The sealed variations remain unexposed
to the author and are not used for this candidate's development.

## Editable package

| File under `skills/multi-agent-research/` | Owns |
| --- | --- |
| `SKILL.md` | Routing, mission workflow, reference loading and output contract. |
| `references/island-protocol.md` | Problem coverage, charters, enabling problems and checkpoints. |
| `references/evidence-and-migration.md` | Findings, lineage, artifact access, revised assignments and novelty. |
| `references/allocation-and-verification.md` | Resource allocation, acceptance gates and verification package. |
| `references/execution-modes.md` | Actual host capabilities, fallback, configuration epochs and authority. |
| `agents/openai.yaml` | Codex presentation metadata only. |
| `evals/evals.json` | All 30 public scenarios copied from the approved plan. |

The package has no executable runtime, sibling dependency, authoring overlay,
private paths, or required external connector. Its behavior contract is shared
by Codex and Claude Code.

## Validation record

Four package-contract tests from the implementation plan were added first and
failed on the absent package. After authoring, they pass. These tests establish
inventory, evaluation schema, resource resolution in a clean copy, and absence
of runtime executables/symlinks; they do not establish research quality.

All repository gates passed: locked dependencies, repository contracts, 49 unit
tests, the pinned official Agent Skills validator, individual cross-client
copy-installs, full pre-commit, and whitespace checks. The reviewed seven-file
candidate was separately copy-installed with `skills@1.5.20` for Codex and Claude
Code; both copies match every source file byte-for-byte and contain no sibling
skill. The source directory is not a runtime resource.

Separate review identified late loading of allocation constraints. The core now
establishes reserves and reads the allocation contract before first dispatch;
scoped re-review confirmed the correction.

Reviewed package digest (SHA-256 of sorted JSON mapping relative paths to file
SHA-256 values):
`5d8a8795614ffc0ba1b1df6c479278d2312be8e86b645bd2712f93b574c59c43`.

An initial four-case Codex smoke attempt used conflicting enable/disable flags
for file-reading tools. It exposed the core through native skill invocation but
could not read references; it is retained as a test-configuration limitation,
not accepted as complete package behavior evidence. Its initial package digest
was `e3842186a5c5e82688408c8cdc78b84b269ae4741e619761849929246ffed130`.
The reviewed candidate is tested again with nonconflicting read-only tool access.
This is a separate smoke stratum, not matched B1: explicit invocation and available
tools differ from the baseline, so no causal improvement claim is supported.

Native Claude evaluation remains blocked by absent current authentication.
Installation alone is not native execution. Full B1 repetitions, native parallel
islands, held-out trials, and usefulness comparisons remain unrun.

## Native Codex smoke observations

Four fresh serial Codex CLI 0.154.0 processes used the independently installed
reviewed package, requested `gpt-6-astra` with high effort, and the existing
subscription. Each had a 120-second ceiling, read-only tools, no external
integrations, and no inherited author conversation. The live input diagnostic
showed the candidate as the available skill; other discovered skills were disabled.
The producing model/version was not attested by the event stream and remains
unknown. No credential, account, or global configuration change was made.

| Path | Observed result | Limit |
| --- | --- | --- |
| Positive planning | Read the core and all four relevant references from the installed copy; proposed distinct programs, problem coverage, two rounds, protected alternative/checking/reporting reserves, and explicit verification gaps. | A proposed plan in a single context; no experiments or worker dispatch occurred. |
| Goal mismatch | Read the core and allocation/verification reference; preserved checker acceptance for sorted input while rejecting arbitrary-input mission acceptance. | Supplied synthetic checker outcome, not a real verifier run. |
| Missing delegation | Read the installed instructions; distinguished source review/self-review from independent checking and disclosed missing target artifacts. | No independent-execution capability was exercised. |
| Non-trigger | Returned only the Russian translation; no skill/reference read or orchestration tool event. | One simple translation example. |

All four completed without timeout. Visible tool actions were only reads inside
the installed package; no scenario answer keys, authoring checkout, or private
evaluator artifacts were read. Filesystem access was procedurally scoped rather
than a security isolation proof. Explicit skill invocation was used for the first
three paths; only the non-trigger tests automatic routing restraint.

Known reviewed-smoke usage: 114,580 input tokens and
3,887 output tokens. The earlier limited attempt additionally
reported 63,237 input and 4,139 output
tokens. These are separate samples; no control comparison or cost-efficiency
claim is made. Monetary cost and total author/reviewer usage are unknown.
Raw visible outputs, event logs, input/configuration records, and digests remain
outside the repository; private evidence IDs are `MAR-CANDIDATE-20260916-02`
followed by the path name in the table. No hidden reasoning text is retained.

These observations were inspected by the author and sent for separate LLM review.
They cover success, failure, fallback, and non-trigger paths at one sample each;
full repeated conformance, independent human validation, and comparative research
quality remain open.

## Iteration

Keep one home for each change using the table above. A behavioral correction
needs an observed example, the smallest relevant instruction change, and a rerun
of that case plus affected boundaries. Preserve successful baseline controls.
Changes to the model, source access, or tools need a new comparison stratum.

The [first applied task](task-queue.md) remains Sony Spatial Reality Display
camera access for gesture capture. Freeze its concrete hardware/SDK/source and
acceptance constraints before comparing a single agent, flat panel, and islands.
No Sony investigation has been performed by creating this package.
