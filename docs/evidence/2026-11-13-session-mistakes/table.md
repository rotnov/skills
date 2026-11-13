# Agent mistakes by type and harness (taxonomy v2)

Each cell is `mistakes / distinct sessions`. A mistake is one human message
labeled as a correction with confidence at or above 0.7.

| Mistake type | Claude | Codex | Grok | Other | Total | Examples |
| --- | --- | --- | --- | --- | --- | --- |
| Ignored instruction | 33 / 19 | 50 / 26 | 0 / 0 | 0 / 0 | 83 / 45 | [ex-f755e247](examples.md#ex-f755e247), [ex-a7b3fd5e](examples.md#ex-a7b3fd5e), [ex-b5abf60b](examples.md#ex-b5abf60b) |
| Unnecessary work | 4 / 2 | 31 / 20 | 0 / 0 | 0 / 0 | 35 / 22 | [ex-7d191368](examples.md#ex-7d191368), [ex-3e03a94a](examples.md#ex-3e03a94a), [ex-ace6d039](examples.md#ex-ace6d039) |
| Wrong tool or approach | 9 / 5 | 23 / 15 | 0 / 0 | 0 / 0 | 32 / 20 | [ex-27dd7757](examples.md#ex-27dd7757), [ex-876fbe90](examples.md#ex-876fbe90), [ex-ebde67ca](examples.md#ex-ebde67ca) |
| Unclear or overlong report | 20 / 12 | 12 / 10 | 0 / 0 | 0 / 0 | 32 / 22 | [ex-8b098981](examples.md#ex-8b098981), [ex-a951d599](examples.md#ex-a951d599), [ex-88146674](examples.md#ex-88146674) |
| Wrong fact or unchecked assumption | 15 / 11 | 14 / 11 | 0 / 0 | 0 / 0 | 29 / 22 | [ex-04be4fc9](examples.md#ex-04be4fc9), [ex-300875ad](examples.md#ex-300875ad), [ex-51025914](examples.md#ex-51025914) |
| False or overstated claim of result | 9 / 7 | 16 / 12 | 0 / 0 | 0 / 0 | 25 / 19 | [ex-8b82bfe8](examples.md#ex-8b82bfe8), [ex-d21c7793](examples.md#ex-d21c7793), [ex-60b625db](examples.md#ex-60b625db) |
| Missed the question | 13 / 10 | 12 / 10 | 0 / 0 | 0 / 0 | 25 / 20 | [ex-c318705e](examples.md#ex-c318705e), [ex-aa991b6a](examples.md#ex-aa991b6a), [ex-44b1778b](examples.md#ex-44b1778b) |
| Asked too much | 16 / 12 | 7 / 6 | 0 / 0 | 0 / 0 | 23 / 18 | [ex-d840bfaa](examples.md#ex-d840bfaa), [ex-38f4ab5b](examples.md#ex-38f4ab5b), [ex-b855f407](examples.md#ex-b855f407) |
| Ignored repository or standing rules | 5 / 4 | 4 / 4 | 0 / 0 | 0 / 0 | 9 / 8 | [ex-9783a581](examples.md#ex-9783a581), [ex-7a7a2dce](examples.md#ex-7a7a2dce), [ex-1ae0dfac](examples.md#ex-1ae0dfac) |
| Acted without asking | 1 / 1 | 5 / 3 | 0 / 0 | 0 / 0 | 6 / 4 | [ex-cb20a8b3](examples.md#ex-cb20a8b3), [ex-12e4696c](examples.md#ex-12e4696c), [ex-dc89ee49](examples.md#ex-dc89ee49) |
| Other | 0 / 0 | 6 / 4 | 0 / 0 | 0 / 0 | 6 / 4 | [ex-6fd3cdc2](examples.md#ex-6fd3cdc2), [ex-e0d749d8](examples.md#ex-e0d749d8), [ex-24b0cdca](examples.md#ex-24b0cdca) |
| Broke something that worked | 0 / 0 | 0 / 0 | 0 / 0 | 0 / 0 | 0 / 0 | - |

## Mistakes per 100 labeled human messages

| Harness | Labeled messages | Sessions | Mistakes | Per 100 |
| --- | --- | --- | --- | --- |
| Claude | 2436 | 84 | 125 | 5.1 |
| Codex | 3936 | 96 | 180 | 4.6 |
| Grok | 18 | 4 | 0 | 0.0 |
| Other | 3 | 1 | 0 | 0.0 |

## By model

| Mistake type | Harness | Model | Count |
| --- | --- | --- | --- |
| Ignored instruction | Claude | claude-opus-5-5 | 20 |
| Ignored instruction | Claude | claude-fable-5 | 5 |
| Ignored instruction | Claude | claude-sonnet-5 | 4 |
| Ignored instruction | Claude | claude-opus-5 | 2 |
| Ignored instruction | Claude | claude-opus-4-8 | 1 |
| Ignored instruction | Claude | claude-fable-5-2 | 1 |
| Ignored instruction | Codex | gpt-6.1-sol | 30 |
| Ignored instruction | Codex | gpt-6-astra | 20 |
| Unnecessary work | Claude | claude-fable-5 | 3 |
| Unnecessary work | Claude | claude-sonnet-5 | 1 |
| Unnecessary work | Codex | gpt-6.1-sol | 20 |
| Unnecessary work | Codex | gpt-6-astra | 10 |
| Unnecessary work | Codex | gpt-6-sol | 1 |
| Wrong tool or approach | Claude | claude-opus-5-5 | 4 |
| Wrong tool or approach | Claude | claude-fable-5 | 2 |
| Wrong tool or approach | Claude | claude-fable-5-2 | 1 |
| Wrong tool or approach | Claude | claude-opus-5 | 1 |
| Wrong tool or approach | Claude | claude-sonnet-5 | 1 |
| Wrong tool or approach | Codex | gpt-6.1-sol | 18 |
| Wrong tool or approach | Codex | gpt-6-astra | 5 |
| Unclear or overlong report | Claude | claude-opus-5-5 | 8 |
| Unclear or overlong report | Claude | claude-sonnet-5 | 5 |
| Unclear or overlong report | Claude | claude-fable-5 | 4 |
| Unclear or overlong report | Claude | claude-opus-5 | 3 |
| Unclear or overlong report | Codex | gpt-6.1-sol | 8 |
| Unclear or overlong report | Codex | gpt-6-astra | 4 |
| Wrong fact or unchecked assumption | Claude | claude-opus-5-5 | 5 |
| Wrong fact or unchecked assumption | Claude | claude-fable-5 | 3 |
| Wrong fact or unchecked assumption | Claude | claude-opus-5 | 2 |
| Wrong fact or unchecked assumption | Claude | claude-fable-5-2 | 2 |
| Wrong fact or unchecked assumption | Claude | claude-sonnet-5 | 2 |
| Wrong fact or unchecked assumption | Claude | claude-opus-4-8 | 1 |
| Wrong fact or unchecked assumption | Codex | gpt-6.1-sol | 11 |
| Wrong fact or unchecked assumption | Codex | gpt-6-astra | 3 |
| False or overstated claim of result | Claude | claude-sonnet-5 | 5 |
| False or overstated claim of result | Claude | claude-opus-4-8 | 2 |
| False or overstated claim of result | Claude | claude-opus-5-5 | 2 |
| False or overstated claim of result | Codex | gpt-6.1-sol | 11 |
| False or overstated claim of result | Codex | gpt-6-astra | 5 |
| Missed the question | Claude | claude-opus-5-5 | 7 |
| Missed the question | Claude | claude-sonnet-5 | 4 |
| Missed the question | Claude | claude-opus-5 | 1 |
| Missed the question | Claude | claude-fable-5 | 1 |
| Missed the question | Codex | gpt-6.1-sol | 6 |
| Missed the question | Codex | gpt-6-astra | 6 |
| Asked too much | Claude | claude-opus-5-5 | 11 |
| Asked too much | Claude | claude-sonnet-5 | 4 |
| Asked too much | Claude | claude-opus-5 | 1 |
| Asked too much | Codex | gpt-6-astra | 4 |
| Asked too much | Codex | gpt-6.1-sol | 3 |
| Ignored repository or standing rules | Claude | claude-sonnet-5 | 2 |
| Ignored repository or standing rules | Claude | claude-fable-5 | 2 |
| Ignored repository or standing rules | Claude | claude-opus-5-5 | 1 |
| Ignored repository or standing rules | Codex | gpt-6.1-sol | 3 |
| Ignored repository or standing rules | Codex | gpt-6-astra | 1 |
| Acted without asking | Claude | claude-opus-5-5 | 1 |
| Acted without asking | Codex | gpt-6.1-sol | 5 |
| Other | Codex | gpt-6.1-sol | 5 |
| Other | Codex | gpt-6-astra | 1 |
