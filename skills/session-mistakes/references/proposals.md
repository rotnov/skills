# Skill proposals

Read this when turning ranked mistakes into skill proposals (stage 2).

## 1. Pick what to work on

`TOOL rank --top 5` scores each type as the sum of severity weights (1, 3, 8
for severity 1, 2, 3), so a rare damaging type can outrank a frequent mild
one. Read the printed descriptions and name the two or three recurring
patterns inside each top type: a skill fixes a pattern ("reports done without
running the check"), never a whole type ("false claims").

Drop a pattern when it is one project's private convention, when no
instruction could have prevented it, or when a mechanical check (a hook, a
lint, a test, a CI gate) would prevent it better than prose. Say so in the
report instead of drafting a skill.

## 2. Decide coverage

`PROPOSE coverage --category <id> --installed` (add `--term` for words from
the pattern) lists skills by keyword overlap and the folders it scanned. For
each candidate read its `SKILL.md` and decide:

- **covered**: the skill states the behavior that prevents the pattern and its
  description would route the situation to it. A mistake that still recurs
  under a covering skill is a routing or strength problem: propose a change.
- **partly covered**: the topic is the skill's, the rule is missing or weak.
- **not covered**.

State confidence separately for the two sources. Repository skills are a
complete list. Installed skills are only what sits in the known client
folders on this machine; whether a plugin skill is enabled, or was installed
when the mistakes happened, is not visible. Call that medium confidence at
best.

## 3. Draft

- A covered or partly covered pattern gets the smallest diff to the owning
  skill: the rule, in the place the skill already handles that moment.
- An uncovered pattern gets a new skill only when it is a topic of its own.
  Follow the project's skill rules; keep it instruction-only unless a script
  is needed.
- Write the draft on a branch or as files under the proposal folder. Never
  merge, push, publish or install it; the user decides.

Each proposal states: the pattern and its counts (mistakes, sessions,
harnesses), example ids from `examples.md`, the coverage decision with
confidence, the change, and the eval numbers.

## 4. Eval scenarios

A scenario rebuilds one real case with invented names and files, so that an
agent without the skill is likely to repeat the mistake. Keep the trap
realistic: the same missing check, the same tempting shortcut, the same
ambiguous instruction as the real session. Do not name the skill or the
mistake in the prompt.

```json
{
  "scenarios": [
    {
      "id": "short-kebab-id",
      "mistake": "taxonomy id",
      "from_examples": ["ex-1a2b3c4d"],
      "files": {"relative/path": "file content"},
      "setup": [["git", "init", "-q"]],
      "prompt": ["first user message", "second user message"],
      "read_back": ["relative/path"],
      "must_not_change": ["relative/path"],
      "must_run": ["regex over the shell commands the agent ran"],
      "must_not_run": ["regex"],
      "pass_criteria": ["one observable statement the judge checks"]
    }
  ]
}
```

Only `id`, `prompt` and at least one check are required. `prompt` is one user
message or a list of consecutive ones; use several turns when the real
mistake came from drift, such as a rule given early and broken later. `files`
and `setup` build a throwaway work directory; `setup` entries are argument
lists, never shell strings. `read_back` files are shown to the judge after the
run, and `must_not_change` files must be byte-identical afterwards. The judge
sees every turn's steps and the last turn's final message. A run passes when
every check and every criterion passes. Write criteria
about what can be seen in the transcript ("the final message says the test
suite was not run"), not about intentions.

## 5. Run and report

```text
PROPOSE compare --skill <skill dir> --scenarios <file> --trials 3 --dry-run
PROPOSE compare --skill <skill dir> --scenarios <file> --trials 3
```

Each scenario runs `--trials` times without the skill and as many with it,
through the `claude` CLI in a clean temporary folder with customizations
off. The skill arm gets the skill's `SKILL.md` in the system prompt and its
folder on disk. `--agent-model` picks the agent, `--model` the judge.

Report the totals and every scenario row as `passed / runs` for both arms.
Also report:

- scenarios where the baseline already passes (the scenario does not
  reproduce the mistake, or the model no longer makes it);
- scenarios the skill did not improve, or made worse;
- that the skill arm measures the instructions once loaded, not whether a
  client would route the situation to the skill;
- the agent model, since the mistakes were made by other models and harnesses;
- the run count: three trials per arm show a direction, not a rate.

Never drop a scenario because its numbers are unflattering.
