#!/usr/bin/env python3
"""Evaluation harness for the evidence-ledger skill.

Standard library only. Subject and judge calls go through the `claude` CLI in
print mode with no tools, no settings and a temporary working directory, so
nothing from the host session reaches the model under test.

Subcommands:
  split   write split.json (seeded, stratified by class)
  run     run conditions on scenarios and append records to a JSONL file
  judge   grade records with the fixed per-scenario checks
  report  aggregate graded records
"""

from __future__ import annotations

import argparse
import concurrent.futures
import json
import random
import re
import subprocess
import sys
import tempfile
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
EVAL_DIR = HERE.parent
SCENARIO_DIR = EVAL_DIR / "scenarios"
SPLIT_FILE = EVAL_DIR / "split.json"
SPLIT_SEED = 20261113
SYSTEM = "You are an experienced analyst helping a team make a decision."
MEMO_MARK = "=== MEMO ==="
MEMO_WORDS = 250
MEMO_ASK = (
    "Write the decision memo for the person who asked: what to do, and how "
    f"sure we are. At most {MEMO_WORDS} words. You may write working notes "
    f"first; put the final memo after a line containing only {MEMO_MARK}"
)
PASS_PREFIXES = ("decision", "hedge")


def load_scenario(path: Path) -> dict:
    head, *doc_parts = re.split(r"^== ", path.read_text(encoding="utf-8"), flags=re.M)
    scenario: dict = {"checks": [], "docs": {}}
    for line in head.splitlines():
        if not line.strip():
            continue
        key, value = line.split(":", 1)
        value = value.strip()
        if key.startswith("check "):
            _, check_id, expected = key.split()
            scenario["checks"].append(
                {"id": check_id, "expected": expected, "question": value}
            )
        elif key == "slices":
            scenario["slices"] = [part.split() for part in value.split("|")]
        else:
            scenario[key] = value
    for part in doc_parts:
        title_line, body = part.split("\n", 1)
        doc_id, title = title_line.split(":", 1)
        scenario["docs"][doc_id.strip()] = {"title": title.strip(), "text": body.strip()}
    used = {doc for lane in scenario["slices"] for doc in lane}
    if used != set(scenario["docs"]):
        raise ValueError(f"{path.name}: slices do not cover the documents exactly")
    if len(scenario["slices"]) != 3:
        raise ValueError(f"{path.name}: expected three slices")
    return scenario


FAMILY = {
    "short": SCENARIO_DIR,
    "long": EVAL_DIR / "scenarios-long",
    "quiet": EVAL_DIR / "scenarios-quiet",
    # Post-hoc probe of the skill's own risk (over-committing); it has no split.
    "weak": EVAL_DIR / "scenarios-weak",
}
SPLIT_FILES = {"short": SPLIT_FILE, "long": SPLIT_FILE, "quiet": EVAL_DIR / "split-quiet.json"}
ACTIVE_FAMILY = "short"


def load_all() -> dict[str, dict]:
    return {
        path.stem: load_scenario(path)
        for path in sorted(FAMILY[ACTIVE_FAMILY].glob("*.txt"))
    }


def make_split() -> dict[str, list[str]]:
    by_class: dict[str, list[str]] = {}
    for scenario_id, scenario in load_all().items():
        by_class.setdefault(scenario["class"], []).append(scenario_id)
    rng = random.Random(SPLIT_SEED)
    split: dict[str, list[str]] = {"dev": [], "heldout": []}
    for name in sorted(by_class):
        ids = sorted(by_class[name])
        rng.shuffle(ids)
        half = len(ids) // 2
        split["dev"] += sorted(ids[:half])
        split["heldout"] += sorted(ids[half:])
    return split


def call_model(prompt: str, model: str | None, attempts: int = 4) -> dict:
    command = [
        "claude", "-p", "--output-format", "json", "--tools", "",
        "--setting-sources", "", "--strict-mcp-config",
        "--no-session-persistence", "--system-prompt", SYSTEM,
    ]
    if model:
        command += ["--model", model]
    last = ""
    for attempt in range(attempts):
        with tempfile.TemporaryDirectory() as cwd:
            started = time.time()
            done = subprocess.run(
                command, input=prompt, capture_output=True, text=True,
                cwd=cwd, timeout=600, check=False,
            )
        try:
            data = json.loads(done.stdout)
            if not data.get("is_error") and data.get("result"):
                usage = data.get("usage", {})
                return {
                    "text": data["result"],
                    "tokens_in": usage.get("input_tokens", 0)
                    + usage.get("cache_creation_input_tokens", 0)
                    + usage.get("cache_read_input_tokens", 0),
                    "tokens_out": usage.get("output_tokens", 0),
                    "cost_usd": data.get("total_cost_usd", 0.0),
                    "seconds": round(time.time() - started, 1),
                }
            last = str(data.get("result"))[:300]
        except json.JSONDecodeError:
            last = (done.stdout or done.stderr)[:300]
        time.sleep(15 * (attempt + 1))
    raise RuntimeError(f"model call failed: {last}")


