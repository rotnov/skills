from __future__ import annotations

import importlib.util
import tempfile
import unittest
from pathlib import Path


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "validate_skills.py"
SPEC = importlib.util.spec_from_file_location("validate_skills", SCRIPT)
assert SPEC and SPEC.loader
validate_skills = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(validate_skills)


class ValidateSkillsTests(unittest.TestCase):
    def test_parse_frontmatter_accepts_folded_description(self) -> None:
        content = """---
name: learn-from-session
description: >-
  Use when a user asks to learn from a real work session or resume a prior session.
---

# Learn from Session
"""
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "SKILL.md"
            path.write_text(content, encoding="utf-8")

            fields, body = validate_skills.parse_frontmatter(path)

        self.assertEqual(fields["name"], "learn-from-session")
        self.assertEqual(
            fields["description"],
            "Use when a user asks to learn from a real work session or resume a "
            "prior session.",
        )
        self.assertEqual(body, "# Learn from Session")


if __name__ == "__main__":
    unittest.main()
