# Active-Learning Public Distillation Implementation Plan

> **Execution:** After approval, use `subagent-driven-development` only when the
> user explicitly selects delegated execution; otherwise use `executing-plans`.
> Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Publish a self-contained learn-from-session skill that retains the safe
Git-local recorder while removing repository-specific workflows, terminology, and
stale evaluation evidence.

**Architecture:** `SKILL.md` owns a portable start/resume/end workflow and treats all
specialized authoring or import helpers as optional capabilities. `recording.py`
continues to own deterministic worktree-local state and claim safety. Portable contract
tests and a standard-library recorder suite enforce the package independently of its
source environment.

**Tech Stack:** Agent Skills Markdown/YAML, Python 3.12+ standard library, Git, `uv`,
`unittest`, pinned `skills@1.5.20`, pinned `skills-ref`, pre-commit 4.6.1.

## Global Constraints

- The only public user operations are `learn-from-session start`, `learn-from-session
  resume`, and `learn-from-session end`.
- Resolve `recording.py` relative to the loaded `SKILL.md`; never assume an install
  root.
- Invoke the recorder with `uv run --no-project --python 3.12` through structured argv
  or a safely quoted shell-string fallback.
- Keep recorder state below the current worktree's resolved Git metadata and out of
  tracked files.
- Require explicit approval before every durable lesson-driven mutation.
- Do not require another skill, plugin, repository hierarchy, task system, or fixed
  skill directory layout.
- Use only the Python standard library in repository unit tests.
- Preserve the existing validation pins and run the complete pre-commit gate.

---

### Task 1: Distill the published behavior contract

**Files:**

- Modify: `tests/test_learn_from_session.py`
- Modify: `skills/learn-from-session/SKILL.md`
- Modify: `skills/learn-from-session/scripts/recording.py`
- Replace: `skills/learn-from-session/evals/evals.json`
- Delete: `skills/learn-from-session/evals/benchmark.json`
- Delete: `skills/learn-from-session/evals/iteration-1/`

**Interfaces:**

- Consumes: recorder argv prefix
  `uv run --no-project --python 3.12 {absolute recorder path}` and internal commands
  from `scripts/recording.py`.
- Produces: the public `start`, `resume`, and `end` behavior contract plus six portable
  owner-resolution evaluation scenarios.

- [ ] **Step 1: Expand the failing portability contract test**

Replace the narrow project-specific test in `tests/test_learn_from_session.py` with a scan
of every published text artifact:

```python
FORBIDDEN_PUBLIC_TERMS = (
    "learn-from-session continue",
    "import-skill",
    "create-skill",
    "ievo",
    "umbrella",
    "managed-repository",
    "submodule checkout",
    '.claude/skills/*/skill.md',
    '.agents/skills/*/skill.md',
    '.codex/skills/*/skill.md',
)


def published_texts() -> dict[Path, str]:
    listed = subprocess.run(
        ("git", "ls-files", "--cached", "--others", "--exclude-standard",
         "skills/learn-from-session"),
        cwd=SKILL.parents[2],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.splitlines()
    return {
        SKILL.parents[2] / relative: (SKILL.parents[2] / relative).read_text(
            encoding="utf-8"
        )
        for relative in listed
        if (SKILL.parents[2] / relative).is_file()
    }


def test_published_package_has_no_project_specific_dependencies(self) -> None:
    texts = published_texts()
    combined = "\n".join(texts.values()).lower()

    for forbidden in FORBIDDEN_PUBLIC_TERMS:
        with self.subTest(forbidden=forbidden):
            self.assertNotIn(forbidden, combined)
    skill = texts[SKILL]
    self.assertIn('"learn-from-session resume"', skill)
    self.assertIn("available project import workflow", " ".join(skill.split()))
    self.assertIn("Agent Skills specification", skill)
```

- [ ] **Step 2: Run the contract test and verify RED**

Run:

```bash
python3 -m unittest discover -s tests -p 'test_learn_from_session.py' -v
```

Expected: FAIL for the old `continue` trigger and project-specific terms in
`SKILL.md` and evaluation artifacts.

