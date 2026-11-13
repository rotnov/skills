# Ledger rules

Read this when you merge lane reports and an origin or a rung is unclear, or
when lanes disagree.

## Finding the origin

Two sources share an origin when any of these holds:

- one cites, quotes or links the other, or both cite a third;
- they give the same figure for the same setup;
- one re-analyses, re-plots or re-runs a query on the other's data;
- one is a summary, changelog echo or support article about the other;
- the result is reported by its own author on someone else's page.

An origin is independent only when it made its own observation: its own
measurement, its own run, its own data. If you cannot tell where a source got
its fact, record `origin: unknown` and do not count it as independent.

A source with a stake in the result (the vendor, the author, the person whose
change is under review) still counts as an origin, but note the stake.

## Rating the rung

| Rung | The claim line shows | Example |
| --- | --- | --- |
| `told` | Someone states it; no observation is shown | "A colleague says it works" |
| `documented` | A primary source states it as designed or released | A specification, a product manual |
| `observed-elsewhere` | Someone ran or measured it, on a setup that is not the target | A trial in another region |
| `observed-on-target` | Someone ran or measured it on the target itself | A trial in the region the decision is about |

Rate the claim, not the confidence of whoever reported it. An observation
proves only what was actually looked at: a step that finished without an error
shows that it finished, not that its result is right. Looking at the same data
twice is one observation.

## Setup differences

List each difference between where a claim was observed and the target:
version, configuration, scale, load, platform, population, period. A
difference matters when it touches the mechanism the claim depends on. When
one lane reports the mechanism and another reports the target's setup, join
them: that join is the merge's main job.

## Worked example

Question: will the new courier get our parcels to the northern region by the
next day?

```text
C1 "next-day delivery nationwide" | origin: courier brochure | documented
C2 "next-day, as promised" on a review site | origin: same brochure (quotes
   it) | documented
C3 96% next-day in 2,000 parcels | origin: sister company pilot, capital
   city | observed-elsewhere | target is rural north
C4 22 of 40 parcels next-day | origin: our own trial, northern region |
   observed-on-target
```

C1 and C2 are one origin. C3 is a real measurement on another setup. C4 is the
only observation on the target and it contradicts the claim, so the rival "not
next-day in the north" survives and decides: do not promise next-day there.
