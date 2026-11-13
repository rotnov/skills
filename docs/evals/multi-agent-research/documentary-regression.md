# Documentary evidence correction

Date: 2026-09-16. Scope: the T01 user-observed synthesis failure.

## Observed failure and correction

The [Sony investigation](t01-sony-camera.md) found and read an implementation
vendor's affirmative camera-sharing procedure. In subsequent discussion, the
author framed the lack of our own hardware experiment as lack of a verified
answer, obscuring the positive documentary result. The user challenged both the
absence of a clear conclusion and the use of articles, then authorized a skill
correction. The sources had been read; the demonstrated failure was primarily
acceptance and synthesis, not failure to run a browser.

The correction aligns acceptance with the requested deliverable and records
evidence basis separately from verdict. Inspected documentation and attributed
implementation reports can answer documentary feasibility. Our reproduction and
deployment-specific verification remain separate claims. Missing checks affect
only the claims that require them. Decision reports must state what is supported,
contradicted, and unresolved; full-source inspection precedes acceptance of a
snippet or migrated summary.

Changed runtime files: `SKILL.md`, `references/evidence-and-migration.md`, and
`references/allocation-and-verification.md`. The skill description, execution
protocol, authority boundaries, and original E01–E30 fixtures are unchanged.
The authoring lesson lives separately in the skill's evolution overlay; no
overlay loader or repository dependency was added to the installable package.

## Frozen checks and observed outcomes

[D01–D03 fixtures and grading criteria](documentary-regression.json) were written
before the runtime edits. The earlier T01 conversation supplies the observed
failure; there was no controlled pre-edit run of these new synthetic cases.
They are regression smoke checks, not evidence of a causal improvement estimate.

| Case | Actual behavior | Verdict |
| --- | --- | --- |
| D01: documentary feasibility | Read both full sources, led with the vendor-documented built-in-camera route and concrete procedure, retained compatibility/API limits, accepted the documentary inquiry | Pass |
| D02: exact-configuration reproduction | Read the procedure, retained the vendor's candidate route, rejected acceptance without the explicitly required reproduction, did not claim impossibility | Pass |
| D03: misleading island summary | Read the full release note, rejected the current-version claim, preserved external-camera support and classified built-in support as planned | Pass |

Three fresh, serial Codex CLI processes used an individual pinned-CLI copy-install
of the revised skill. Each received only its user prompt and synthetic source
files, with a 120-second ceiling, read-only tools, disabled network research and
connectors, and the existing ChatGPT subscription. No API key or additional
service was enabled. Input preflight showed the candidate skill and no author
conversation or grading artifact. Source and reference reads are visible in the
event records; access separation is procedural, not a filesystem isolation claim.
Each source-review trial used single-context fallback, not research islands.

All three processes completed with exit zero and no timeout, in 39.3, 29.8, and
23.0 seconds. Recorded usage totals: 114,950 input tokens and 2,055 output tokens;
cached input tokens are included in input totals. These exclude author/reviewer
work. Requested model: `gpt-6-astra`, high effort; actual immutable model version
and monetary cost unavailable. No efficiency comparison is supported.

Private run evidence: `MAR-DOCUMENTARY-20260916-01`, held under
`/private/tmp/mar-documentary-regression/evaluator/`; includes configuration,
prompt input, visible events, outputs, manifests, and package file hashes. Hidden
reasoning is excluded. The full synthetic source texts remain in the frozen JSON
fixture, independently of the temporary run directory.

## Package checks and limits

Both independent Codex and Claude Code copy-installs match the revised package
byte for byte. The project's existing symlink resolves to that same source.
All mandatory repository gates passed: lock/sync, skill contracts, 49 unit tests,
pinned official specification validator, pinned cross-client installation smoke,
full pre-commit, and whitespace checks. Native behavior was exercised in Codex;
Claude Code installation is not a native Claude behavioral test.

Revised package digest (SHA-256 of sorted JSON mapping relative paths to file
SHA-256 values):
`a0f578a7ce26b82da52dbdba950472c8b25c79c9ff1d9bcf48dc87a05292318e`.

Scoped instruction review found no material issues. A separate reviewer of the
actual outputs and events confirmed all three passes, matched the fixture digest
to the freeze, and found only installed-skill and supplied-source reads in visible
tool calls. This reviewer knew the rubric; the producing workers did not receive
it. One sample per scenario does not establish robustness, research-quality
improvement, or completion of the full E01–E30 evaluation.
