from __future__ import annotations

import unittest
from pathlib import Path


SKILL = (
    Path(__file__).resolve().parents[1]
    / "skills"
    / "active-learning"
    / "SKILL.md"
)


class ActiveLearningSkillTests(unittest.TestCase):
    def test_recorder_is_resolved_from_the_loaded_skill(self) -> None:
        text = SKILL.read_text(encoding="utf-8")

        self.assertNotIn(
            ".claude/skills/active-learning/scripts/recording.py",
            text,
        )
        self.assertIn("the absolute directory containing this loaded `SKILL.md`", text)
        self.assertIn('["uv", "run", RECORDER', text)

    def test_cross_task_continuation_is_explicit(self) -> None:
        text = SKILL.read_text(encoding="utf-8")

        self.assertIn('"active-learning continue"', text)
        self.assertIn("does not run `status` automatically", text)
        self.assertNotIn(
            "The project startup workflow checks `status` on every new task.",
            text,
        )


if __name__ == "__main__":
    unittest.main()
