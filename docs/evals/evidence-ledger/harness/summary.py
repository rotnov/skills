#!/usr/bin/env python3
"""Print the aggregate tables quoted in results.md from the run records."""

from __future__ import annotations

import collections
import json
import sys
from pathlib import Path

RUNS = Path(__file__).resolve().parents[1] / "runs"
HELDOUT = ["heldout.jsonl", "heldout-long.jsonl", "heldout-shipped.jsonl"]
DEV_STRONG = ["dev-baseline.jsonl", "dev-long-baseline.jsonl", "dev-quiet-baseline.jsonl"]
DEV_SMALL = ["dev-baseline-haiku.jsonl", "dev-weak-probe.jsonl"]
SKILL_NAMES = {"skillfixed": "skill", "merge": "skill"}


def records(files: list[str]):
    for name in files:
        for line in (RUNS / name).read_text(encoding="utf-8").splitlines():
            record = json.loads(line)
            if "verdict" in record:
                record.setdefault("family", "short")
                yield record


def table(title: str, files: list[str], key, keep=lambda record: True) -> None:
    rows = collections.defaultdict(lambda: collections.Counter())
    for record in records(files):
        if not keep(record):
            continue
        row = rows[key(record)]
        kind = "control" if record["class"] == "control" else "trap"
        row[f"{kind}_pass"] += record["verdict"]["pass"]
        row[f"{kind}_n"] += 1
        row["n"] += 1
        for field in ("n_calls", "tokens_in", "tokens_out", "cost_usd", "model_seconds"):
            row[field] += record[field]
    print(f"\n{title}")
    print("| condition | traps | controls | calls | tokens in | tokens out | cost USD | seconds |")
    print("| --- | --- | --- | --- | --- | --- | --- | --- |")
    for name, row in sorted(rows.items()):
        n = row["n"]
        print(
            f"| {name} | {row['trap_pass']}/{row['trap_n']} "
            f"| {row['control_pass']}/{row['control_n']} | {row['n_calls'] / n:.0f} "
            f"| {row['tokens_in'] / n:.0f} | {row['tokens_out'] / n:.0f} "
            f"| {row['cost_usd'] / n:.3f} | {row['model_seconds'] / n:.0f} |"
        )


def per_control() -> None:
    cells = collections.defaultdict(lambda: [0, 0])
    files = DEV_STRONG + ["dev-skill-v3.jsonl"] + HELDOUT
    for record in records(files):
        if record["class"] != "control":
            continue
        condition = SKILL_NAMES.get(record["condition"], record["condition"])
        cell = cells[(record["scenario"], condition)]
        cell[0] += record["verdict"]["pass"]
        cell[1] += 1
    print("\nControls by scenario, strong model, all families and lane caps")
    print("| control | set | single | flat | skill panel | skill alone |")
    print("| --- | --- | --- | --- | --- | --- |")
    for name, part in (
        ("ct1", "dev"), ("ct2", "dev"), ("qc2", "dev"),
        ("ct3", "held-out"), ("ct4", "held-out"), ("qc1", "held-out"),
    ):
        row = " | ".join(
            "{}/{}".format(*cells[(name, condition)])
            for condition in ("single", "flat", "skill", "solo")
        )
        print(f"| {name} | {part} | {row} |")


def main() -> int:
    not_weak = lambda record: record["family"] != "weak"  # noqa: E731
    label = lambda record: f"{record['condition']}:{record['label']}"  # noqa: E731
    table("Held-out, short + long + quiet", HELDOUT, label, not_weak)
    table("Weak-target probe", HELDOUT, label, lambda record: record["family"] == "weak")
    table(
        "Dev baseline probe, strong model",
        DEV_STRONG,
        lambda r: f"{r['family']} {r['condition']} @{r['lane_words']}",
    )
    table(
        "Dev baseline probe, small model",
        DEV_SMALL,
        lambda r: f"{r['family']} {r['condition']}:{r['label']} @{r['lane_words']}",
    )
    table(
        "Dev, skill drafts",
        ["dev-skill-v1.jsonl", "dev-skill-v2.jsonl", "dev-skill-v3.jsonl"],
        lambda r: f"{r['family']} {r['condition']}:{r['label']} @{r['lane_words']}",
    )
    table(
        "Dev subset, ablations",
        ["dev-ablations.jsonl", "dev-ablations-merge.jsonl"],
        label,
    )
    per_control()
    return 0


if __name__ == "__main__":
    sys.exit(main())
