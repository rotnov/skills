from __future__ import annotations

import json
import subprocess
import tempfile
import unittest
from pathlib import Path


SKILL = (
    Path(__file__).resolve().parents[1]
    / "skills"
    / "active-learning"
    / "SKILL.md"
)
RECORDER = SKILL.parent / "scripts" / "recording.py"
REPOSITORY_ROOT = SKILL.parents[2]


class ActiveLearningSkillTests(unittest.TestCase):
    def test_recorder_is_resolved_from_the_loaded_skill(self) -> None:
        text = SKILL.read_text(encoding="utf-8")

        self.assertNotIn(
            ".claude/skills/active-learning/scripts/recording.py",
            text,
        )
        self.assertIn("the absolute directory containing this loaded `SKILL.md`", text)
        self.assertIn('["uv", "run", "--no-project", RECORDER', text)

    def test_cross_task_continuation_is_explicit(self) -> None:
        text = SKILL.read_text(encoding="utf-8")

        self.assertIn('"active-learning continue"', text)
        self.assertIn("does not run `status` automatically", text)
        self.assertNotIn(
            "The project startup workflow checks `status` on every new task.",
            text,
        )

    def test_recorder_invocation_supports_shell_string_tools(self) -> None:
        text = SKILL.read_text(encoding="utf-8")
        normalized = " ".join(text.split())

        self.assertNotIn("Never assemble the command in a shell string.", text)
        self.assertIn("command tool accepts only a shell string", normalized)
        self.assertIn("shell-quote every argument", normalized)

    def test_recorder_invocation_is_isolated_from_the_host_project(self) -> None:
        text = SKILL.read_text(encoding="utf-8")

        self.assertIn('["uv", "run", "--no-project", RECORDER', text)
        self.assertNotIn('["uv", "run", RECORDER', text)

        with tempfile.TemporaryDirectory(dir=REPOSITORY_ROOT) as temporary:
            Path(temporary, "pyproject.toml").write_text(
                '[project]\nname = "incompatible-host"\nversion = "0.0.0"\n'
                'requires-python = ">=99"\n',
                encoding="utf-8",
            )
            result = subprocess.run(
                ["uv", "run", "--no-project", str(RECORDER), "status"],
                cwd=temporary,
                capture_output=True,
                text=True,
                check=False,
            )

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("active", json.loads(result.stdout))

    def test_owner_update_uses_the_routed_canonical_path(self) -> None:
        text = SKILL.read_text(encoding="utf-8")

        self.assertNotIn("canonical `.claude/skills/{name}/` source", text)
        self.assertIn("canonical owner path established during routing", text)


if __name__ == "__main__":
    unittest.main()
