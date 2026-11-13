#!/usr/bin/env python3
"""Print the end-to-end table quoted in results.md from the run records."""

from __future__ import annotations

import json
import sys
from pathlib import Path

RUNS = Path(__file__).resolve().parents[1] / "runs"
FILES = ["e2e-shipped.jsonl", "e2e-shipped-codex.jsonl", "e2e.jsonl"]
SLICED = ["e2e-sliced.jsonl", "e2e-sliced-codex.jsonl"]


def main() -> int:
    print("| file | host and mode | skill | scenario | pass | subagents | tokens in | seconds |")
    print("| --- | --- | --- | --- | --- | --- | --- | --- |")
    for name in FILES:
        for line in (RUNS / name).read_text(encoding="utf-8").splitlines():
            record = json.loads(line)
            host = record["host"]
            if "item_types" in host:
                # Codex does not report spawns as items. Each thread start
                # repeats two configuration warnings as `error` items, once
                # for the main thread, so the extra ones count subagents.
                subagents = max(0, host["item_types"].get("error", 0) - 2)
            else:
                subagents = host.get("subagents")
            verdict = record.get("verdict", {}).get("pass")
            print(
                f"| {name} | {record['condition'][4:]} "
                f"| {'no' if record['label'] else 'yes'} | {record['scenario']} "
                f"| {verdict} | {subagents} | {record['tokens_in']} "
                f"| {record['model_seconds']:.0f} |"
            )
    print("\nSplit reading, by host and class")
    print("| host | skill | controls passed | trap passed | subagents | tokens in | seconds |")
    print("| --- | --- | --- | --- | --- | --- | --- |")
    rows: dict[tuple, dict] = {}
    for name in SLICED:
        for line in (RUNS / name).read_text(encoding="utf-8").splitlines():
            record = json.loads(line)
            key = (record["condition"].split("-")[1], "no" if record["label"] else "yes")
            row = rows.setdefault(
                key, {"control": [0, 0], "trap": [0, 0], "tokens": 0, "seconds": 0, "n": 0, "sub": set()}
            )
            kind = "control" if record["class"] == "control" else "trap"
            row[kind][0] += bool(record.get("verdict", {}).get("pass"))
            row[kind][1] += 1
            row["tokens"] += record["tokens_in"]
            row["seconds"] += record["model_seconds"]
            row["n"] += 1
            host = record["host"]
            row["sub"].add(
                max(0, host["item_types"].get("error", 0) - 2)
                if "item_types" in host
                else host.get("subagents")
            )
    for (host, skill), row in sorted(rows.items()):
        print(
            f"| {host} | {skill} | {row['control'][0]}/{row['control'][1]} "
            f"| {row['trap'][0]}/{row['trap'][1]} | {sorted(row['sub'])} "
            f"| {row['tokens'] / row['n']:.0f} | {row['seconds'] / row['n']:.0f} |"
        )
    print("\nSplit reading, controls by scenario (passed of runs)")
    cells: dict[tuple, list[int]] = {}
    for name in SLICED:
        for line in (RUNS / name).read_text(encoding="utf-8").splitlines():
            record = json.loads(line)
            key = (record["scenario"], record["condition"].split("-")[1], "no" if record["label"] else "yes")
            cell = cells.setdefault(key, [0, 0])
            cell[0] += bool(record.get("verdict", {}).get("pass"))
            cell[1] += 1
    for key, cell in sorted(cells.items()):
        print(key, f"{cell[0]}/{cell[1]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
