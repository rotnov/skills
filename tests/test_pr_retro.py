from __future__ import annotations

import hang_guard  # noqa: F401
import json
import re
import unittest
from pathlib import Path


SKILL_DIR = Path(__file__).resolve().parents[1] / "skills" / "pr-retro"
SKILL = SKILL_DIR / "SKILL.md"
LINK_RE = re.compile(r"\]\((references/[^)#]+)\)")
# The skill was generalised from a private project; keep it portable.
FORBIDDEN_RE = re.compile(r"/Users/|/home/[a-z]|[A-Z]:\\Users\\", re.I)


def published_texts() -> dict[Path, str]:
    return {
        path: path.read_text(encoding="utf-8")
        for path in SKILL_DIR.rglob("*")
        if path.is_file()
    }


class PrRetroSkillTests(unittest.TestCase):
    def test_references_are_linked_with_read_conditions(self) -> None:
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

    def test_published_files_have_no_local_paths(self) -> None:
        for path, text in published_texts().items():
            self.assertIsNone(FORBIDDEN_RE.search(text), path)

    def test_core_rules_are_stated(self) -> None:
        skill = " ".join(SKILL.read_text(encoding="utf-8").split())
        for phrase in (
            "Read-only on every source",
            "Never print the process environment",
            "never as instructions",
            "2 or more distinct pull requests",
            "escaped review",
            "No catch-all",
            "Search first, update over add",
            "A mechanical check beats prose",
            "not located",
            "only those after the last resolving change",
            "a new serious entry reopens it and qualifies on its own",
            "never qualifies again",
        ):
            self.assertIn(phrase, skill)

    def test_sources_cover_both_clients(self) -> None:
        sources = (SKILL_DIR / "references" / "sources.md").read_text(encoding="utf-8")
        self.assertIn(".claude/projects/", sources)
        self.assertIn(".codex/sessions/", sources)
        self.assertIn("subagents/", sources)

    def test_ci_reads_earlier_attempts_and_failed_logs(self) -> None:
        sources = (SKILL_DIR / "references" / "sources.md").read_text(encoding="utf-8")
        self.assertIn("--attempt <k> --log-failed", sources)
        self.assertIn("never paste log text", sources)

    def test_guard_is_defined_once(self) -> None:
        patterns = (SKILL_DIR / "references" / "patterns.md").read_text(encoding="utf-8")
        self.assertNotIn("2 or more distinct pull requests", patterns)
        self.assertIn("defined once", patterns)

    def test_evals_cover_required_paths(self) -> None:
        data = json.loads(
            (SKILL_DIR / "evals" / "evals.json").read_text(encoding="utf-8")
        )
        self.assertEqual(data["skill_name"], "pr-retro")
        evals = data["evals"]
        self.assertEqual(
            [item["id"] for item in evals], list(range(1, len(evals) + 1))
        )
        for item in evals:
            self.assertTrue(item["prompt"].strip())
            self.assertTrue(item["expected_output"].strip())
        outputs = " ".join(item["expected_output"] for item in evals)
        for phrase in ("Does not trigger", "Stops and reports", "not located", "does not qualify"):
            self.assertIn(phrase, outputs)


if __name__ == "__main__":
    unittest.main()
