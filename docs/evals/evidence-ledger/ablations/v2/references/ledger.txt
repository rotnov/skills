# Ledger rules

Read this when you merge lane reports and an origin or a rung is unclear, or
when lanes disagree.

## Finding the origin

Two sources share an origin when any of these holds:

- one cites, quotes or links the other, or both cite a third;
- one re-analyses, re-plots or re-runs a query on the other's data;
- one is a summary, echo or support article about the other;
- the result is reported by its own author on someone else's page.

An origin is independent only when it made its own observation: its own
measurement, its own run, its own data. If you cannot tell where a source got
its fact, record `origin: unknown` and do not count it as independent.

Independent origins can still share a blind spot. Several teams that each
measured the same kind of setup made separate observations of the same thing:
count them as independent for that setup and as silent about any other.

A source with a stake in the result (the vendor, the author, the person whose
change is under review) still counts as an origin; note the stake.

## Rating the rung

Lanes report how a fact is known and where it was observed. You assign the
rung, against the target.

| Rung | The claim line shows | Example |
| --- | --- | --- |
| `told` | Someone states it; no observation is shown | "A colleague says it works" |
| `documented` | A primary source states it as designed or released | A specification, a product manual |
| `observed-elsewhere` | Someone ran or measured it on a setup that differs from the target where the mechanism lives | A trial in another region with other roads |
| `observed-on-target` | Someone ran or measured it on the target or a representative part of it | A trial in the region the decision is about |

An observation proves only what was actually looked at: a step that finished
without an error shows that it finished, not that its result is right. Looking
at the same data twice is one observation.

## Setup differences

Compare where each claim was observed with the target. A difference counts
only when you can say how it changes the result: it touches the mechanism the
claim depends on. A difference you cannot connect to the mechanism does not
lower the rung, and you do not need to rule out differences nobody can name.

The join usually spans lanes: one lane reports where a result was observed,
another reports how the effect works, a third reports the target's setup. No
single lane can see the mismatch. Line the three up for the crux before you
decide.

## Worked example

Question: will the new courier get our parcels to the northern region by the
next day?

```text
C1 "next-day delivery nationwide" | origin: courier brochure | documented
C2 "next-day, as promised" on a review site | origin: same brochure (quotes
   it) | documented
C3 96% next-day in 2,000 parcels | origin: sister company pilot, capital
   city | observed-elsewhere: the north has no local depot
C4 22 of 40 parcels next-day | origin: our own trial, northern region |
   observed-on-target
```

C1 and C2 are one origin. C3 is a real measurement, on a setup that differs
where it matters. C4 is the only observation on the target and it contradicts
the claim: do not promise next-day there. Had C4 shown 39 of 40, the decision
would be to commit, with no further checks, whatever C1 to C3 said.
