# Labeling quality

Read this when running the hand check of the labels, or when changing the
taxonomy or the labeling prompt.

## What the model sees and returns

For each human message the model gets the user's previous message, the end of
the agent's last reply, whether the user interrupted the agent, and the
message itself, with secrets stripped. Messages that open a session have no
agent reply to judge and are skipped unless `--include-first` is given.

It returns `mistake`, `none` or `not_human`. A mistake also gets a category, a
one-sentence generic description, an exact quote from the message, a severity
(1 friction, 2 rework, 3 damage or anger) and a confidence. The tool checks
that the quote really occurs in the message (`evidence_ok`); `table` counts
only labels at or above `--min-confidence` (default 0.6) and shows only
examples with a verified quote.

## Hand check

1. `TOOL sample --mistakes 40 --negatives 40` writes a private JSONL file in
   `<data dir>/review/`. It holds raw text: never move it into a repository.
2. For each entry read `prev_user`, `agent_context` and `text`, then fill:
   - `human_verdict`: `mistake`, `none` or `not_human`, judged before looking
     at the model's verdict where practical;
   - `human_category`: for a real mistake, the taxonomy id you would pick;
     `human_alt_category`: a second id when two fit equally.
   A stronger check is blind: give a second reviewer the items without the
   model's labels, in shuffled order, and merge their verdicts into the file.
3. `TOOL score <file>` (add `--min-confidence` to score only the flags the
   table will count) prints:
   - **extraction precision**: flagged messages that are real mistakes;
   - **missed in negative sample**: unflagged messages that are mistakes (a
     floor for what recall loses, not recall itself);
   - **classification precision**: real mistakes whose category matches yours;
   - **evidence quote verified**: flagged messages whose quote is exact.
4. Report the numbers with the sample size. Below about 0.8 extraction
   precision, raise `--min-confidence` for `table` and `rank` to the lowest
   value that reaches it, or tighten the prompt and label again.

The reviewer is usually the agent itself. Say so in the report: an agent
checking a model's labels is weaker evidence than the user checking them.

## Changing the taxonomy

`scripts/taxonomy.json` holds `version`, a `history` list, and the categories
(id, title, definition, coverage keywords). `other` stays last.

Revise it when "Other" is among the largest types, when the hand check shows
two types being confused, or when a type stays empty. Then:

1. Read the `other_label` values and descriptions of the confused types.
2. Edit the categories, raise `version`, and add a `history` entry that says
   what changed and which data drove it.
3. Label again with `TOOL label --relabel` over the same selection, and note
   in the report that counts from different taxonomy versions do not compare.

Each label stores the taxonomy and prompt version that produced it. Raise
`PROMPT_VERSION` in `scripts/mistakes.py` whenever the prompt text changes.

## Known limits

- A user who silently fixes or abandons the work leaves no message, so the
  table undercounts. It measures corrections, not mistakes.
- Confidence is the model's own estimate and is not calibrated.
- Counts depend on how many messages were labeled per harness; compare
  harnesses by the "per 100 labeled messages" table, not by raw counts.
