"""Static contracts for the independently installed research skill."""

from __future__ import annotations

import json
import re
import shutil
import tempfile
import unittest
from pathlib import Path
from urllib.parse import unquote, urlsplit

ROOT = Path(__file__).resolve().parents[1]
BUNDLE = ROOT / "skills" / "multi-agent-research"
REFERENCES = {
    "island-protocol.md",
    "evidence-and-migration.md",
    "allocation-and-verification.md",
    "execution-modes.md",
}
LINK = re.compile(r"\[[^\]]*\]\(([^)]+)\)")


class MultiAgentResearchPackageTests(unittest.TestCase):
    def text(self, path: Path) -> str:
        self.assertTrue(path.is_file(), f"Missing package file: {path}")
        return path.read_text(encoding="utf-8")

    def test_core_and_reference_inventory(self) -> None:
        core = self.text(BUNDLE / "SKILL.md")
        self.assertTrue(core.startswith("---\n"))
        self.assertIn("name: multi-agent-research", core.split("---", 2)[1])
        reference_dir = BUNDLE / "references"
        self.assertTrue(reference_dir.is_dir())
        actual = {p.name for p in reference_dir.iterdir() if p.is_file()}
        self.assertEqual(actual, REFERENCES)
        for name in sorted(REFERENCES):
            with self.subTest(reference=name):
                self.assertTrue(self.text(reference_dir / name).strip())
                self.assertIn(f"references/{name}", core)
        self.assertTrue(self.text(BUNDLE / "agents" / "openai.yaml").strip())

    def test_all_thirty_evaluation_records(self) -> None:
        data = json.loads(self.text(BUNDLE / "evals" / "evals.json"))
        self.assertEqual(set(data), {"skill_name", "evals"})
        self.assertEqual(data["skill_name"], "multi-agent-research")
        self.assertIsInstance(data["evals"], list)
        self.assertEqual(len(data["evals"]), 30)
        ids = []
        for case in data["evals"]:
            self.assertEqual(set(case), {"id", "prompt", "expected_output"})
            self.assertIs(type(case["id"]), int)
            ids.append(case["id"])
            for field in ("prompt", "expected_output"):
                self.assertIsInstance(case[field], str)
                self.assertTrue(case[field].strip())
        self.assertEqual(sorted(ids), list(range(1, 31)))

    def test_no_runtime_executables_or_symlinks(self) -> None:
        self.assertTrue(BUNDLE.is_dir())
        self.assertFalse((BUNDLE / "scripts").exists())
        forbidden_suffixes = {".py", ".sh", ".js", ".ts", ".exe", ".so"}
        for path in BUNDLE.rglob("*"):
            with self.subTest(path=path.relative_to(BUNDLE)):
                self.assertFalse(path.is_symlink())
                if path.is_file():
                    self.assertNotIn(path.suffix, forbidden_suffixes)

    def test_relative_resources_resolve_in_a_clean_copy(self) -> None:
        self.assertTrue(BUNDLE.is_dir())
        with tempfile.TemporaryDirectory() as temporary:
            copied = Path(temporary) / "multi-agent-research"
            shutil.copytree(BUNDLE, copied)
            for document in copied.rglob("*.md"):
                for raw in LINK.findall(document.read_text(encoding="utf-8")):
                    target = raw.strip().strip("<>")
                    parsed = urlsplit(target)
                    if parsed.scheme or not parsed.path:
                        continue
                    # Agent Skills local resource paths are relative to the root.
                    destination = (copied / unquote(parsed.path)).resolve()
                    with self.subTest(document=document.name, target=target):
                        self.assertTrue(destination.is_relative_to(copied.resolve()))
                        self.assertTrue(destination.exists())
            self.assertTrue((copied / "evals" / "evals.json").is_file())
            self.assertTrue((copied / "agents" / "openai.yaml").is_file())


if __name__ == "__main__":
    unittest.main()
