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
    "HTTP/3", "advisory lock", "consent banner", "NAT", "Salvo", "Orrin",
    "Marlow", "Halden", "Ostra", "Pell", "compaction", "quantis", "first name",
)

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
    def test_skill_is_one_file_with_no_references(self) -> None:
        # The lane brief was the last reference; it changed no decision and
        # its effect on cost was inconsistent, so it was cut.
        self.assertFalse((SKILL_DIR / "references").exists())
        self.assertEqual(LINK_RE.findall(SKILL.read_text(encoding="utf-8")), [])
        self.assertEqual(
            sorted(path.name for path in SKILL_DIR.iterdir()),
            ["README.md", "SKILL.md", "evals"],
        )

    def test_skill_is_portable_and_small(self) -> None:
        self.assertFalse((SKILL_DIR / "agents").exists())
        for path, text in skill_texts().items():
            self.assertIsNone(FORBIDDEN_RE.search(text), path)
        skill = SKILL.read_text(encoding="utf-8")
        # The ablations showed one rule carries the effect; keep the skill small.
        self.assertLessEqual(len(skill.splitlines()), 80)
        description = skill.split("---")[1]
        self.assertLessEqual(len(" ".join(description.split())), 320)
        # Instruction-only and client-neutral: no tool or client names.
        for word in ("Claude", "Codex", "Task tool", "Agent tool", "spawn_agent"):
            self.assertNotIn(word, skill)

    def test_decision_rule_is_stated(self) -> None:
        skill = " ".join(SKILL.read_text(encoding="utf-8").split())
        for phrase in (
            "Take facts from the lanes, not conclusions",
            "Decide for the action, not for certainty",
            "observed on the asker's own setup",
            "however many others repeat it",
            "no other answer fits the observations better",
            "**Commit** when the crux is settled",
            "Do not add conditions, pre-checks or caveats",
            "**Hold** when it is not, and the action is costly to undo",
            "**Split** when the evidence covers part",
            "prefer that to a panel",
            "## Without subagents",
        ):
            self.assertIn(phrase, skill)
        for line in ("Decision:", "Rests on:", "Not verified:", "Next check:"):
            self.assertIn(line, skill)

    def test_parts_cut_by_ablation_stay_cut(self) -> None:
        skill = SKILL.read_text(encoding="utf-8")
        # No planning turn, no pre-framed rivals, no rung ladder, no report
        # format for lanes: none of them changed a decision in the ablations.
        for word in ("Rival", "rival", "Rung", "rung", "observed-elsewhere", "claim line"):
            self.assertNotIn(word, skill)

    def test_skill_does_not_name_evaluation_scenarios(self) -> None:
        for relative in ("SKILL.md",):
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
        for phrase in ("commits plainly", "Holds", "splits the decision", "Skips the lanes", "Does not trigger"):
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

    def test_quiet_family_has_its_own_seeded_split(self) -> None:
        self.harness.ACTIVE_FAMILY = "quiet"
        try:
            stored = json.loads((EVAL_DIR / "split-quiet.json").read_text(encoding="utf-8"))
            self.assertEqual(stored, self.harness.make_split())
            scenarios = self.harness.load_all()
        finally:
            self.harness.ACTIVE_FAMILY = "short"
        self.assertEqual(len(scenarios), 6)
        for part in ("dev", "heldout"):
            classes = sorted(scenarios[name]["class"] for name in stored[part])
            self.assertEqual(classes, ["control", "scope-join", "scope-join"], part)

    def test_ablation_copies_rebuild_from_the_frozen_draft(self) -> None:
        spec = importlib.util.spec_from_file_location(
            "evidence_ledger_make_ablations", EVAL_DIR / "harness" / "make_ablations.py"
        )
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        source = EVAL_DIR / "ablations" / "v2"
        self.assertTrue((EVAL_DIR / "ablations" / "v3" / "SKILL.txt").is_file())
        for name, edits in module.VARIANTS.items():
            texts = {
                str(path.relative_to(source).with_suffix(".md")): path.read_text(encoding="utf-8")
                for path in source.rglob("*.txt")
            }
            for relative, old, new in edits:
                if old is None:
                    del texts[relative]
                    continue
                self.assertEqual(texts[relative].count(old), 1, f"{name}: {relative}")
                texts[relative] = texts[relative].replace(old, new)
            for relative, text in texts.items():
                stored = (EVAL_DIR / "ablations" / name / relative).with_suffix(".txt")
                self.assertEqual(stored.read_text(encoding="utf-8"), text, f"{name}: {relative}")

    def test_fixed_brief_fills_the_frozen_v3_template(self) -> None:
        scenario = self.scenarios["so1"]
        brief = self.harness.fixed_brief(EVAL_DIR / "ablations" / "v3", scenario)
        self.assertIn(scenario["question"], brief)
        self.assertIn(scenario["setup"], brief)
        self.assertNotIn("<the question as asked>", brief)


if __name__ == "__main__":
    unittest.main()