def task_block(scenario: dict) -> str:
    return f"QUESTION: {scenario['question']}\nCONTEXT: {scenario['setup']}"


def materials(scenario: dict, doc_ids: list[str]) -> str:
    return "\n\n".join(
        f"[{doc_id}] {scenario['docs'][doc_id]['title']}\n{scenario['docs'][doc_id]['text']}"
        for doc_id in sorted(doc_ids, key=lambda item: int(item[1:]))
    )


def skill_file(skill_dir: Path, relative: str) -> Path:
    """Ablation copies are stored as .txt so no tool mistakes them for skills."""
    path = skill_dir / relative
    return path if path.exists() else path.with_suffix(".txt")


def skill_text(skill_dir: Path) -> str:
    parts = [
        "--- SKILL.md ---\n"
        + skill_file(skill_dir, "SKILL.md").read_text(encoding="utf-8")
    ]
    references = skill_dir / "references"
    if references.is_dir():
        for path in sorted(references.iterdir()):
            parts.append(
                f"--- references/{path.stem}.md ---\n{path.read_text(encoding='utf-8')}"
            )
    return "\n\n".join(parts)


def fixed_brief(skill_dir: Path, scenario: dict) -> str:
    """The lane brief template with only the question and target filled in."""
    text = skill_file(skill_dir, "references/lane-brief.md").read_text(encoding="utf-8")
    template = re.search(r"```text\n(.*?)```", text, flags=re.S).group(1)
    if "Rival" in template:
        raise RuntimeError("skillfixed needs a lane brief without rivals")
    return template.replace("<the question as asked>", scenario["question"]).replace(
        "<the exact setup or population the decision applies to>", scenario["setup"]
    )


def memo_of(text: str) -> str:
    return text.split(MEMO_MARK)[-1].strip()


def run_lanes(prompts: list[str], model: str | None) -> list[dict]:
    with concurrent.futures.ThreadPoolExecutor(len(prompts)) as pool:
        results = list(pool.map(lambda prompt: call_model(prompt, model), prompts))
    return [
        {"role": f"lane{index}", **result} for index, result in enumerate(results, 1)
    ]


def cond_single(scenario: dict, options: argparse.Namespace) -> list[dict]:
    prompt = (
        f"{task_block(scenario)}\n\nMATERIALS:\n"
        f"{materials(scenario, list(scenario['docs']))}\n\n{MEMO_ASK}"
    )
    return [{"role": "final", **call_model(prompt, options.model)}]


def cond_solo(scenario: dict, options: argparse.Namespace) -> list[dict]:
    prompt = (
        "You have this skill loaded and the request below triggers it.\n\n"
        f"<skill>\n{skill_text(options.skill_dir)}\n</skill>\n\n"
        f"{task_block(scenario)}\n\nThis host has no subagents; you work alone.\n\n"
        f"MATERIALS:\n{materials(scenario, list(scenario['docs']))}\n\n"
        f"Follow the skill. {MEMO_ASK}"
    )
    return [{"role": "final", **call_model(prompt, options.model)}]


def flat_lanes(scenario: dict, options: argparse.Namespace) -> list[dict]:
    prompts = [
        "You are one of three analysts investigating the question below in "
        "parallel for a coordinator. You do not see the other analysts' "
        f"work.\n\n{task_block(scenario)}\n\nMATERIALS YOU FOUND:\n"
        f"{materials(scenario, lane)}\n\nReport your findings and your "
        f"recommendation to the coordinator. At most {options.lane_words} words."
        for lane in scenario["slices"]
    ]
    return run_lanes(prompts, options.lane_model)


def cond_flat(scenario: dict, options: argparse.Namespace) -> list[dict]:
    calls = flat_lanes(scenario, options)
    reports = "\n\n".join(
        f"REPORT FROM ANALYST {index}:\n{call['text']}"
        for index, call in enumerate(calls, 1)
    )
    prompt = (
        "You coordinate three analysts who investigated the question below in "
        "parallel. You have only their reports.\n\n"
        f"{task_block(scenario)}\n\n{reports}\n\n{MEMO_ASK}"
    )
    calls.append({"role": "final", **call_model(prompt, options.model)})
    return calls


