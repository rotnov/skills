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
name: learning
description: >-
  Use when a user asks to learn from a real work session or resume a prior session.
---

# Learning
"""
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "SKILL.md"
            path.write_text(content, encoding="utf-8")

            fields, body = validate_skills.parse_frontmatter(path)

        self.assertEqual(fields["name"], "learning")
        self.assertEqual(
            fields["description"],
            "Use when a user asks to learn from a real work session or resume a "
            "prior session.",
        )
        self.assertEqual(body, "# Learning")

    def _make_skill(self, root: Path, readme: str | None) -> Path:
        skill_dir = root / "demo"
        skill_dir.mkdir()
        (skill_dir / "SKILL.md").write_text(
            "---\nname: demo\ndescription: Demo skill. Use for tests.\n---\n\n# Demo\n",
            encoding="utf-8",
        )
        if readme is not None:
            (skill_dir / "README.md").write_text(readme, encoding="utf-8")
        return skill_dir

    VALID_README = """# Demo

## Install the skill

> Install the demo skill from
> <https://github.com/rotnov/skills/tree/main/skills/demo> for this client,
> globally for Claude Code and Codex with the pinned skills@1.5.20 CLI,
> then read its SKILL.md.

## Use
"""

    def test_valid_skill_with_install_prompt_passes(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            skill_dir = self._make_skill(Path(directory), self.VALID_README)
            self.assertEqual(validate_skills.validate_skill(skill_dir), [])

    def test_missing_readme_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            skill_dir = self._make_skill(Path(directory), None)
            problems = validate_skills.validate_skill(skill_dir)
        self.assertTrue(any("missing README.md" in p for p in problems))

    def test_readme_without_install_prompt_is_rejected(self) -> None:
        readme = "# Demo\n\n## Install the skill\n\nRun the CLI.\n"
        with tempfile.TemporaryDirectory() as directory:
            skill_dir = self._make_skill(Path(directory), readme)
            problems = validate_skills.validate_skill(skill_dir)
        self.assertTrue(any("blockquoted LLM prompt" in p for p in problems))

    def test_install_prompt_must_link_its_own_skill(self) -> None:
        readme = self.VALID_README.replace("skills/demo>", "skills/other>")
        with tempfile.TemporaryDirectory() as directory:
            skill_dir = self._make_skill(Path(directory), readme)
            problems = validate_skills.validate_skill(skill_dir)
        self.assertTrue(any("skills/demo" in p for p in problems))

    def test_client_agents_metadata_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            skill_dir = self._make_skill(Path(directory), self.VALID_README)
            (skill_dir / "agents").mkdir()
            (skill_dir / "agents" / "openai.yaml").write_text(
                "interface: {}\n", encoding="utf-8"
            )
            problems = validate_skills.validate_skill(skill_dir)
        self.assertTrue(any("agents/" in p for p in problems))


if __name__ == "__main__":
    unittest.main()
