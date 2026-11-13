#!/usr/bin/env python3
"""Re-grade a random sample of graded records with a second model family.

Uses `codex exec` with the same judge prompt and reports agreement with the
stored verdicts on the pass/fail outcome and on each check.
"""

from __future__ import annotations

import concurrent.futures
import json
import random
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import run_eval  # noqa: E402


def codex(prompt: str) -> str:
    with tempfile.TemporaryDirectory() as cwd:
        last = Path(cwd) / "last.txt"
        subprocess.run(
            ["codex", "exec", "--skip-git-repo-check", "-s", "read-only", "-o", str(last), "-"],
            input=prompt, capture_output=True, text=True, cwd=cwd, timeout=600, check=False,
        )
        return last.read_text(encoding="utf-8")


def main() -> int:
    sample_size, seed, out, *files = sys.argv[1:]
    records = []
    for name in files:
        for line in Path(name).read_text(encoding="utf-8").splitlines():
            record = json.loads(line)
            if "verdict" in record:
                records.append(record)
    random.Random(int(seed)).shuffle(records)
    records = records[: int(sample_size)]
    scenarios = run_eval.load_all()

    def regrade(record: dict) -> dict:
        scenario = scenarios[record["scenario"]]
        return run_eval.verdict_from(codex(run_eval.judge_prompt(record, scenario)), scenario)

    with concurrent.futures.ThreadPoolExecutor(4) as pool:
        second = list(pool.map(regrade, records))
    same_pass = sum(1 for r, s in zip(records, second) if r["verdict"]["pass"] == s["pass"])
    checks = [(r["verdict"]["checks"][k], s["checks"][k]) for r, s in zip(records, second) for k in s["checks"]]
    same_checks = sum(1 for a, b in checks if a == b)
    rows = [
        {"scenario": r["scenario"], "condition": r["condition"], "label": r["label"],
         "run": r["run"], "family": r.get("family", "short"), "lane_words": r["lane_words"],
         "claude": r["verdict"], "codex": s}
        for r, s in zip(records, second)
    ]
    Path(out).write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")
    print(f"pass/fail agreement: {same_pass}/{len(records)}")
    print(f"per-check agreement: {same_checks}/{len(checks)}")
    for row in rows:
        if row["claude"]["checks"] != row["codex"]["checks"]:
            print("DISAGREE", row["scenario"], row["condition"], row["run"], row["claude"]["checks"], row["codex"]["checks"])
    return 0


if __name__ == "__main__":
    sys.exit(main())
