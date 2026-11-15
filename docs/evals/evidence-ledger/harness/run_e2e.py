#!/usr/bin/env python3
"""End-to-end portability check: the installed skill in a real host session.

For each run a temporary project is created with the skill copied to the
places the skills CLI installs it (`.claude/skills/` and `.agents/skills/`)
and the scenario's documents as files under `evidence/`. The host (Claude
Code or Codex, headless) gets the question and is asked to use the skill. The
host decides everything else itself: whether and how to dispatch subagents,
what to put in briefs, how to merge. Records are appended in the same shape
as `run_eval.py` so the same judge and report apply.
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import run_eval  # noqa: E402

SKILL_NAME = "evidence-ledger"


def prompt_for(scenario: dict, mode: str, with_skill: bool) -> str:
    split = "; ".join(
        f"subagent {index} reads only " + ", ".join(f"evidence/{doc}.txt" for doc in lane)
        for index, lane in enumerate(scenario["slices"], 1)
    )
    helpers = {
        "agents": "Use three subagents.",
        "solo": "Work alone: do not start subagents.",
        # The harness's slices, in a real host: each lane sees part of the evidence.
        "sliced": (
            f"Use three subagents and split the reading between them: {split}. "
            "Do not read the evidence files yourself; work from their reports."
        ),
    }[mode]
    skill = f"Use the {SKILL_NAME} skill. " if with_skill else ""
    return (
        f"{scenario['question']}\nContext: {scenario['setup']}\n"
        "Everything we have collected is in the ./evidence folder, one file "
        f"per source. {skill}{helpers} Do not use the web. Finish with the "
        "decision memo for the person who asked: what to do, and how sure we "
        f"are, at most {run_eval.MEMO_WORDS} words, after a line containing "
        f"only {run_eval.MEMO_MARK}"
    )


def prepare(root: Path, scenario: dict, skill_dir: Path, with_skill: bool) -> None:
    evidence = root / "evidence"
    evidence.mkdir()
    for doc_id, doc in scenario["docs"].items():
        (evidence / f"{doc_id}.txt").write_text(
            f"{doc['title']}\n\n{doc['text']}\n", encoding="utf-8"
        )
    if with_skill:
        for base in (".claude/skills", ".agents/skills"):
            shutil.copytree(skill_dir, root / base / SKILL_NAME)


def run_claude(root: Path, prompt: str, mode: str) -> dict:
    command = [
        "claude", "-p", "--output-format", "json", "--setting-sources", "project",
        "--strict-mcp-config", "--no-session-persistence",
        "--allowedTools", "Read,Glob,Grep,Skill,Agent,Task",
    ]
    if mode == "solo":
        command += ["--disallowedTools", "Agent,Task"]
    done = subprocess.run(
        command, input=prompt, capture_output=True, text=True, cwd=root,
        timeout=1500, check=False,
    )
    data = json.loads(done.stdout)
    usage = data.get("usage", {})
    models = data.get("modelUsage", {})
    return {
        "text": data.get("result") or "",
        "subagents": data.get("subagent_stats", {}).get("spawned"),
        "turns": data.get("num_turns"),
        "tokens_in": sum(
            m.get("inputTokens", 0) + m.get("cacheReadInputTokens", 0)
            + m.get("cacheCreationInputTokens", 0) for m in models.values()
        ) or usage.get("input_tokens", 0),
        "tokens_out": sum(m.get("outputTokens", 0) for m in models.values()),
        "cost_usd": data.get("total_cost_usd", 0.0),
    }


def run_codex(root: Path, prompt: str, mode: str) -> dict:
    last = root / "last-message.txt"
    done = subprocess.run(
        ["codex", "exec", "--skip-git-repo-check", "-s", "read-only", "--json",
         "-o", str(last), "-"],
        input=prompt, capture_output=True, text=True, cwd=root, timeout=1500,
        check=False,
    )
    events = [line for line in done.stdout.splitlines() if line.startswith("{")]
    tokens_in = tokens_out = 0
    item_types: dict[str, int] = {}
    for line in events:
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        usage = event.get("usage") or {}
        item = event.get("item") or {}
        if event.get("type") == "item.completed" and item.get("type"):
            item_types[item["type"]] = item_types.get(item["type"], 0) + 1
        tokens_in += usage.get("input_tokens", 0)
        tokens_out += usage.get("output_tokens", 0)
    return {
        "text": last.read_text(encoding="utf-8") if last.exists() else "",
        # Codex reports subagent work as collaboration items, when it has any.
        "subagents": sum(
            count for kind, count in item_types.items()
            if "collab" in kind or "agent" in kind and kind != "agent_message"
        ),
        "item_types": item_types,
        "turns": len(events),
        "tokens_in": tokens_in,
        "tokens_out": tokens_out,
        "cost_usd": 0.0,
        "mentions_skill": SKILL_NAME in done.stdout,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", choices=("claude", "codex"), required=True)
    parser.add_argument("--mode", choices=("agents", "solo", "sliced"), required=True)
    parser.add_argument("--no-skill", action="store_true")
    parser.add_argument("--family", choices=tuple(run_eval.FAMILY), default="long")
    parser.add_argument("--scenarios", required=True)
    parser.add_argument("--runs", type=int, default=1)
    parser.add_argument("--out", required=True)
    parser.add_argument("--skill-dir", type=Path, default=run_eval.EVAL_DIR.parents[2] / "skills" / SKILL_NAME)
    options = parser.parse_args()
    run_eval.ACTIVE_FAMILY = options.family
    scenarios = run_eval.load_all()
    runner = {"claude": run_claude, "codex": run_codex}[options.host]
    out = Path(options.out)
    for scenario_id in options.scenarios.split(","):
        scenario = scenarios[scenario_id]
        for run_index in range(1, options.runs + 1):
            started = time.time()
            with tempfile.TemporaryDirectory() as name:
                root = Path(name).resolve()
                prepare(root, scenario, options.skill_dir, not options.no_skill)
                try:
                    result = runner(root, prompt_for(scenario, options.mode, not options.no_skill), options.mode)
                except Exception as error:  # noqa: BLE001 - recorded, never hidden
                    result = {"text": "", "error": str(error)[:300]}
            record = {
                "scenario": scenario_id,
                "class": scenario["class"],
                "condition": f"e2e-{options.host}-{options.mode}",
                "label": "no-skill" if options.no_skill else "",
                "run": run_index,
                "lane_words": 0,
                "family": options.family,
                "n_calls": (result.get("subagents") or 0) + 1,
                "tokens_in": result.get("tokens_in", 0),
                "tokens_out": result.get("tokens_out", 0),
                "cost_usd": round(result.get("cost_usd", 0.0), 4),
                "model_seconds": round(time.time() - started, 1),
                "host": {k: v for k, v in result.items() if k != "text"},
                "full_text": result["text"],
            }
            if run_eval.MEMO_MARK in result["text"]:
                record["memo"] = run_eval.memo_of(result["text"])
            else:
                record["error"] = result.get("error", "no memo marker in output")
            with out.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(record, ensure_ascii=False) + "\n")
            print(scenario_id, record["condition"], run_index, "memo" in record, result.get("subagents"), flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