- [ ] **Step 3: Remove the layout-specific recorder catalog**

The recorder's skill snapshot is not used by its state machine and contradicts the
portable owner-routing contract. In `recording.py`, bump `SCHEMA_VERSION` from 1 to 2,
delete the `skill_catalog` discovery function, and omit `skill_catalog` from new state.
Keep read compatibility for schema 1: require and validate its legacy catalog with the
existing entry-validation rules, then normalize the returned payload in memory by
removing `skill_catalog` and setting `schema_version` to 2. The next mutation persists
schema 2. Schema 2 rejects `skill_catalog` as an extra key. Keep `hashlib` because bearer
claims still use SHA-256. The package scan already fails on the three fixed catalog
patterns; also add this static assertion before changing the script:

```python
recorder_text = texts[SKILL.parent / "scripts" / "recording.py"]
self.assertNotIn("def skill_catalog(", recorder_text)
self.assertIn("SCHEMA_VERSION = 2", recorder_text)
```

Run the focused contract test before the implementation and expect it to fail because
schema version 1 still generates the catalog; run it again after removal and expect
PASS. Add a recorder regression that writes a valid schema-1 payload with an empty
catalog, reads it as normalized schema 2 without the catalog, performs one mutation,
and verifies schema 2 was persisted. Owner resolution happens fresh from repository
evidence at end time and never from recorder state.

- [ ] **Step 4: Rewrite `SKILL.md` around the portable lifecycle**

Keep the frontmatter description below 1024 characters and use these sections in this
order:

```markdown
# Learn from Session

## Boundaries
## Recorder invocation
## Start
## Observe
## Resume in a later task
## End
### Claim a stable snapshot
### Extract candidate lessons
### Resolve each owner
### Approve mutations
### Apply through the canonical owner
### Validate and close
## Failure behavior
```

The owner table must encode these exact outcomes without naming helper skills:

```markdown
| Match | Disposition |
| --- | --- |
| One editable owner in this repository | Propose an in-place canonical update. |
| Owner in another Git repository | Propose an update in that repository's own checkout and keep this recording resumable until verification. |
| Installed-only owner with authoritative origin | Propose bringing the pinned source into the project with provenance, then adapting it. |
| Installed-only owner without authoritative origin | Ask for the source repository and path; do not edit or copy the cache. |
| Several plausible owners | Ask the user to select or refine the boundary. |
| No owner | Propose a new project skill conforming to the Agent Skills specification. |
| No reusable lesson | Make no skill change. |
```

State that a documented available project import workflow or skill-authoring workflow
may be used, but neither is required. Replace every public `continue` trigger with
`resume`. Keep the internal bearer-claim behavior, explicit mutation preview, safe-token
transport, and compare-and-close rule.

- [ ] **Step 5: Replace the current evaluation specification**

Write six portable cases in `skills/learn-from-session/evals/evals.json` with these IDs and
required outcomes:

```json
[
  {"id": "invocation-is-not-ownership", "outcome": "resolve responsibility without preferring the invoked skill"},
  {"id": "external-owner-with-origin", "outcome": "preserve origin and pinned revision before project adaptation"},
  {"id": "external-owner-without-origin", "outcome": "stop for authoritative provenance without editing the cache"},
  {"id": "cross-repository-owner", "outcome": "update only in the owning repository after approval"},
  {"id": "no-existing-owner", "outcome": "propose a spec-conforming project skill and wait for approval"},
  {"id": "nothing-reusable", "outcome": "make no skill change and close the claimed snapshot"}
]
```

Retain the repository's existing `prompt`, `expected_output`, and `assertions` schema;
the abbreviated objects above define content, not a schema change.
Add a top-level README note in Task 3 that these are unscored portable evaluation
inputs, not evidence of an executed benchmark. A fresh scored evaluation is explicitly
out of scope because this public repository has no pinned evaluator or grading runner;
no quality score may be claimed from this file.

- [ ] **Step 6: Remove stale evidence**

Delete only the historical evaluation results named by the approved design:

```bash
git rm skills/learn-from-session/evals/benchmark.json
git rm -r skills/learn-from-session/evals/iteration-1
```

