#!/usr/bin/env python3
"""Derive ablation copies of a skill directory by exact text replacement.

Each variant is a list of (file, old, new) edits applied to the source skill.
An edit whose `old` text is missing fails loudly, so a variant can never
silently equal the full skill. Copies are written as .txt files under
`ablations/<variant>/` so that no tool mistakes them for publishable skills.

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

VARIANTS = {"noplan": NO_PLAN, "lean": LEAN}


def build(source: Path, name: str) -> None:
    texts = {
        str(path.relative_to(source)): path.read_text(encoding="utf-8")
        for path in [source / "SKILL.md", *sorted((source / "references").glob("*.md"))]
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
