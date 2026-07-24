#!/usr/bin/env python3
"""Validate every published skill with agentskills.io's reference library."""

from __future__ import annotations

import sys
from pathlib import Path

from skills_ref import validate


ROOT = Path(__file__).resolve().parents[1]
SKILLS_ROOT = ROOT / "skills"


def main() -> int:
    skill_dirs = sorted(path for path in SKILLS_ROOT.iterdir() if path.is_dir())
    if not skill_dirs:
        print("error: no publishable skills found", file=sys.stderr)
        return 1

    failed = False
    for skill_dir in skill_dirs:
        problems = validate(skill_dir)
        if problems:
            failed = True
            for problem in problems:
                print(f"error: {skill_dir.name}: {problem}", file=sys.stderr)
        else:
            print(f"Valid skill: {skill_dir}")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
