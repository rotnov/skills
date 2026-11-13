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
  no new spend). 815 calls of 10 messages; reported list-price equivalent
  about 21 USD.
- Scope: the newest 4,000 messages per harness that follow an agent reply.
  That is all of Claude Code (2026-07 to 2026-11), the newest 4,000 of 13,939
  Codex messages (2026-10 and 2026-11), and all of Grok and Cursor. 7,818
  messages got a label; 9,941 older Codex messages did not.
- After removing copies from resumed and forked sessions, 6,393 labeled
  messages remain.
- Taxonomy: version 2. Version 1 was the draft; a 312-message pilot put 15 of
  61 mistakes into "Other", which became three new types (see
  `skills/session-mistakes/scripts/taxonomy.json`).

## Labeling quality

A blind hand check: 80 labeled messages (40 flagged, 40 not flagged) were
shuffled and judged without the model's labels by a second agent, not by the
user.

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
2.6 times larger (796 mistakes) and less precise; its order of types differs
(see below).

## Top types

At confidence 0.7 (305 mistakes in 6,393 labeled messages):

| Rank by cost | Type | Cost score | Mistakes | Sessions | Claude | Codex |
| --- | --- | --- | --- | --- | --- | --- |
| 1 | Ignored instruction | 226 | 83 | 45 | 33 | 50 |
| 2 | Wrong tool or approach | 92 | 32 | 20 | 9 | 23 |
| 3 | Unnecessary work | 75 | 35 | 22 | 4 | 31 |
| 4 | False or overstated claim of result | 67 | 25 | 19 | 9 | 16 |
| 5 | Wrong fact or unchecked assumption | 61 | 29 | 22 | 15 | 14 |
| 6 | Missed the question | 57 | 25 | 20 | 13 | 12 |
| 7 | Unclear or overlong report | 52 | 32 | 22 | 20 | 12 |
| 8 | Asked too much | 41 | 23 | 18 | 16 | 7 |

The cost score adds 1, 3 or 8 per mistake for severity 1, 2 or 3 as judged
by the labeling model. Grok and Cursor have no rows at this threshold.

At confidence 0.6 (796 mistakes) the order is: ignored instruction 183,
missed the question 124, wrong approach 84, unclear report 83, false claim
71, asked too much 65, wrong fact 64, unnecessary work 63.

Rates are close across the two large harnesses: 5.1 flagged corrections per
100 labeled messages for Claude Code and 4.6 for Codex. Grok (18 labeled
messages) and Cursor (3) are too small to say anything.

## Not verified

- The user has not reviewed any label; both the labels and the hand check
  come from models.
- Recall is unknown. A mistake the user fixed silently leaves no message.
- Codex before 2026-10 is collected but not labeled.
- Whether a message came from the person or from an orchestrating agent is
  decided by harness markers and text rules; the labeling model marked a
  further 1.5 percent of messages as not human.
