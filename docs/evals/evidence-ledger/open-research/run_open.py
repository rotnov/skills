#!/usr/bin/env python3
"""Open-research probe for evidence-ledger: live web research, three conditions.

Standard library only. Every model call goes through the `claude` CLI.

    python3 run_open.py run --runs 3 --out runs/main.jsonl
    python3 run_open.py judge --inp runs/main.jsonl --out runs/judged.jsonl
    python3 run_open.py summary --inp runs/judged.jsonl
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import tempfile
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

HERE = Path(__file__).resolve().parent
TASKS = json.loads((HERE / "tasks.json").read_text(encoding="utf-8"))
SKILL = HERE.parents[3] / "skills" / "evidence-ledger" / "SKILL.md"
MODEL = "claude-opus-5-5"
LOCK = threading.Lock()

ANGLES = [
    "primary and official sources: documentation, official reports, the original studies or announcements",
    "press, forums, issue trackers and community write-ups",
    "independent measurements, critiques and counter-evidence",
]


def call(prompt: str, web: bool, max_turns: int, attempts: int = 3) -> dict:
    tools = "WebSearch,WebFetch" if web else ""
    command = [
        "claude", "-p", "--output-format", "json", "--model", MODEL,
        "--tools", tools, "--setting-sources", "", "--strict-mcp-config",
        "--no-session-persistence", "--max-turns", str(max_turns),
    ]
    if web:
        command += ["--allowedTools", tools]
    last = ""
    for attempt in range(attempts):
        with tempfile.TemporaryDirectory() as cwd:
            started = time.time()
            try:
                done = subprocess.run(command, input=prompt, capture_output=True,
                                      text=True, cwd=cwd, timeout=1800, check=False)
            except subprocess.TimeoutExpired:
                last = "timeout"
                continue
        try:
            data = json.loads(done.stdout)
        except json.JSONDecodeError:
            last = (done.stdout or done.stderr)[:300]
            time.sleep(20 * (attempt + 1))
            continue
        if data.get("result") and not data.get("is_error"):
            usage = data.get("usage", {})
            return {
                "text": data["result"],
                "tokens_in": usage.get("input_tokens", 0)
                + usage.get("cache_creation_input_tokens", 0)
                + usage.get("cache_read_input_tokens", 0),
                "tokens_out": usage.get("output_tokens", 0),
                "cost_usd": data.get("total_cost_usd", 0.0),
                "turns": data.get("num_turns", 0),
                "seconds": round(time.time() - started, 1),
                "subtype": data.get("subtype"),
            }
        last = f"{data.get('subtype')}: {str(data.get('result'))[:200]}"
        time.sleep(20 * (attempt + 1))
    raise RuntimeError(f"call failed: {last}")


def ask(task: dict) -> str:
    return f"QUESTION: {task['question']}\nMY SETUP: {task['setup']}"


def single_prompt(task: dict) -> str:
    return (
        f"{ask(task)}\n\nResearch this with web search and fetch. Budget: about 36 "
        "searches or page fetches in total. Then write the final answer for me in at "
        "most 350 words, with the URL for every source you rely on."
    )


def lane_prompt(task: dict, angle: str) -> str:
    return (
        f"You are one of three researchers working on this question for someone. "
        f"The others cover other angles; yours is: {angle}.\n\n{ask(task)}\n\n"
        "Research your angle with web search and fetch. Budget: about 12 searches or "
        "page fetches. Then report to the coordinator in at most 400 words: your "
        "findings, each with its URL, and what you are unsure of."
    )


def coordinator_prompt(task: dict, reports: list[str], skill: bool) -> str:
    head = ""
    if skill:
        head = ("You have this skill; follow it.\n\n--- SKILL.md ---\n"
                + SKILL.read_text(encoding="utf-8") + "\n--- end of skill ---\n\n")
    body = "\n\n".join(f"REPORT {i + 1}:\n{text}" for i, text in enumerate(reports))
    return (
        f"{head}You coordinated three researchers on this question. Their reports are "
        f"below; you have no tools.\n\n{ask(task)}\n\n{body}\n\nWrite the final answer "
        "for the asker in at most 350 words, with the URL for every source you rely on."
    )


def write(out: Path, record: dict) -> None:
    with LOCK, out.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, ensure_ascii=False) + "\n")
    print(record["task"], record["run"], record["condition"], flush=True)


def done_keys(out: Path) -> set:
    if not out.exists():
        return set()
    return {(r["task"], r["run"], r["condition"])
            for r in map(json.loads, out.read_text(encoding="utf-8").splitlines())}


def run_single(out: Path, task_id: str, run: int) -> None:
    result = call(single_prompt(TASKS[task_id]), web=True, max_turns=80)
    write(out, {"task": task_id, "run": run, "condition": "single", "answer": result})


def run_panel(out: Path, task_id: str, run: int) -> None:
    task = TASKS[task_id]
    with ThreadPoolExecutor(3) as pool:
        lanes = list(pool.map(lambda a: call(lane_prompt(task, a), web=True, max_turns=30), ANGLES))
    write(out, {"task": task_id, "run": run, "condition": "lanes", "lanes": lanes})
    reports = [lane["text"] for lane in lanes]
    for condition, skill in (("flat", False), ("ledger", True)):
        result = call(coordinator_prompt(task, reports, skill), web=False, max_turns=2)
        write(out, {"task": task_id, "run": run, "condition": condition, "answer": result})


def cmd_run(args: argparse.Namespace) -> None:
    out = Path(args.out)
    have = done_keys(out)
    jobs = []
    for run in range(1, args.runs + 1):
        for task_id in (args.tasks.split(",") if args.tasks else TASKS):
            if (task_id, run, "single") not in have:
                jobs.append((run_single, task_id, run))
            if (task_id, run, "ledger") not in have:
                jobs.append((run_panel, task_id, run))
    with ThreadPoolExecutor(args.concurrency) as pool:
        futures = [pool.submit(fn, out, t, r) for fn, t, r in jobs]
        for future in futures:
            try:
                future.result()
            except Exception as error:  # noqa: BLE001 - report and keep going
                print("FAILED", error, file=sys.stderr, flush=True)


JUDGE = """You grade one research answer against a key. You do not know who wrote it.

