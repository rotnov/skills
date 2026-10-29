from __future__ import annotations

import json
import re
import unittest
from pathlib import Path


SKILL_DIR = Path(__file__).resolve().parents[1] / "skills" / "next-milestone"
SKILL = SKILL_DIR / "SKILL.md"
LINK_RE = re.compile(r"\]\((references/[^)#]+)\)")
FORBIDDEN_TERMS = (
    "pycc",
    "issue-select",
    "issue-implement",
    "issue-to-plan",
    "hook-install-check",
    "DISTRIBUTION.md",
    "Tier-1",
    "advisor",
    ".claude/",
    ".agents/",
)
DECISION_ID_RE = re.compile(r"\bD-\d{3}\b")


def published_texts() -> dict[Path, str]:
    return {
        path: path.read_text(encoding="utf-8")
        for path in SKILL_DIR.rglob("*")
        if path.is_file()
    }


class NextMilestoneSkillTests(unittest.TestCase):
    def test_references_are_linked_one_level_deep_with_read_conditions(self) -> None:
        skill = SKILL.read_text(encoding="utf-8")
        linked = set(LINK_RE.findall(skill))
        present = {
            f"references/{path.name}" for path in (SKILL_DIR / "references").iterdir()
        }
        self.assertEqual(linked, present)
        for relative in present:
            text = (SKILL_DIR / relative).read_text(encoding="utf-8")
            first_paragraph = text.split("\n\n", 2)[1]
            self.assertTrue(first_paragraph.startswith("Read this when"), relative)

    def test_published_files_are_portable(self) -> None:
        for path, text in published_texts().items():
            for term in FORBIDDEN_TERMS:
                self.assertNotIn(term, text, f"{path}: {term}")
            self.assertIsNone(DECISION_ID_RE.search(text), path)

    def test_github_milestones_are_the_source_of_truth(self) -> None:
        skill = " ".join(SKILL.read_text(encoding="utf-8").split())
        self.assertIn("GitHub milestones are the source of truth", skill)
        self.assertIn("never decide from it", skill)
        self.assertIn("stop and report", skill)
        self.assertIn("Do not substitute a local roadmap file", skill)
        self.assertIn("at most one new milestone ahead", skill)
        self.assertIn('"content complete", not "released"', skill)

    def test_evals_cover_required_paths(self) -> None:
        data = json.loads(
            (SKILL_DIR / "evals" / "evals.json").read_text(encoding="utf-8")
        )
        self.assertEqual(data["skill_name"], "next-milestone")
        evals = data["evals"]
        self.assertEqual(
            [item["id"] for item in evals], list(range(1, len(evals) + 1))
        )
        for item in evals:
            self.assertTrue(item["prompt"].strip())
            self.assertTrue(item["expected_output"].strip())
        outputs = " ".join(item["expected_output"] for item in evals)
        for phrase in ("Does not trigger", "Stops and reports", "exactly one", "Paused"):
            self.assertIn(phrase, outputs)


if __name__ == "__main__":
    unittest.main()
