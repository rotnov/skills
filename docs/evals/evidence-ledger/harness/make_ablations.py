#!/usr/bin/env python3
"""Derive ablation copies of a skill directory by exact text replacement.

Each variant is a list of (file, old, new) edits applied to the source skill.
An edit whose `old` text is missing fails loudly, so a variant can never
silently equal the full skill. Copies are written as .txt files under
`ablations/<variant>/` so that no tool mistakes them for publishable skills.

The variants in this file were derived from the second draft of the skill,
frozen as `ablations/v2/`:

    make_ablations.py ../ablations/v2 noplan lean nopool nodecide core

Usage: make_ablations.py <source skill dir> <variant> [<variant> ...]
"""

from __future__ import annotations

import sys
from pathlib import Path

OUT = Path(__file__).resolve().parents[1] / "ablations"

# Remove the rivals written before reading and the per-lane assigned rival.
# Every lane then gets the same template brief, so no planning call is needed.
NO_PLAN = [
    (
        "SKILL.md",
        """### 1. Frame before reading

Write the decision, the target, and two to four rivals. One rival must reject
the question's own framing ("the premise is wrong", "neither option", "the
effect is not real").
""",
        """### 1. Frame

Write the decision and the target.
""",
    ),
    (
        "SKILL.md",
        "4. **Test each rival against every observation**:",
        "4. **List the rival answers the pooled facts allow**, including one that\n"
        "   rejects the question's own framing, and test each against every\n"
        "   observation:",
    ),
    (
        "references/lane-brief.md",
        "Send every lane the same brief; change only the assigned rival. Do not put",
        "Send every lane the same brief. Do not put",
    ),
    (
        "references/lane-brief.md",
        """Rivals: A) <...> B) <...> C) <...>
Test rival <X> hardest, and report every fact that bears on any rival,
including facts that hurt <X>.
""",
        "Report every fact that bears on the question, for or against.\n",
    ),
]

# On top of NO_PLAN: drop the four-rung vocabulary and the ledger reference.
# What remains is "say what was observed and where", in plain words.
LEAN = NO_PLAN + [
    (
        "SKILL.md",
        """- **Rung**: how a fact is known, lowest to highest: `told` (someone says so),
  `documented` (a primary source states it), `observed-elsewhere` (measured
  or run on a setup that is not the target), `observed-on-target` (measured
  or run on the target, or on a representative part of it).
""",
        "",
    ),
    (
        "SKILL.md",
        """Build one ledger from the lanes' claim lines. Read
[references/ledger.md](references/ledger.md) when an origin or a rung is
unclear or lanes disagree.
""",
        "Build one ledger from the lanes' claim lines.\n",
    ),
    (
        "SKILL.md",
        """3. **Re-rate every rung against the target.** "Verified", "confirmed" and
   "reproduced" mean only the rung the claim line supports. A difference
   between where a fact was observed and the target lowers the rung only
   when you can name how it touches the mechanism; one unit of several like
   units, or a sample of the real load, is the target.
""",
        """3. **Check each result against the target.** "Verified", "confirmed" and
   "reproduced" mean only what was actually observed, and where. A result
   from another setup counts for the target unless you can name a difference
   that touches the mechanism; one unit of several like units, or a sample
   of the real load, is the target.
""",
    ),
    (
        "SKILL.md",
        "- **Commit** when the crux is `observed-on-target` and no rival is alive.",
        "- **Commit** when the crux was observed on the target and no rival is alive.",
    ),
    (
        "SKILL.md",
        "- **Hold** when the crux is lower, or a live rival would change the action,",
        "- **Hold** when the crux was not observed on the target, or a live rival\n"
        "  would change the action,",
    ),
    (
        "SKILL.md",
        "Rests on: <crux claim> - <rung>, <n> independent origin(s)",
        "Rests on: <crux claim> - <how it is known, and where it was observed>,\n"
        "<n> independent origin(s)",
    ),
    (
        "SKILL.md",
        """Use the rung words exactly. Write "confirmed" or "verified" only for
`observed-on-target`.
""",
        """Write "confirmed" or "verified" only for what was observed on the target.
""",
    ),
    ("references/ledger.md", None, None),
]

