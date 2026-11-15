# Session mistakes: first run (2026-11-13)

Output of the `session-mistakes` skill on one developer's machine. Everything
here is anonymized: counts, model names, and descriptions written by the
labeling model. No transcript text and no user quotes are included. The raw
store stays on that machine in `~/.local/share/session-mistakes/`.

- [table.md](table.md): mistake type by harness, with models
- [examples.md](examples.md): three anonymized examples per type
- [table.json](table.json): the same counts, machine-readable

## Sources

| Harness | Location (under the home folder) | Files | Human messages | Sessions with one |
| --- | --- | --- | --- | --- |
| Claude Code | `.claude/projects/<encoded cwd>/<session>.jsonl` | 338 | 4,135 | 284 |
| Codex | `.codex/sessions/<Y>/<M>/<D>/rollout-*.jsonl[.zst]`, `.codex/archived_sessions/` | 5,850 | 15,912 | 518 |
| Grok | `.grok/sessions/<encoded cwd>/<session>/chat_history.jsonl` | 28 | 45 | 15 |
| Cursor | `.cursor/projects/<encoded cwd>/agent-transcripts/<id>/<id>.jsonl` | 1 | 4 | 1 |

Left out on purpose: 1,163 Claude Code subagent transcripts and all Codex
subagent threads (agent-to-agent traffic), tool results, system reminders,
hook and scheduler events, peer-agent messages, and slash commands without
prose. 5,128 of the Codex files are zstd-compressed and were read through
`zstd`.

## What was labeled

- Model: `claude-sonnet-5-5` through the signed-in `claude` CLI (subscription;
  no new spend). 1,810 calls of 10 messages in two passes; reported
  list-price equivalent about 44 USD.
- First pass: the newest 4,000 messages per harness that follow an agent
  reply (7,818 labels). Second pass, added before the pull request: the
  remaining 9,941 older Codex messages, with no failed calls.
- Scope now: every message that follows an agent reply, on all four
  harnesses. 17,759 messages have a label; 8 newer Claude Code messages
  arrived after the first pass and do not. Codex labels run from 2025-10 to
  2026-11, with 97 percent of them from 2026-07 onward.
- After removing copies from resumed and forked sessions, 16,272 labeled
  messages remain.
- Taxonomy: version 2. Version 1 was the draft; a 312-message pilot put 15 of
  61 mistakes into "Other", which became three new types (see
  `skills/session-mistakes/scripts/taxonomy.json`).

## Labeling quality

A blind check by a second agent, not by the user: 80 labeled messages (40
flagged, 40 not flagged) were shuffled and judged without the model's
labels. The sample was drawn after the first pass, so none of the 9,941
older Codex messages is in it.

| Flags counted | Extraction precision | Classification precision |
| --- | --- | --- |
| all flags | 22 / 40 = 0.55 | 10 / 22 = 0.45 |
| confidence at or above 0.6 | 18 / 25 = 0.72 | 9 / 18 = 0.50 |
| confidence at or above 0.7 | 9 / 11 = 0.82 | 6 / 9 = 0.67 |

- 2 of the 40 unflagged messages were judged to be mistakes (0.05).
- 38 of the 40 evidence quotes occur word for word in the message.
- The reviewer marked 28 of its 80 verdicts as borderline, mostly because the
  agent's reply is cut to its last part.

So the committed table counts only flags at confidence 0.7 or higher. Read it
as "about four in five rows are real corrections, and about two in three of
those carry the right type". The sample at that threshold is 11 messages:
the precision figure is a direction, not a measurement. At 0.6 the table is
2.4 times larger (2,058 mistakes) and less precise; its order of types
differs (see below).

## Top types

At confidence 0.7 (853 mistakes in 16,272 labeled messages):

| Rank by cost | Type | Cost score | Mistakes | Sessions | Claude | Codex |
| --- | --- | --- | --- | --- | --- | --- |
| 1 | Ignored instruction | 485 | 188 | 85 | 33 | 155 |
| 2 | Wrong tool or approach | 339 | 123 | 56 | 9 | 114 |
| 3 | False or overstated claim of result | 243 | 88 | 45 | 9 | 79 |
| 4 | Unnecessary work | 227 | 102 | 44 | 4 | 98 |
| 5 | Wrong fact or unchecked assumption | 216 | 96 | 59 | 15 | 81 |
| 6 | Missed the question | 173 | 81 | 53 | 13 | 68 |
| 7 | Unclear or overlong report | 103 | 67 | 42 | 20 | 47 |
| 8 | Ignored repository or standing rules | 79 | 31 | 18 | 5 | 26 |

The cost score adds 1, 3 or 8 per mistake for severity 1, 2 or 3 as judged
by the labeling model. Grok and Cursor have no rows at this threshold.

At confidence 0.6 (2,058 mistakes) the order by cost, with counts, is: ignored
instruction 495, wrong approach 260, missed the question 289, false claim
186, wrong fact 208, unnecessary work 166, unclear report 191, asked too
much 113.

## Rates by harness

Share of labeled messages that are a flagged correction of each type, at
confidence 0.7 (Claude Code: 2,436 labeled messages; Codex: 13,815). This is
the comparison to read across harnesses; the counts above are larger for
Codex only because it has 5.7 times as many labeled messages.

| Type | Claude Code | Codex | Higher on |
| --- | --- | --- | --- |
| Ignored instruction | 1.35% | 1.12% | Claude Code, 1.2 times |
| Wrong tool or approach | 0.37% | 0.83% | Codex, 2.2 times |
| Unnecessary work | 0.16% | 0.71% | too few cases to compare |
| Wrong fact or unchecked assumption | 0.62% | 0.59% | about equal |
| False or overstated claim of result | 0.37% | 0.57% | Codex, 1.5 times |
| Missed the question | 0.53% | 0.49% | about equal |
| Unclear or overlong report | 0.82% | 0.34% | Claude Code, 2.4 times |
| Asked too much | 0.66% | 0.20% | Claude Code, 3.2 times |
| Ignored repository or standing rules | 0.21% | 0.19% | about equal |
| Other | 0.00% | 0.12% | too few cases to compare |
| Acted without asking | 0.04% | 0.09% | too few cases to compare |
| Broke something that worked | 0.00% | 0.03% | too few cases to compare |
| **All types** | **5.1%** | **5.3%** | about equal |

The overall rate is the same; the mix differs. Codex is flagged more for
unnecessary work, a wrong approach and false claims; Claude Code for
unclear reports and asking too much. Rows with fewer than ten Claude Code
cases (wrong approach 9, false claim 9) give a rough ratio only, and with
fewer than five (unnecessary work: 4 against 98 on Codex) no ratio is shown. Grok (18
labeled messages) and Cursor (3) are too small to say anything.

Against the first pass (305 mistakes in 6,393 messages), the top type is
unchanged, "False or overstated claim" rose from fourth to third, and
"Unnecessary work" fell from third to fourth.

## Not verified

- The user has not reviewed any label; both the labels and the hand check
  come from models.
- Recall is unknown. A mistake the user fixed silently leaves no message.
- The hand check does not cover the second labeling pass (Codex before
  2026-10).
- Near-identical examples can appear twice (ex-8f8bf697 and ex-de04ba47
  describe one incident). Copies from forked sessions are removed; whether
  this pair is two real corrections or a copy the removal missed has not
  been checked.
- Whether a message came from the person or from an orchestrating agent is
  decided by harness markers and text rules; the labeling model marked a
  further 0.9 percent of messages as not human.
