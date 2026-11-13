from __future__ import annotations

import hang_guard  # noqa: F401
import importlib.util
import json
import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SKILL_DIR = ROOT / "skills" / "evidence-ledger"
SKILL = SKILL_DIR / "SKILL.md"
EVAL_DIR = ROOT / "docs" / "evals" / "evidence-ledger"
LINK_RE = re.compile(r"\]\((references/[^)#]+)\)")
FORBIDDEN_RE = re.compile(r"/Users/|/home/[a-z]|[A-Z]:\\Users\\", re.I)
# Invented names from the evaluation packets. None may leak into the skill:
# a skill that names its own test cases is tuned to them.
SCENARIO_NAMES = (
    "Brokk", "Tern", "Schedulo", "Kestrel", "Lumen", "Jobber", "PgBouncer",
    "Vela", "Corvid", "Strand", "Vesta", "Quill", "Petrel", "lazy_vacuum",
    "HTTP/3", "advisory lock", "consent banner", "NAT",
)
RUNGS = ("told", "documented", "observed-elsewhere", "observed-on-target")


def load_harness():
    spec = importlib.util.spec_from_file_location(
        "evidence_ledger_run_eval", EVAL_DIR / "harness" / "run_eval.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def skill_texts() -> dict[Path, str]:
    return {
        path: path.read_text(encoding="utf-8")
        for path in SKILL_DIR.rglob("*")
        if path.is_file()
    }


class EvidenceLedgerSkillTests(unittest.TestCase):
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

    def test_skill_is_portable_and_small(self) -> None:
        self.assertFalse((SKILL_DIR / "agents").exists())
        for path, text in skill_texts().items():
            self.assertIsNone(FORBIDDEN_RE.search(text), path)
        skill = SKILL.read_text(encoding="utf-8")
        self.assertLessEqual(len(skill.splitlines()), 120)
        description = skill.split("---")[1]
        self.assertLessEqual(len(" ".join(description.split())), 320)
        # Instruction-only and client-neutral: no tool or client names.
        for word in ("Claude", "Codex", "Task tool", "Agent tool", "spawn_agent"):
            self.assertNotIn(word, skill)

    def test_core_rules_are_stated(self) -> None:
        skill = " ".join(SKILL.read_text(encoding="utf-8").split())
        for phrase in (
            "Count origins, not voices",
            "A lane agreeing with another lane is not evidence",
            "Re-rate every rung against the target",
            "commit plainly",
            "With no subagents",
            "Skip it for",
        ):
            self.assertIn(phrase, skill)
        for rung in RUNGS:
            self.assertIn(f"`{rung}`", skill)

    def test_lane_report_carries_origin_rung_and_setup(self) -> None:
        brief = (SKILL_DIR / "references" / "lane-brief.md").read_text(encoding="utf-8")
        for field in ("origin:", "rung:", "setup:"):
            self.assertIn(field, brief)
        self.assertIn("Do not give a recommendation", brief)
        for rung in RUNGS:
            self.assertIn(rung, brief)

    def test_skill_does_not_name_evaluation_scenarios(self) -> None:
        for relative in ("SKILL.md", "references/lane-brief.md", "references/ledger.md"):
            text = (SKILL_DIR / relative).read_text(encoding="utf-8")
            for name in SCENARIO_NAMES:
                self.assertNotIn(name, text, f"{relative} mentions {name}")

    def test_evals_cover_required_paths(self) -> None:
        data = json.loads((SKILL_DIR / "evals" / "evals.json").read_text(encoding="utf-8"))
        self.assertEqual(data["skill_name"], "evidence-ledger")
        evals = data["evals"]
        self.assertEqual([item["id"] for item in evals], list(range(1, len(evals) + 1)))
        for item in evals:
            self.assertTrue(item["prompt"].strip())
            self.assertTrue(item["expected_output"].strip())
        outputs = " ".join(item["expected_output"] for item in evals)
        for phrase in ("one origin", "observed-elsewhere", "commits plainly", "alone", "Does not trigger"):
            self.assertIn(phrase, outputs)


class EvidenceLedgerEvaluationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.harness = load_harness()
        cls.scenarios = cls.harness.load_all()

    def test_sixteen_scenarios_in_four_classes(self) -> None:
        classes: dict[str, int] = {}
        for scenario in self.scenarios.values():
            classes[scenario["class"]] = classes.get(scenario["class"], 0) + 1
        self.assertEqual(
            classes,
            {"shared-origin": 4, "evidence-level": 4, "collapse": 4, "control": 4},
        )

    def test_every_scenario_has_gradable_checks(self) -> None:
        for name, scenario in self.scenarios.items():
            ids = [check["id"] for check in scenario["checks"]]
            self.assertTrue(any(i.startswith("decision") for i in ids), name)
            self.assertIn("hedge" if scenario["class"] == "control" else "aware", ids, name)
            for check in scenario["checks"]:
                self.assertIn(check["expected"], ("yes", "no"), name)
            self.assertTrue(scenario["truth"], name)

    def test_split_is_the_seeded_stratified_one(self) -> None:
        stored = json.loads((EVAL_DIR / "split.json").read_text(encoding="utf-8"))
        self.assertEqual(stored, self.harness.make_split())
        self.assertFalse(set(stored["dev"]) & set(stored["heldout"]))
        for part in ("dev", "heldout"):
            counts: dict[str, int] = {}
            for name in stored[part]:
                key = self.scenarios[name]["class"]
                counts[key] = counts.get(key, 0) + 1
            self.assertEqual(set(counts.values()), {2}, part)

    def test_long_family_keeps_headers_and_original_text(self) -> None:
        long_dir = EVAL_DIR / "scenarios-long"
        for name, scenario in self.scenarios.items():
            long = self.harness.load_scenario(long_dir / f"{name}.txt")
            for key in ("question", "setup", "slices", "truth", "checks"):
                self.assertEqual(long[key], scenario[key], f"{name}: {key}")
            for doc_id, doc in scenario["docs"].items():
                self.assertEqual(long["docs"][doc_id]["text"].count(doc["text"]), 1, f"{name} {doc_id}")

    def test_pass_ignores_the_awareness_check(self) -> None:
        self.assertEqual(self.harness.PASS_PREFIXES, ("decision", "hedge"))


if __name__ == "__main__":
    unittest.main()