POOL_RULE = """1. **Pool first.** A lane saw part of the evidence. What one lane lacks,
   another may hold: join them before judging anything. A gap or doubt a lane
   mentions is closed when another lane's claim fills it.
"""
ORIGIN_RULE = """2. **Count origins, not voices.** Group claims by origin. Sources, people and
   lanes repeating one origin are one line of evidence. A lane agreeing with
   another lane is not evidence.
"""
TARGET_RULE = """3. **Check each result against the target.** "Verified", "confirmed" and
   "reproduced" mean only what was actually observed, and where. A result
   from another setup counts for the target unless you can name a difference
   that touches the mechanism; one unit of several like units, or a sample
   of the real load, is the target.
"""
RIVAL_RULE = """4. **List the rival answers the pooled facts allow**, including one that
   rejects the question's own framing, and test each against every
   observation: when the effect started,
   where it appeared, when it stopped, on what setup. A rival is dead when a
   named observation contradicts it, and alive only while some observation
   fits it better than the leading one. Lack of proof against a rival does
   not keep it alive; fewer lanes backing it does not kill it.
"""
DECIDE_RULES = """The crux is the claim the decision turns on. Decide for the action, not for
certainty: a doubt matters only if settling it could change what to do.

- **Commit** when the crux was observed on the target and no rival is alive.
  Say so plainly. Do not add conditions, pre-checks or caveats; routine
  monitoring is not a condition.
- **Hold** when the crux was not observed on the target, or a live rival
  would change the action,
  and the action is costly to undo. Name the one cheapest check that would
  settle it, and the action that is safe meanwhile.
- **Split** when the evidence covers part of the target: go where it is
  observed, hold where it is not.
"""
REPORT_TAIL = """Not verified: <what would change the decision and nobody observed on the
target, or "nothing that would change the decision">
Next check: <the one cheapest step, or "none needed">
"""

# Coordinator-rule ablations, each on top of LEAN.
NO_POOL = LEAN + [("SKILL.md", POOL_RULE, "")]
NO_DECIDE = LEAN + [
    ("SKILL.md", DECIDE_RULES, "Find the crux, the claim the decision turns on, and decide.\n"),
    (
        "SKILL.md",
        REPORT_TAIL,
        "Not verified: <what nobody observed on the target>\n"
        "Next check: <the cheapest step that would settle it>\n",
    ),
]
# Only pooling and the decision rule: no origin count, target check or rivals.
CORE = LEAN + [
    ("SKILL.md", ORIGIN_RULE, ""),
    ("SKILL.md", TARGET_RULE, ""),
    ("SKILL.md", RIVAL_RULE, ""),
    (
        "SKILL.md",
        "- **Commit** when the crux was observed on the target and no rival is alive.",
        "- **Commit** when the pooled facts settle the question for the asker's own\n"
        "  setup.",
    ),
    (
        "SKILL.md",
        """- **Hold** when the crux was not observed on the target, or a live rival
  would change the action,
  and the action is costly to undo.""",
        "- **Hold** when they do not, and the action is costly to undo.",
    ),
    (
        "SKILL.md",
        "Rests on: <crux claim> - <how it is known, and where it was observed>,\n"
        "<n> independent origin(s)\n"
        "Ruled out: <rival> - <the observation that kills it>\n",
        "Rests on: <crux claim>\n",
    ),
]

VARIANTS = {
    "noplan": NO_PLAN,
    "lean": LEAN,
    "nopool": NO_POOL,
    "nodecide": NO_DECIDE,
    "core": CORE,
}


def build(source: Path, name: str) -> None:
    # The source may be a skill directory (.md) or a frozen copy (.txt).
    paths = [
        path
        for path in [*source.glob("SKILL.*"), *sorted((source / "references").iterdir())]
        if path.suffix in (".md", ".txt")
    ]
    texts = {
        str(path.relative_to(source).with_suffix(".md")): path.read_text(encoding="utf-8")
        for path in paths
    }
    for relative, old, new in VARIANTS[name]:
        if old is None:
            del texts[relative]
            continue
        if texts[relative].count(old) != 1:
            raise SystemExit(f"{name}: edit does not apply exactly once in {relative}")
        texts[relative] = texts[relative].replace(old, new)
    for relative, text in texts.items():
        target = (OUT / name / relative).with_suffix(".txt")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")
    print("built", name)


def main() -> int:
    source = Path(sys.argv[1])
    for name in sys.argv[2:]:
        build(source, name)
    return 0


if __name__ == "__main__":
    sys.exit(main())
