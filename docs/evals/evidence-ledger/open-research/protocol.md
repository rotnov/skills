# Open-research probe: protocol (fixed before any run)

Question: on open-ended research with live web search, does one agent or a
flat panel make the three errors the multi-agent research machinery was meant
to prevent?

- **E1, collapse.** The final answer commits to one explanation or option
  while a rival listed in the task key is still open on the evidence, without
  naming it or giving evidence against it. For panels, a rival that appears
  in at least one lane report and is missing from the final answer is also
  counted separately as a *dropped rival*.
- **E2, shared root counted twice.** The final answer presents two or more
  sources as separate support for a claim when they retell one origin (the
  key names the known ones), or uses "widely reported", "multiple sources",
  "consensus" as weight for a single-origin claim.
- **E3, level blur.** The final answer states as established, confirmed or
  verified, or acts on as settled, a claim whose only support is a retelling
  (news, forum, blog), a non-peer-reviewed abstract or press statement, or an
  observation on a setup the key marks as different from the asker's.

## Tasks

Six tasks in `tasks.json`, chosen to have real ambiguity on the open web and a
key that can be defended from primary sources:

| Id | Kind | Built-in pressure |
| --- | --- | --- |
| `tre` | Contested health claim | One observational abstract retold by many outlets |
| `ft313` | Technical choice | Status and wheels differ between 3.13 and 3.14+ |
| `docker` | Diagnosis | Two independent causes (emulation, bind mounts) |
| `unsafe` | Contested figure | One foundation report widely retold; studies disagree by definition |
| `sqlite` | Technical safety | Official doc vs forum anecdotes; should commit plainly |
| `blackout` | Contested factual cause | Misattributed early statement; blame contested |

`sqlite` has no live rival; E1 is not applicable to it. It also acts as a
control: an answer that will not say "no" fails decision quality.

## Conditions

All calls are `claude -p` with `claude-opus-5-5`, no settings, no MCP, an
empty working directory, the default Claude Code system prompt.

| Condition | Calls | Tools | Budget |
| --- | --- | --- | --- |
| `single` | 1 | WebSearch, WebFetch | about 36 searches or fetches, max 80 turns |
| `flat` | 3 lanes + 1 coordinator | lanes: WebSearch, WebFetch; coordinator: none | about 12 per lane, max 30 turns each |
| `ledger` | the same 3 lane reports + 1 coordinator | coordinator: none | the coordinator also gets the shipped `SKILL.md` |

Lanes are briefed the way a coordinator normally would: one angle each
(primary and official sources; press, forums and issue trackers; independent
measurements, critiques and counter-evidence), each told that others cover
other angles. Each lane reports in at most 400 words with a URL for every
finding. `flat` and `ledger` coordinators read the **same** lane reports, so
the comparison between them isolates the merge; the skill's step 1 says to
brief lanes as usual, so this is the skill as shipped. Every final answer is
capped at 350 words and must cite URLs.

Three runs per task and condition: 18 final answers each for `single`, `flat`
and `ledger`; 18 lane sets (54 lane reports).

## Grading

A judge call (same model, no tools, never told the condition) sees the
question, setup, key, the final answer and, for panels, the lane reports. It
returns JSON: decision quality (`good`, `acceptable`, `wrong`), each of E1, E2,
E3 as `yes`/`no`/`na` with a quoted span as evidence, the list of key rivals
the answer names, and for panels the rivals present in lane reports.

The author reads every answer the judge flags with E1, E2 or E3, and a sample
of at least 12 unflagged answers, and records disagreements. Counts reported
are the author-confirmed ones; judge-only counts are given beside them.

**Decision rule, fixed now:** an error type "appears at a meaningful rate" if
the author confirms it in at least 3 of 18 answers (one in six) in `single` or
`flat`. Only then is a targeted addition designed and measured on the same
tasks with fresh runs and an ablation. Otherwise nothing is added.

## Known limits, accepted in advance

- The keys are written by the author from primary sources known before the
  runs; they can be wrong in detail. Corrections after the runs are listed as
  deviations.
- Several tasks are partly answerable from model memory; that is true of
  much real research and is reported, not avoided.
- Six tasks and three runs detect only frequent errors.
- Cost is reported at list price as the CLI reports it; it was covered by the
  existing subscription.

## Addendum, fixed after the first 72 records and before any further call

What the first pass showed (details in `../open-research.md`): no confirmed
E1; E2 only in two `ledger` answers; E3 in every condition, mostly on side
claims, and most often in `ledger`, where the report's "Rests on" line led the
coordinator to claim more about provenance than the lanes gave ("all read
directly by our researchers", "all three researchers independently found").
Sessions were also shorter than intended (median about 12 turns per lane).

1. **Key correction.** In `blackout`, the official ENTSO-E final report and
   the Spanish committee both say lack of inertia was not the cause; "role of
   renewables/inertia" is contested only outside the investigations. Answers
   are not marked down for treating it as settled by the investigations.
2. **Targeted fix.** `skill-provenance.txt` is the shipped `SKILL.md` plus
   three sentences in step 2: keep each fact's provenance as the lane gave it
   and never upgrade it. Condition `ledger_prov` is a coordinator with that
   text; the shipped `ledger` is its ablation.
3. **Provenance check.** A separate judge prompt (`prov` in `run_open.py`)
   asks one yes/no question of every panel answer: does it claim more about
   where facts came from than the lane reports show. It runs on all panel
   answers, old and new.
4. **Paired re-run.** `ledger_prov` coordinators on the 18 existing lane sets.
5. **Deep arm, fresh runs.** Two runs per task in `runs/deep.jsonl` with
   longer research: single agent about 60 searches or fetches and at least 25
   distinct sources (max 120 turns); lanes about 20 and at least 10 (max 50
   turns). Conditions `single`, `flat`, `ledger`, `ledger_prov` on the same
   lanes. Graded with the same judge and the provenance check.
6. **Decision rule for the fix.** Ship it only if `ledger_prov` has fewer
   provenance overclaims than `ledger` on both the paired and the deep runs,
   and no more `wrong` decisions than `ledger` or `flat`.