Expected: `skills/learn-from-session/evals/` contains only `evals.json`.

- [ ] **Step 7: Verify GREEN and validate the skill**

Run:

```bash
python3 -m unittest discover -s tests -p 'test_learn_from_session.py' -v
python3 scripts/validate_skills.py
./scripts/check-agent-skills-spec.sh
./scripts/check-skills-cli.sh
```

Expected: every command exits 0; no forbidden public term is found.

- [ ] **Step 8: Commit the distilled contract**

```bash
git add skills/learn-from-session tests/test_learn_from_session.py
git commit -m "Distill learn-from-session for public use"
```

---

### Task 2: Add independent recorder characterization coverage

**Files:**

- Create: `tests/test_learn_from_session_recording.py`
- Modify only if a characterization test exposes a defect:
  `skills/learn-from-session/scripts/recording.py`

**Interfaces:**

- Consumes: `start_recording`, `add_note`, `recording_status`, `prepare_end`,
  `resume_end`, `adopt_end`, `reopen_recording`, `close_recording`, `read_active`,
  `state_path`, and `main` from `recording.py`.
- Produces: standard-library regression coverage for the recorder's security and
  lifecycle invariants.

- [ ] **Step 1: Create the standard-library test harness**

Use this module setup and fixture base:

```python
from __future__ import annotations

import contextlib
import hashlib
import importlib.util
import io
import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "skills/learn-from-session/scripts/recording.py"
SPEC = importlib.util.spec_from_file_location("learn_from_session_recording", SCRIPT)
assert SPEC and SPEC.loader
recording = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(recording)


def git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ("git", *args), cwd=repo, check=True, capture_output=True, text=True
    ).stdout.strip()


class RecorderTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)

    def init_repo(self, name: str = "repo") -> Path:
        repo = self.root / name
        repo.mkdir()
        git(repo, "init", "-q")
        (repo / "README.md").write_text("# Fixture\n", encoding="utf-8")
        git(repo, "add", "README.md")
        subprocess.run(
            ("git", "-c", "user.name=Recorder Test", "-c",
             "user.email=recorder@example.invalid", "commit", "-q", "-m", "fixture"),
            cwd=repo,
            check=True,
        )
        return repo
```

- [ ] **Step 2: Add lifecycle and input-safety characterization tests**

Implement one `unittest` method for each row, using `self.assertRaisesRegex` and
`mock.patch.object` rather than pytest fixtures:

| Test method | Arrange, act, and required assertions |
| --- | --- |
| `test_start_keeps_state_outside_worktree` | Initialize a repository, call `start_recording`, assert `phase == "open"`, `revision == 0`, `schema_version == 2`, no `skill_catalog` key, `state_path` is beneath `--absolute-git-dir`, and porcelain status is empty. |
| `test_schema_one_state_normalizes_and_remains_resumable` | Start schema 2, rewrite the payload as schema 1 with an empty `skill_catalog`, assert `read_active` returns schema 2 without the catalog, add one note, and assert the persisted file is schema 2 without the catalog. |
| `test_second_start_preserves_original_recording` | Start once, assert a second start raises `RecordingError` matching `already active`, and compare the persisted recording ID with the first ID. |
| `test_safe_tokens_round_trip` | Change to the fixture repository under `mock.patch("pathlib.Path.cwd", return_value=repo)`, call `main` for a labeled start and a note, then assert the joined label, summary, and evidence in `read_active`. |
| `test_unsafe_tokens_fail_before_state_mutation` | Run `safe_text` for `$(touch-marker)`, `quote'`, a newline, and `é` as subtests; each raises `RecordingError` matching `safe ASCII token`, and status remains `{"active": False}`. |
| `test_notes_are_bounded_and_require_open_phase` | Patch `MAX_EVENTS` to 1, add one note, assert the second raises `event limit`, prepare end, and assert a late note raises `end phase`. |
| `test_prepare_resume_and_compare_close` | Start and prepare end; assert persisted state contains only the SHA-256 claim digest, status omits `claim_id`, a second prepare fails, resume accepts the claim, wrong claim and wrong recording ID fail, stale revision close fails, matching close returns inactive. |
| `test_lost_claim_requires_authorized_adoption_and_rotates` | Assert unauthorized adoption, wrong recording ID, and stale revision fail; authorized adoption changes the claim and increments revision/adoption count; the old claim fails resume/reopen/close and the new claim resumes. |
| `test_reopen_preserves_evidence` | Prepare end, reopen with its claim, add a correction, prepare again, and assert a different claim plus the retained correction. |

