# Finding template and output block

Read this when writing findings in step 3 or the report in step 6.

## One finding

```markdown
### F<k>: <one abstract sentence: what went wrong>

- Tag: self-corrected | pointed-out (<maintainer, operator, reviewer, review bot or CI>) | found-by-retro
- Severity: Critical | P1 | Important | Minor | none (as the source rated it)
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

Guard: <pattern key>: <n> pull requests, serious <yes | no>, so <qualifies | does not qualify>.

Verdict: no findings | pattern recorded (<n> occurrences) | change proposed (<owner>, <proposal or PR>).
```