def cond_skill(scenario: dict, options: argparse.Namespace) -> list[dict]:
    skill = skill_text(options.skill_dir)
    plan_prompt = (
        "You have this skill loaded and the request below triggers it.\n\n"
        f"<skill>\n{skill}\n</skill>\n\n{task_block(scenario)}\n\n"
        "You have three subagents. Each investigates separately and is given "
        "the materials it finds; you will see only their reports (at most "
        f"{options.lane_words} words each). Follow the skill. Reply now with "
        "only the three briefs, under the headings '### LANE 1', '### LANE 2' "
        "and '### LANE 3'. Each brief must be self-contained: the subagent "
        "sees nothing except its brief and its materials."
    )
    plan = call_model(plan_prompt, options.model)
    calls = [{"role": "plan", **plan}]
    briefs = [
        part.strip()
        for part in re.split(r"^###\s*LANE\s*\d+.*$", plan["text"], flags=re.M)[1:]
    ]
    if len(briefs) != 3:
        raise RuntimeError(f"expected three briefs, got {len(briefs)}")
    lane_calls = run_lanes(
        [
            f"{brief}\n\nMATERIALS YOU FOUND:\n{materials(scenario, lane)}\n\n"
            f"Your report: at most {options.lane_words} words."
            for brief, lane in zip(briefs, scenario["slices"])
        ],
        options.lane_model,
    )
    calls += lane_calls
    reports = "\n\n".join(
        f"REPORT FROM LANE {index}:\n{call['text']}"
        for index, call in enumerate(lane_calls, 1)
    )
    prompt = (
        "You have this skill loaded and you are finishing the investigation it "
        f"governs.\n\n<skill>\n{skill}\n</skill>\n\n{task_block(scenario)}\n\n"
        f"YOUR BRIEFS WERE:\n{plan['text']}\n\n{reports}\n\n"
        f"Follow the skill to finish. {MEMO_ASK}"
    )
    calls.append({"role": "final", **call_model(prompt, options.model)})
    return calls


def cond_skillfixed(scenario: dict, options: argparse.Namespace) -> list[dict]:
    """No planning call: every lane gets the same template brief."""
    brief = fixed_brief(options.skill_dir, scenario)
    lane_calls = run_lanes(
        [
            f"{brief}\n\nMATERIALS YOU FOUND:\n{materials(scenario, lane)}\n\n"
            f"Your report: at most {options.lane_words} words."
            for lane in scenario["slices"]
        ],
        options.lane_model,
    )
    reports = "\n\n".join(
        f"REPORT FROM LANE {index}:\n{call['text']}"
        for index, call in enumerate(lane_calls, 1)
    )
    prompt = (
        "You have this skill loaded and you are finishing the investigation it "
        f"governs. You sent each of three lanes the skill's lane brief.\n\n"
        f"<skill>\n{skill_text(options.skill_dir)}\n</skill>\n\n"
        f"{task_block(scenario)}\n\n{reports}\n\n"
        f"Follow the skill to finish. {MEMO_ASK}"
    )
    return lane_calls + [{"role": "final", **call_model(prompt, options.model)}]


def cond_format(scenario: dict, options: argparse.Namespace) -> list[dict]:
    """Ablation: the skill's lane report format, merged by a coordinator with no skill."""
    brief = fixed_brief(options.skill_dir, scenario)
    lane_calls = run_lanes(
        [
            f"{brief}\n\nMATERIALS YOU FOUND:\n{materials(scenario, lane)}\n\n"
            f"Your report: at most {options.lane_words} words."
            for lane in scenario["slices"]
        ],
        options.lane_model,
    )
    reports = "\n\n".join(
        f"REPORT FROM ANALYST {index}:\n{call['text']}"
        for index, call in enumerate(lane_calls, 1)
    )
    prompt = (
        "You coordinate three analysts who investigated the question below in "
        "parallel. You have only their reports.\n\n"
        f"{task_block(scenario)}\n\n{reports}\n\n{MEMO_ASK}"
    )
    return lane_calls + [{"role": "final", **call_model(prompt, options.model)}]


