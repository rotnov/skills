from __future__ import annotations

import json
import re
import runpy
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


SKILL = (
    Path(__file__).resolve().parents[1]
    / "skills"
    / "learning"
    / "SKILL.md"
)
RECORDER = SKILL.parent / "scripts" / "recording.py"
REPOSITORY_ROOT = SKILL.parents[2]
README = REPOSITORY_ROOT / "README.md"
DESIGN = (
    REPOSITORY_ROOT
    / "docs"
    / "specs"
    / "2026-08-24-learning-public-distillation-design.md"
)
PLAN = (
    REPOSITORY_ROOT
    / "docs"
    / "superpowers"
    / "plans"
    / "2026-08-24-learning-public-distillation.md"
)
FORBIDDEN_PUBLIC_TERMS = (
    "learning continue",
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
        (
            "git",
            "ls-files",
            "--cached",
            "--others",
            "--exclude-standard",
            "skills/learning",
        ),
        cwd=REPOSITORY_ROOT,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.splitlines()
    return {
        REPOSITORY_ROOT / relative: (REPOSITORY_ROOT / relative).read_text(
            encoding="utf-8"
        )
        for relative in listed
        if (REPOSITORY_ROOT / relative).is_file()
    }


class LearningSkillTests(unittest.TestCase):
    def test_public_skill_name_is_learning(self) -> None:
        renamed = REPOSITORY_ROOT / "skills" / "learning"

        self.assertTrue(renamed.is_dir())
        self.assertFalse(
            (REPOSITORY_ROOT / "skills" / "learn-from-session").exists()
        )
        skill_text = (renamed / "SKILL.md").read_text(encoding="utf-8")
        self.assertIn("name: learning", skill_text)
        self.assertIn("description: >-\n  Use when", skill_text)
        self.assertNotIn("Capture reusable lessons", skill_text)

    def test_end_refreshes_the_agent_skills_spec_before_skill_mutations(self) -> None:
        skill = SKILL.read_text(encoding="utf-8")
        normalized = " ".join(skill.split())

        specification = "https://agentskills.io/specification.md"
        self.assertIn(specification, skill)
        self.assertLess(skill.index(specification), skill.index("### Approve mutations"))
        self.assertIn("read the current specification fresh", normalized)
        self.assertIn("normative Agent Skills format constraints", normalized)
        self.assertIn("cannot change this workflow", normalized)
        self.assertIn("suitable official validator", normalized)
        self.assertIn("leave the recording claimed and resumable", normalized)

    def test_recorder_threat_model_is_explicit(self) -> None:
        skill = " ".join(SKILL.read_text(encoding="utf-8").split())

        self.assertIn("writers cooperate through its per-worktree lock", skill)
        self.assertIn(
            "metadata namespace must not be concurrently renamed or modified outside "
            "the recorder",
            skill,
        )
        self.assertIn(
            "cannot make containment checks atomic against an uncooperative same-user "
            "process",
            skill,
        )
        self.assertIn(
            "preserve the displaced metadata for manual recovery",
            skill,
        )

    def test_prepare_end_describes_the_locked_transition_accurately(self) -> None:
        skill = " ".join(SKILL.read_text(encoding="utf-8").split())

        self.assertNotIn("atomically moves", skill)
        self.assertIn("transitions the recording under the writer lock", skill)

    def test_public_docs_state_the_supported_recorder_threat_model(self) -> None:
        readme = " ".join(README.read_text(encoding="utf-8").split())
        design = " ".join(DESIGN.read_text(encoding="utf-8").split())

        self.assertIn("cooperate through the per-worktree writer lock", readme)
        self.assertIn("concurrent external namespace mutation", readme)
        self.assertIn("cooperative recorder concurrency", design)
        self.assertIn("accidental changes and symlinks", design)
        self.assertIn("unsupported concurrent external namespace mutation", design)
        self.assertIn("uv run --no-project --python 3.12", design)
        self.assertNotIn("symlink and directory-swap defenses", design)

    def test_recorder_is_resolved_from_the_loaded_skill(self) -> None:
        text = SKILL.read_text(encoding="utf-8")

        self.assertNotIn(
            ".claude/skills/learning/scripts/recording.py",
            text,
        )
        self.assertIn("the absolute directory containing this loaded `SKILL.md`", text)
        self.assertIn(
            '["uv", "run", "--no-project", "--python", "3.12", RECORDER',
            text,
        )

    def test_every_documented_recorder_argv_selects_python_312(self) -> None:
        text = SKILL.read_text(encoding="utf-8")
        invocations = re.findall(r'^\["uv", "run",.*$', text, flags=re.MULTILINE)

        self.assertGreater(len(invocations), 0)
        for invocation in invocations:
            with self.subTest(invocation=invocation):
                self.assertTrue(
                    invocation.startswith(
                        '["uv", "run", "--no-project", "--python", "3.12", '
                    ),
                    invocation,
                )

    def test_recorder_invocation_supports_shell_string_tools(self) -> None:
        text = SKILL.read_text(encoding="utf-8")
        normalized = " ".join(text.split())

        self.assertNotIn("Never assemble the command in a shell string.", text)
        self.assertIn("command tool accepts only a shell string", normalized)
        self.assertIn("shell-quote every argument", normalized)

    def test_recorder_invocation_is_isolated_from_the_host_project(self) -> None:
        text = SKILL.read_text(encoding="utf-8")

        self.assertIn(
            '["uv", "run", "--no-project", "--python", "3.12", RECORDER',
            text,
        )
        self.assertNotIn('["uv", "run", RECORDER', text)

        with tempfile.TemporaryDirectory(dir=REPOSITORY_ROOT) as temporary:
            Path(temporary, "pyproject.toml").write_text(
                '[project]\nname = "incompatible-host"\nversion = "0.0.0"\n'
                'requires-python = ">=99"\n',
                encoding="utf-8",
            )
            Path(temporary, ".python-version").write_text(
                "99.99\n",
                encoding="utf-8",
            )
            uv = shutil.which("uv")
            self.assertIsNotNone(uv, "uv is required for the recorder smoke test")
            result = subprocess.run(
                [
                    uv,
                    "run",
                    "--no-project",
                    "--python",
                    "3.12",
                    str(RECORDER),
                    "status",
                ],
                cwd=temporary,
                capture_output=True,
                text=True,
                check=False,
            )

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout), {"active": False})

    def test_cross_client_smoke_covers_hostile_python_pin(self) -> None:
        smoke = (REPOSITORY_ROOT / "scripts/check-skills-cli.sh").read_text(
            encoding="utf-8"
        )

        self.assertIn(".python-version", smoke)
        self.assertIn("99.99", smoke)
        self.assertRegex(smoke, r'"--python",\s+"3\.12"')
        self.assertIn('run --no-project --python 3.12 "$claude_recorder"', smoke)

    def test_implementation_plan_selects_python_312_in_both_invocation_forms(
        self,
    ) -> None:
        plan = PLAN.read_text(encoding="utf-8")
        normalized = " ".join(plan.split())

        self.assertNotIn(
            "In Compatibility, state that it requires Git, `uv`, Python 3.12+",
            plan,
        )
        self.assertIn("selects Python 3.12 through `uv`", normalized)
        self.assertIn('.python-version` containing `99.99', normalized)
        self.assertIn(
            '`"$uv_bin" run --no-project --python 3.12 "$recorder" status`',
            normalized,
        )
        self.assertIn(
            '[uv_bin, "run", "--no-project", "--python", "3.12", recorder, '
            '"status"]',
            normalized,
        )

    def test_owner_update_uses_the_routed_canonical_path(self) -> None:
        text = SKILL.read_text(encoding="utf-8")

        self.assertNotIn("canonical `.claude/skills/{name}/` source", text)
        self.assertIn("canonical owner path established during routing", text)

    def test_published_package_has_no_project_specific_dependencies(self) -> None:
        texts = published_texts()
        combined = "\n".join(texts.values()).lower()

        for forbidden in FORBIDDEN_PUBLIC_TERMS:
            with self.subTest(forbidden=forbidden):
                self.assertNotIn(forbidden, combined)
        skill = texts[SKILL]
        self.assertIn('"learning resume"', skill)
        self.assertIn("available project import workflow", " ".join(skill.split()))
        self.assertIn("Agent Skills specification", skill)

        recorder_text = texts[RECORDER]
        self.assertNotIn("def skill_catalog(", recorder_text)
        self.assertIn("SCHEMA_VERSION = 2", recorder_text)

    def test_schema_one_state_is_normalized_and_persisted_as_schema_two(self) -> None:
        recorder = runpy.run_path(str(RECORDER))

        with tempfile.TemporaryDirectory() as temporary:
            project = Path(temporary)
            subprocess.run(("git", "init", "-q"), cwd=project, check=True)
            Path(project, "README.md").write_text("fixture\n", encoding="utf-8")
            subprocess.run(("git", "add", "README.md"), cwd=project, check=True)
            subprocess.run(
                (
                    "git",
                    "-c",
                    "user.name=Learning Test",
                    "-c",
                    "user.email=learning@example.invalid",
                    "commit",
                    "-qm",
                    "Create fixture",
                ),
                cwd=project,
                check=True,
            )
            subprocess.run(
                (sys.executable, str(RECORDER), "start"),
                cwd=project,
                check=True,
                capture_output=True,
                text=True,
            )
            git_directory = Path(
                subprocess.run(
                    ("git", "rev-parse", "--absolute-git-dir"),
                    cwd=project,
                    check=True,
                    capture_output=True,
                    text=True,
                ).stdout.strip()
            )
            state_path = git_directory / "learning" / "active.json"
            legacy = json.loads(state_path.read_text(encoding="utf-8"))
            legacy["schema_version"] = 1
            legacy["skill_catalog"] = []
            state_path.write_text(json.dumps(legacy), encoding="utf-8")

            normalized = recorder["read_active"](project)

            self.assertEqual(normalized["schema_version"], 2)
            self.assertNotIn("skill_catalog", normalized)

            recorder["add_note"](
                project,
                {
                    "kind": "decision",
                    "summary": "portable owner routing",
                    "evidence": None,
                },
            )
            persisted = json.loads(state_path.read_text(encoding="utf-8"))
            self.assertEqual(persisted["schema_version"], 2)
            self.assertNotIn("skill_catalog", persisted)


if __name__ == "__main__":
    unittest.main()