For `test_prepare_resume_and_compare_close`, assert that only the claim hash persists,
status omits the bearer claim, a stale revision fails, and the matching revision removes
active state. For unsafe tokens, use `$(touch-marker)`, `quote'`, a newline, and `é` as
subtests and assert status remains `{"active": False}`.

- [ ] **Step 3: Add malformed-state and filesystem-defense tests**

Implement the following exact matrix:

| Test method | Arrange, act, and required assertions |
| --- | --- |
| `test_malformed_or_stale_states_fail_closed` | Mutate the persisted JSON once per subtest, write it back, assert `read_active` raises the expected message, and assert the state file still exists. |
| `test_invalid_utf8_and_oversized_states_fail_closed` | Replace state first with `b"\xff"` and then with `MAX_STATE_BYTES + 1` spaces; assert `malformed` and `exceeds` respectively. |
| `test_state_symlinks_fail_closed` | On non-Windows, test regular and dangling `active.json` links plus a `learn-from-session` directory link; assert regular-file/directory errors and no writes outside Git metadata. |
| `test_directory_swap_and_atomic_write_failure_fail_closed` | On non-Windows, swap the held directory before mutation and during child creation, assert the descriptor identity error and unchanged outside state; separately patch `write_all` to raise and assert no temporary file remains. |
| `test_unsupported_descriptor_backend_never_mutates` | Patch `descriptor_backend_supported` false; absent state returns inactive/unavailable, start raises `cannot safely write`, and an existing state directory makes status fail rather than inspect it. |

The malformed-state subtests must cover wrong event type, missing recording ID, unknown
phase, boolean schema/revision values, invalid end claim, and a stale repository root.
Patch `write_all` to raise `OSError("injected write failure")` and assert no temporary
file survives.

- [ ] **Step 4: Add worktree, catalog, and real-CLI tests**

Implement the following exact matrix:

| Test method | Arrange, act, and required assertions |
| --- | --- |
| `test_linked_worktrees_have_independent_recordings` | Add a detached linked worktree, start in both checkouts, and assert different IDs and state paths. |
| `test_cli_lifecycle_in_real_repository` | Redirect stdout, run start/note/prepare/resume/close through `main`, parse each JSON result, and assert final inactive state and clean porcelain status. |
| `test_cli_help_and_outside_git_error` | Capture help `SystemExit(0)` and the `prepare-end` help text; then change to a non-Git temporary directory, assert status returns 2, and assert stderr says `not inside a Git worktree`. |

Capture CLI output with `contextlib.redirect_stdout(io.StringIO())` and stderr with
`contextlib.redirect_stderr(io.StringIO())`. Assert the real lifecycle leaves
`git status --porcelain` empty.

- [ ] **Step 5: Run the characterization suite**

Run:

```bash
python3 -m unittest discover -s tests -p 'test_learn_from_session_recording.py' -v
```

Expected: PASS on supported descriptor platforms, with only the explicit Windows
symlink skips. If any existing behavior fails, stop and create a failing focused test
before changing `recording.py`; do not weaken or skip the invariant.

- [ ] **Step 6: Run all unit tests and commit**

```bash
python3 -m unittest discover -s tests -v
git add tests/test_learn_from_session_recording.py skills/learn-from-session/scripts/recording.py
git commit -m "Test learn-from-session recorder lifecycle"
```

Expected: all repository tests pass; `recording.py` remains unchanged unless a verified
defect required a separate tested fix.

---

### Task 3: Prove installed-package portability and run the release gate

**Files:**