def cond_merge(scenario: dict, options: argparse.Namespace) -> list[dict]:
    """Ablation: ordinary analyst reports, merged by a coordinator with the skill."""
    calls = flat_lanes(scenario, options)
    reports = "\n\n".join(
        f"REPORT FROM LANE {index}:\n{call['text']}"
        for index, call in enumerate(calls, 1)
    )
    prompt = (
        "You have this skill loaded and you are finishing an investigation. "
        "Three analysts already reported, in their own format.\n\n"
        f"<skill>\n{skill_text(options.skill_dir)}\n</skill>\n\n"
        f"{task_block(scenario)}\n\n{reports}\n\n"
        f"Follow the skill to finish. {MEMO_ASK}"
    )
    return calls + [{"role": "final", **call_model(prompt, options.model)}]


CONDITIONS = {
    "skillfixed": cond_skillfixed,
    "format": cond_format,
    "merge": cond_merge,
    "single": cond_single,
    "solo": cond_solo,
    "flat": cond_flat,
    "skill": cond_skill,
}


def run_one(job: tuple, options: argparse.Namespace) -> dict:
    scenario_id, condition, run_index = job
    scenario = load_all()[scenario_id]
    started = time.time()
    try:
        calls = CONDITIONS[condition](scenario, options)
    except Exception as error:  # noqa: BLE001 - recorded, never hidden
        return {
            "scenario": scenario_id, "condition": condition, "label": options.label,
            "run": run_index, "error": str(error)[:500],
        }
    return {
        "scenario": scenario_id,
        "class": scenario["class"],
        "condition": condition,
        "label": options.label,
        "run": run_index,
        "lane_words": options.lane_words,
        "family": ACTIVE_FAMILY,
        "memo": memo_of(calls[-1]["text"]),
        "n_calls": len(calls),
        "tokens_in": sum(call["tokens_in"] for call in calls),
        "tokens_out": sum(call["tokens_out"] for call in calls),
        "cost_usd": round(sum(call["cost_usd"] for call in calls), 4),
        "model_seconds": round(sum(call["seconds"] for call in calls), 1),
        "wall_seconds": round(time.time() - started, 1),
        "calls": calls,
    }


def command_run(options: argparse.Namespace) -> int:
    if options.scenarios:
        ids = options.scenarios.split(",")
    else:
        ids = json.loads(SPLIT_FILES[ACTIVE_FAMILY].read_text(encoding="utf-8"))[options.set]
    jobs = [
        (scenario_id, condition, run_index)
        for scenario_id in ids
        for condition in options.conditions.split(",")
        for run_index in range(1, options.runs + 1)
    ]
    out = Path(options.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    with concurrent.futures.ThreadPoolExecutor(options.concurrency) as pool:
        futures = [pool.submit(run_one, job, options) for job in jobs]
        for future in concurrent.futures.as_completed(futures):
            record = future.result()
            with out.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(record, ensure_ascii=False) + "\n")
            print(
                record["scenario"], record["condition"], record["run"],
                "ERROR" if "error" in record else "ok", flush=True,
            )
    return 0


def judge_prompt(record: dict, scenario: dict) -> str:
    checks = "\n".join(f"{check['id']}: {check['question']}" for check in scenario["checks"])
    return (
        "You grade a decision memo against fixed yes/no checks. Judge only "
        "what the memo itself says; do not reward or punish style or length.\n\n"
        f"QUESTION THE MEMO ANSWERS: {scenario['question']}\n\n"
        f"REFERENCE ANSWER (for your understanding only): {scenario['truth']}\n\n"
        f"MEMO:\n{record['memo']}\n\nCHECKS:\n{checks}\n\n"
        "Reply with one JSON object only, mapping each check id to \"yes\" or \"no\"."
    )


def verdict_from(text: str, scenario: dict) -> dict:
    answers = json.loads(re.search(r"\{.*\}", text, flags=re.S).group(0))
    verdict = {
        check["id"]: answers[check["id"]].strip().lower() == check["expected"]
        for check in scenario["checks"]
    }
    return {
        "pass": all(ok for key, ok in verdict.items() if key.startswith(PASS_PREFIXES)),
        "aware": verdict.get("aware"),
        "checks": verdict,
    }


def judge_one(record: dict, model: str | None) -> dict:
    scenario = load_scenario(
        FAMILY[record.get("family", "short")] / f"{record['scenario']}.txt"
    )
    return verdict_from(call_model(judge_prompt(record, scenario), model)["text"], scenario)


