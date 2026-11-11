# Finding template and output block

Read this when rating review findings in step 2, writing findings in step 3,
or writing the report in step 6.

## Rating a review finding

- **valid**: a real defect worth the round.
- **valid-minor**: correct, but an edge case or nitpick whose fix cost more
  than it saved, for example a late round of low-priority edge cases.
- **invalid**: wrong, already covered, or out of scope.

Only valid findings are author errors. If successive review rounds yield only
valid-minor findings, write one review-process finding for the whole loop,
with its cost (rounds, minutes) and its earliest preventable point, for
example no stop rule for bot review rounds, or minor findings that should have
been batched into a follow-up.

## One finding

```markdown
### F<k>: <one abstract sentence: what went wrong>

- Tag: self-corrected | pointed-out (<maintainer, operator, reviewer, review bot or CI>) | found-by-retro
- Severity: Critical | P1 | Important | Minor | none (as the source rated it)
- Rating: valid | valid-minor | invalid | n/a (not a review finding)
- Evidence: <pull request, review or comment URL>; <CI run id>; <session id> #<line index>
- Cost: <extra review rounds>; <minutes from the error to its fix>; <CI reruns>
- Recurring and generalisable: yes | no, because <one line>
- Earliest preventable point: <rule, skill step or brief>, confidence <low | medium | high>
- Pattern key: <key> (new | existing, now <n> pull requests)
```

Never paste transcript text, comment bodies, environment values, host names,
or local paths into a finding; describe and cite.

## Output block

```markdown
## PR retro: <short description of the pull request> (<o>/<r>#<n>)

Sources read: brief <n>; author session 1 (+<n> subagents, max depth <d>);
child sessions <n>; other sessions <n>; reviews <n> (human <n>, bot <n>);
inline comments <n>; CI runs <n> (failures <n>, reruns <n>); human steers <n>.
Not located: <source, or "none">.

Timeline: brief <hh:mm> -> opened <...> -> first review <...> -> fix <...> -> merged <...>
(open to merge <duration>, <n> rounds).

Findings:
- F1 ... (tag, cost, pattern key)

Review loop: <n> rounds, <n> valid / <n> valid-minor / <n> invalid findings;
<converged | minor-only tail of <n> rounds, recorded as F<k>>.

Guard: <pattern key>: <n> pull requests, serious <yes | no>, so <qualifies | does not qualify>.

Verdict: no findings | pattern recorded (<n> occurrences) | change proposed (<owner>, <proposal or PR>).
```