- Modify: `README.md`
- Modify: `scripts/check-skills-cli.sh`
- Modify: pull-request description and review threads through GitHub after local commits

**Interfaces:**

- Consumes: the final `start`, `resume`, and `end` contract and its Git/uv requirements.
- Produces: accurate public discovery and a verified pull-request head.

- [ ] **Step 1: Add the learn-from-session README entry**

Add a `learn-from-session` section before `i-have-an-issue` with this content:

```markdown
### `learn-from-session`

Run a bounded learning loop over a real work session. It records concise checkpoints
outside the working tree, extracts reusable lessons, resolves each lesson to its
canonical project skill, and applies only user-approved changes.

Install it with the [skills CLI](https://skills.sh/):

```bash
npx skills add rotnov/skills --skill learn-from-session
```
```

In Compatibility, state that it requires Git, `uv`, and a platform with safe no-follow
directory-descriptor operations for recording mutations, and that each recorder
invocation selects Python 3.12 through `uv`. State that later-task continuation is
explicit through `learn-from-session resume`.
State that `evals/evals.json` contains unscored portable scenarios and that the
repository publishes no benchmark score for this version.

- [ ] **Step 2: Strengthen the cross-client installation smoke**

In `scripts/check-skills-cli.sh`, pin `uv_version="0.11.7"`. Use an existing `uv` when
its version matches; otherwise create a temporary venv below `install_root`, install
exactly `uv==0.11.7`, and use that binary. After the existing skills CLI copy install:

1. assert both `.claude/skills/learn-from-session/scripts/recording.py` and
   `.agents/skills/learn-from-session/scripts/recording.py` exist;
2. create one Git fixture per installed client under a directory whose path contains a
   space;
3. write a `pyproject.toml` declaring `requires-python = ">=99"` and a
   `.python-version` containing `99.99`;
4. from each fixture, invoke the installed recorder with the shell-string form
   `"$uv_bin" run --no-project --python 3.12 "$recorder" status`;
5. invoke the other installed copy from Python with the structured argv vector
   `[uv_bin, "run", "--no-project", "--python", "3.12", recorder, "status"]` and
   `shell=False`;
6. parse both outputs with Python and assert each is `{"active": false}`.

This one smoke proves the supporting script is copied, resolution from each installed
`SKILL.md` is possible, shell quoting preserves a path with spaces, structured argv is
executable without shell translation, and `--no-project` avoids the hostile host
project. Do not skip when `uv` is absent; provision only the pinned temporary binary.

- [ ] **Step 3: Run the complete local gate**

```bash
git diff --check
python3 scripts/validate_skills.py
python3 -m unittest discover -s tests -v
./scripts/check-agent-skills-spec.sh
./scripts/check-skills-cli.sh
pre-commit run --all-files
```

Expected: every command exits 0 with no skip beyond platform-specific Windows symlink
tests.

- [ ] **Step 4: Scan the public package**

```bash
rg -ni 'learn-from-session continue|import-skill|create-skill|ievo|umbrella|managed-repository|submodule checkout|amplifier|godfather|meddylib|surgent|SCRUM-[0-9]+' skills/learn-from-session
```

Expected: no matches.

- [ ] **Step 5: Commit documentation and smoke-test changes, then push**

```bash
git add README.md scripts/check-skills-cli.sh
git commit -m "Document portable learn-from-session workflow"
git push origin codex/add-active-learning
```

- [ ] **Step 6: Reconcile the pull request**

Update the PR body with the distilled package, removed stale evaluation evidence,
standard-library recorder coverage, and exact validation commands. Reply to every
applicable inline comment in its own thread, resolve only addressed threads, and verify
the unresolved-thread count is zero.

- [ ] **Step 7: Verify CI and request final review**

Confirm GitHub sees the pushed head SHA, wait for every required check to pass, then add
one top-level `@codex review` comment. Treat `eyes` only as in-progress. Completion is
either a submitted Codex review whose reviewed commit equals the exact head or the bot's
final `+1` reaction on that request comment after the head was confirmed; the latter is
the documented no-findings result. Evaluate new findings before changing code, require
zero unresolved threads, and never accept a result requested before the final head.
