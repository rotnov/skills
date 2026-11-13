# Agent mistakes by type and harness (taxonomy v2)

Each cell is `mistakes / distinct sessions`. A mistake is one human message
labeled as a correction with confidence at or above 0.7.

| Mistake type | Claude | Codex | Grok | Other | Total | Examples |
| --- | --- | --- | --- | --- | --- | --- |
| Ignored instruction | 33 / 19 | 155 / 66 | 0 / 0 | 0 / 0 | 188 / 85 | [ex-f755e247](examples.md#ex-f755e247), [ex-97de3b9b](examples.md#ex-97de3b9b), [ex-a7b3fd5e](examples.md#ex-a7b3fd5e) |
| Wrong tool or approach | 9 / 5 | 114 / 51 | 0 / 0 | 0 / 0 | 123 / 56 | [ex-cbace820](examples.md#ex-cbace820), [ex-27dd7757](examples.md#ex-27dd7757), [ex-94726464](examples.md#ex-94726464) |
| Unnecessary work | 4 / 2 | 98 / 42 | 0 / 0 | 0 / 0 | 102 / 44 | [ex-9ec9a4e3](examples.md#ex-9ec9a4e3), [ex-7d191368](examples.md#ex-7d191368), [ex-b076ff48](examples.md#ex-b076ff48) |
| Wrong fact or unchecked assumption | 15 / 11 | 81 / 48 | 0 / 0 | 0 / 0 | 96 / 59 | [ex-8f8bf697](examples.md#ex-8f8bf697), [ex-de04ba47](examples.md#ex-de04ba47), [ex-04be4fc9](examples.md#ex-04be4fc9) |
| False or overstated claim of result | 9 / 7 | 79 / 38 | 0 / 0 | 0 / 0 | 88 / 45 | [ex-300b9387](examples.md#ex-300b9387), [ex-ccffabd5](examples.md#ex-ccffabd5), [ex-8b82bfe8](examples.md#ex-8b82bfe8) |
| Missed the question | 13 / 10 | 68 / 43 | 0 / 0 | 0 / 0 | 81 / 53 | [ex-a505a053](examples.md#ex-a505a053), [ex-e355e513](examples.md#ex-e355e513), [ex-c318705e](examples.md#ex-c318705e) |
| Unclear or overlong report | 20 / 12 | 47 / 30 | 0 / 0 | 0 / 0 | 67 / 42 | [ex-38c52baf](examples.md#ex-38c52baf), [ex-8b098981](examples.md#ex-8b098981), [ex-a951d599](examples.md#ex-a951d599) |
| Asked too much | 16 / 12 | 28 / 22 | 0 / 0 | 0 / 0 | 44 / 34 | [ex-d840bfaa](examples.md#ex-d840bfaa), [ex-38f4ab5b](examples.md#ex-38f4ab5b), [ex-b855f407](examples.md#ex-b855f407) |
| Ignored repository or standing rules | 5 / 4 | 26 / 14 | 0 / 0 | 0 / 0 | 31 / 18 | [ex-d6cf05fb](examples.md#ex-d6cf05fb), [ex-7c627c13](examples.md#ex-7c627c13), [ex-23d2bd36](examples.md#ex-23d2bd36) |
| Other | 0 / 0 | 16 / 10 | 0 / 0 | 0 / 0 | 16 / 10 | [ex-6fd3cdc2](examples.md#ex-6fd3cdc2), [ex-e0d749d8](examples.md#ex-e0d749d8), [ex-a1d94bf3](examples.md#ex-a1d94bf3) |
| Acted without asking | 1 / 1 | 12 / 7 | 0 / 0 | 0 / 0 | 13 / 8 | [ex-db6624a2](examples.md#ex-db6624a2), [ex-dde1c6f7](examples.md#ex-dde1c6f7), [ex-cb20a8b3](examples.md#ex-cb20a8b3) |
| Broke something that worked | 0 / 0 | 4 / 4 | 0 / 0 | 0 / 0 | 4 / 4 | [ex-f204dc69](examples.md#ex-f204dc69), [ex-1ca189bf](examples.md#ex-1ca189bf), [ex-71f7a280](examples.md#ex-71f7a280) |

## Mistakes per 100 labeled human messages

| Harness | Labeled messages | Sessions | Mistakes | Per 100 |
| --- | --- | --- | --- | --- |
| Claude | 2436 | 84 | 125 | 5.1 |
| Codex | 13815 | 313 | 728 | 5.3 |
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
| Ignored instruction | Codex | gpt-6-astra | 66 |
| Ignored instruction | Codex | gpt-5.6-sol | 49 |
| Ignored instruction | Codex | gpt-6.1-sol | 32 |
| Ignored instruction | Codex | gpt-5.5 | 6 |
| Ignored instruction | Codex | gpt-5-codex | 1 |
| Ignored instruction | Codex | gpt-5.1-codex | 1 |
| Wrong tool or approach | Claude | claude-opus-5-5 | 4 |
| Wrong tool or approach | Claude | claude-fable-5 | 2 |
| Wrong tool or approach | Claude | claude-fable-5-2 | 1 |
| Wrong tool or approach | Claude | claude-opus-5 | 1 |
| Wrong tool or approach | Claude | claude-sonnet-5 | 1 |
| Wrong tool or approach | Codex | gpt-5.6-sol | 65 |
| Wrong tool or approach | Codex | gpt-6-astra | 25 |
| Wrong tool or approach | Codex | gpt-6.1-sol | 19 |
| Wrong tool or approach | Codex | gpt-5.6-terra | 2 |
| Wrong tool or approach | Codex | gpt-daybreak-blue-latest | 1 |
| Wrong tool or approach | Codex | gpt-5.5 | 1 |
| Wrong tool or approach | Codex | gpt-5-codex | 1 |
| Unnecessary work | Claude | claude-fable-5 | 3 |
| Unnecessary work | Claude | claude-sonnet-5 | 1 |
| Unnecessary work | Codex | gpt-5.6-sol | 51 |
| Unnecessary work | Codex | gpt-6-astra | 24 |
| Unnecessary work | Codex | gpt-6.1-sol | 20 |
| Unnecessary work | Codex | gpt-5.5 | 1 |
| Unnecessary work | Codex | gpt-6-sol | 1 |
| Unnecessary work | Codex | gpt-5.6-terra | 1 |
| Wrong fact or unchecked assumption | Claude | claude-opus-5-5 | 5 |
| Wrong fact or unchecked assumption | Claude | claude-fable-5 | 3 |
| Wrong fact or unchecked assumption | Claude | claude-opus-5 | 2 |
| Wrong fact or unchecked assumption | Claude | claude-fable-5-2 | 2 |
| Wrong fact or unchecked assumption | Claude | claude-sonnet-5 | 2 |
| Wrong fact or unchecked assumption | Claude | claude-opus-4-8 | 1 |
| Wrong fact or unchecked assumption | Codex | gpt-5.6-sol | 39 |
| Wrong fact or unchecked assumption | Codex | gpt-6-astra | 22 |
| Wrong fact or unchecked assumption | Codex | gpt-6.1-sol | 13 |
| Wrong fact or unchecked assumption | Codex | gpt-5.5 | 3 |
| Wrong fact or unchecked assumption | Codex | gpt-5.4-mini | 1 |
| Wrong fact or unchecked assumption | Codex | gpt-5.6-terra | 1 |
| Wrong fact or unchecked assumption | Codex | gpt-5.2-codex | 1 |
| Wrong fact or unchecked assumption | Codex | gpt-5-codex | 1 |
| False or overstated claim of result | Claude | claude-sonnet-5 | 5 |
| False or overstated claim of result | Claude | claude-opus-4-8 | 2 |
| False or overstated claim of result | Claude | claude-opus-5-5 | 2 |
| False or overstated claim of result | Codex | gpt-6-astra | 27 |
| False or overstated claim of result | Codex | gpt-5.6-sol | 21 |
| False or overstated claim of result | Codex | gpt-5.5 | 15 |
| False or overstated claim of result | Codex | gpt-6.1-sol | 13 |
| False or overstated claim of result | Codex | gpt-5.4-mini | 2 |
| False or overstated claim of result | Codex | gpt-5.1-codex | 1 |
| Missed the question | Claude | claude-opus-5-5 | 7 |
| Missed the question | Claude | claude-sonnet-5 | 4 |
| Missed the question | Claude | claude-opus-5 | 1 |
| Missed the question | Claude | claude-fable-5 | 1 |
| Missed the question | Codex | gpt-5.6-sol | 41 |
| Missed the question | Codex | gpt-6-astra | 16 |
| Missed the question | Codex | gpt-6.1-sol | 7 |
| Missed the question | Codex | gpt-6-sol | 2 |
| Missed the question | Codex | gpt-5.3-codex | 1 |
| Missed the question | Codex | gpt-daybreak-blue-latest | 1 |
| Unclear or overlong report | Claude | claude-opus-5-5 | 8 |
| Unclear or overlong report | Claude | claude-sonnet-5 | 5 |
| Unclear or overlong report | Claude | claude-fable-5 | 4 |
| Unclear or overlong report | Claude | claude-opus-5 | 3 |
| Unclear or overlong report | Codex | gpt-5.6-sol | 21 |
| Unclear or overlong report | Codex | gpt-6-astra | 16 |
| Unclear or overlong report | Codex | gpt-6.1-sol | 8 |
| Unclear or overlong report | Codex | gpt-6-sol | 1 |
| Unclear or overlong report | Codex | gpt-5.5 | 1 |
| Asked too much | Claude | claude-opus-5-5 | 11 |
| Asked too much | Claude | claude-sonnet-5 | 4 |
| Asked too much | Claude | claude-opus-5 | 1 |
| Asked too much | Codex | gpt-5.6-sol | 13 |
| Asked too much | Codex | gpt-6-astra | 5 |
| Asked too much | Codex | gpt-6.1-sol | 4 |
| Asked too much | Codex | gpt-5.6-terra | 3 |
| Asked too much | Codex | gpt-5.1-codex | 1 |
| Asked too much | Codex | gpt-6-sol | 1 |
| Asked too much | Codex | gpt-5.4-mini | 1 |
| Ignored repository or standing rules | Claude | claude-sonnet-5 | 2 |
| Ignored repository or standing rules | Claude | claude-fable-5 | 2 |
| Ignored repository or standing rules | Claude | claude-opus-5-5 | 1 |
| Ignored repository or standing rules | Codex | gpt-5.6-sol | 11 |
| Ignored repository or standing rules | Codex | gpt-6-astra | 11 |
| Ignored repository or standing rules | Codex | gpt-6.1-sol | 3 |
| Ignored repository or standing rules | Codex | gpt-5.5 | 1 |
| Other | Codex | gpt-6-astra | 7 |
| Other | Codex | gpt-6.1-sol | 5 |
| Other | Codex | gpt-5.6-sol | 3 |
| Other | Codex | gpt-5.5 | 1 |
| Acted without asking | Claude | claude-opus-5-5 | 1 |
| Acted without asking | Codex | gpt-5.6-sol | 7 |
| Acted without asking | Codex | gpt-6.1-sol | 5 |
| Broke something that worked | Codex | gpt-6-astra | 2 |
| Broke something that worked | Codex | gpt-5.6-sol | 1 |
| Broke something that worked | Codex | gpt-5.5 | 1 |
