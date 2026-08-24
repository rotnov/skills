# Blind judge result

The judge received anonymized A/B outputs and graded only explicit evidence. Candidate
A was the with-skill run; candidate B was the clean baseline.

| Case | A | B |
| --- | ---: | ---: |
| Invocation is not ownership | 4/4 | 1/4 |
| External owner with origin | 5/5 | 1/5 |
| External owner without origin | 4/4 | 4/4 |
| Cross-repository owner | 4/4 | 3/4 |
| No existing owner | 5/5 | 3/5 |
| Nothing reusable | 4/4 | 2/4 |
| **Total** | **26/26** | **14/26** |

The skill's value is concentrated in safety boundaries the baseline omitted: ambiguous
ownership, project import before adaptation, source-recording continuity across a
repository handoff, concrete new-skill gating, and claim-safe close.