{ask}

KEY (written in advance from primary sources):
{key}

{lanes}FINAL ANSWER:
{answer}

Definitions:
- E1 collapse: the final answer commits to one explanation/option while a rival in the key's "rivals" is still open on the evidence, without naming it or giving evidence against it. "na" if the key lists no rivals.
- E2 shared root counted twice: the final answer presents two or more sources as separate support for a claim when they retell one origin (see key "shared_origin", or one source plainly cites the other), or uses "widely reported", "multiple sources", "consensus" as weight for a single-origin claim.
- E3 level blur: the final answer states as established, confirmed or verified, or acts on as settled, a claim whose only support is a retelling (news, forum, blog), a non-peer-reviewed abstract or press statement, or an observation on a setup the key marks as different from the asker's.
- decision: "good" if it matches the key's decision; "acceptable" if it reaches the same action with a gap; "wrong" if it recommends a different action, or will not commit where the key commits.

Return only JSON:
{{"decision": "good|acceptable|wrong", "decision_why": "...",
 "E1": "yes|no|na", "E1_evidence": "quote or empty",
 "E2": "yes|no", "E2_evidence": "quote or empty",
 "E3": "yes|no", "E3_evidence": "quote or empty",
 "rivals_in_answer": ["key rivals the answer names"],
 "rivals_in_lanes": ["key rivals any lane report names, or [] if no lanes shown"]}}"""


def judge_one(record: dict, lanes: list[str] | None) -> dict:
    task = TASKS[record["task"]]
    lane_text = ""
    if lanes:
        lane_text = "LANE REPORTS the answer was built from:\n" + "\n\n".join(
            f"REPORT {i + 1}:\n{t}" for i, t in enumerate(lanes)) + "\n\n"
    prompt = JUDGE.format(ask=ask(task), key=json.dumps(task["key"], indent=1),
                          lanes=lane_text, answer=record["answer"]["text"])
    for _ in range(3):
        text = call(prompt, web=False, max_turns=2)["text"]
        start, end = text.find("{"), text.rfind("}")
        try:
            return json.loads(text[start:end + 1])
        except json.JSONDecodeError:
            continue
    raise RuntimeError("judge returned no JSON")


def cmd_judge(args: argparse.Namespace) -> None:
    records = [json.loads(l) for l in Path(args.inp).read_text(encoding="utf-8").splitlines()]
    lanes = {(r["task"], r["run"]): [x["text"] for x in r["lanes"]]
             for r in records if r["condition"] == "lanes"}
    out = Path(args.out)
    have = done_keys(out)
    todo = [r for r in records if r["condition"] != "lanes"
            and (r["task"], r["run"], r["condition"]) not in have]

    def work(record: dict) -> None:
        panel = lanes.get((record["task"], record["run"])) if record["condition"] != "single" else None
        record = dict(record, grade=judge_one(record, panel))
        write(out, record)

    with ThreadPoolExecutor(args.concurrency) as pool:
        list(pool.map(work, todo))


def cmd_summary(args: argparse.Namespace) -> None:
    records = [json.loads(l) for l in Path(args.inp).read_text(encoding="utf-8").splitlines()]
    raw = [json.loads(l) for l in Path(args.raw).read_text(encoding="utf-8").splitlines()]
    override = {}
    if args.overrides and Path(args.overrides).exists():
        override = json.loads(Path(args.overrides).read_text(encoding="utf-8"))
    print("| Condition | n | Decision good/acc/wrong | E1 | E2 | E3 | Dropped rival |")
    print("| --- | --- | --- | --- | --- | --- | --- |")
    for condition in ("single", "flat", "ledger"):
        rows = [r for r in records if r["condition"] == condition]
        count = {k: 0 for k in ("good", "acceptable", "wrong", "E1", "E2", "E3", "drop")}
        applicable_e1 = 0
        for r in rows:
            g = dict(r["grade"])
            g.update(override.get(f"{r['task']}/{r['run']}/{condition}", {}))
            count[g["decision"]] += 1
            for e in ("E1", "E2", "E3"):
                count[e] += g[e] == "yes"
            applicable_e1 += g["E1"] != "na"
            if condition != "single":
                lanes = {x.lower() for x in g.get("rivals_in_lanes", [])}
                answer = {x.lower() for x in g.get("rivals_in_answer", [])}
                count["drop"] += bool(lanes - answer)
        print(f"| {condition} | {len(rows)} | {count['good']}/{count['acceptable']}/{count['wrong']} "
              f"| {count['E1']}/{applicable_e1} | {count['E2']}/{len(rows)} | {count['E3']}/{len(rows)} "
              f"| {count['drop'] if condition != 'single' else '-'} |")
    print()
    print("| Condition | Calls | Tokens in | Tokens out | Cost USD | Seconds (wall) |")
    print("| --- | --- | --- | --- | --- | --- |")
    lanes = {(r["task"], r["run"]): r["lanes"] for r in raw if r["condition"] == "lanes"}
    for condition in ("single", "flat", "ledger"):
        rows = [r for r in raw if r["condition"] == condition]
        tin = tout = cost = secs = 0.0
        for r in rows:
            parts = [r["answer"]]
            lane_parts = [] if condition == "single" else lanes[(r["task"], r["run"])]
            for p in parts + lane_parts:
                tin += p["tokens_in"]; tout += p["tokens_out"]; cost += p["cost_usd"]
            secs += r["answer"]["seconds"] + (max(p["seconds"] for p in lane_parts) if lane_parts else 0)
        n = len(rows) or 1
        calls = 1 if condition == "single" else 4
        print(f"| {condition} | {calls} | {tin / n:,.0f} | {tout / n:,.0f} | {cost / n:.2f} | {secs / n:.0f} |")


def main() -> None:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run")
    r.add_argument("--runs", type=int, default=3)
    r.add_argument("--tasks", default="")
    r.add_argument("--out", required=True)
    r.add_argument("--concurrency", type=int, default=4)
    j = sub.add_parser("judge")
    j.add_argument("--inp", required=True)
    j.add_argument("--out", required=True)
    j.add_argument("--concurrency", type=int, default=6)
    s = sub.add_parser("summary")
    s.add_argument("--inp", required=True)
    s.add_argument("--raw", required=True)
    s.add_argument("--overrides", default="")
    args = parser.parse_args()
    {"run": cmd_run, "judge": cmd_judge, "summary": cmd_summary}[args.cmd](args)


if __name__ == "__main__":
    main()