def command_judge(options: argparse.Namespace) -> int:
    source = Path(options.records)
    records = [json.loads(line) for line in source.read_text(encoding="utf-8").splitlines()]
    todo = [record for record in records if "memo" in record and "verdict" not in record]
    with concurrent.futures.ThreadPoolExecutor(options.concurrency) as pool:
        for record, verdict in zip(
            todo, pool.map(lambda item: judge_one(item, options.model), todo)
        ):
            record["verdict"] = verdict
    source.write_text(
        "".join(json.dumps(record, ensure_ascii=False) + "\n" for record in records),
        encoding="utf-8",
    )
    print(f"judged {len(todo)} record(s)")
    return 0


def command_report(options: argparse.Namespace) -> int:
    rows: dict[tuple, list[dict]] = {}
    errors = 0
    for name in options.records:
        for line in Path(name).read_text(encoding="utf-8").splitlines():
            record = json.loads(line)
            if "verdict" not in record:
                errors += 1
                continue
            label = record["condition"] + (f":{record['label']}" if record["label"] else "")
            label = f"{record.get('family', 'short')} {label}"
            key = (label, record["lane_words"])
            rows.setdefault(key, []).append(record)
    print("| condition | lane words | trap pass | control pass | aware | calls | tokens in | tokens out | cost USD | seconds |")
    print("| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |")
    for (label, words), records in sorted(rows.items()):
        traps = [r for r in records if r["class"] != "control"]
        controls = [r for r in records if r["class"] == "control"]
        aware = [r for r in traps if r["verdict"]["aware"] is not None]

        def rate(items: list[dict], field: str) -> str:
            if not items:
                return "n/a"
            hits = sum(1 for item in items if item["verdict"][field])
            return f"{hits}/{len(items)}"

        def mean(field: str) -> float:
            return sum(r[field] for r in records) / len(records)

        print(
            f"| {label} | {words} | {rate(traps, 'pass')} | {rate(controls, 'pass')} "
            f"| {rate(aware, 'aware')} | {mean('n_calls'):.1f} | {mean('tokens_in'):.0f} "
            f"| {mean('tokens_out'):.0f} | {mean('cost_usd'):.3f} | {mean('model_seconds'):.0f} |"
        )
    if options.by_scenario:
        print("\n| scenario | condition | lane words | pass | aware |")
        print("| --- | --- | --- | --- | --- |")
        cells: dict[tuple, list[dict]] = {}
        for (label, words), records in rows.items():
            for record in records:
                cells.setdefault((record["scenario"], label, words), []).append(record)
        for (scenario_id, label, words), records in sorted(cells.items()):
            passed = sum(1 for r in records if r["verdict"]["pass"])
            aware = sum(1 for r in records if r["verdict"]["aware"])
            print(f"| {scenario_id} | {label} | {words} | {passed}/{len(records)} | {aware}/{len(records)} |")
    print(f"\nrecords without a verdict (errors or ungraded): {errors}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("split").add_argument("--family", choices=tuple(FAMILY), default="short")
    run = commands.add_parser("run")
    run.add_argument("--set", choices=("dev", "heldout"), default="dev")
    run.add_argument("--family", choices=tuple(FAMILY), default="short")
    run.add_argument("--scenarios", default="")
    run.add_argument("--conditions", required=True)
    run.add_argument("--runs", type=int, default=3)
    run.add_argument("--out", required=True)
    run.add_argument("--label", default="")
    run.add_argument("--skill-dir", type=Path, default=EVAL_DIR.parents[2] / "skills" / "evidence-ledger")
    run.add_argument("--lane-words", type=int, default=120)
    run.add_argument("--model", default=None)
    run.add_argument("--lane-model", default=None)
    run.add_argument("--concurrency", type=int, default=6)
    judge = commands.add_parser("judge")
    judge.add_argument("records")
    judge.add_argument("--model", default=None)
    judge.add_argument("--concurrency", type=int, default=6)
    report = commands.add_parser("report")
    report.add_argument("records", nargs="+")
    report.add_argument("--by-scenario", action="store_true")
    options = parser.parse_args()
    global ACTIVE_FAMILY
    ACTIVE_FAMILY = getattr(options, "family", "short")
    if options.command == "split":
        target = SPLIT_FILES[ACTIVE_FAMILY]
        target.write_text(json.dumps(make_split(), indent=2) + "\n", encoding="utf-8")
        print(target.read_text(encoding="utf-8"))
        return 0
    return {"run": command_run, "judge": command_judge, "report": command_report}[
        options.command
    ](options)


if __name__ == "__main__":
    sys.exit(main())
