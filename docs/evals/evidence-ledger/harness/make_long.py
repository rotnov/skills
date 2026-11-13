#!/usr/bin/env python3
"""Build the `long` scenario family: the same facts buried in filler.

Each document of each scenario is expanded to roughly 250 words by a model.
The original text must survive verbatim as one block that is neither the first
nor the last part of the document; this is checked here, and a document that
fails the check is regenerated. Headers (question, slices, truth, checks) are
copied unchanged, so grading is identical to the short family.
"""

from __future__ import annotations

import concurrent.futures
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import run_eval  # noqa: E402

OUT = run_eval.EVAL_DIR / "scenarios-long"


def expand(question: str, title: str, text: str) -> str:
    prompt = (
        "You are preparing reading material for an exercise. Expand the "
        "document below into a realistic, longer version of the same kind of "
        "source (keep its genre and voice), 230 to 270 words in total.\n\n"
        "Rules:\n"
        "1. The ORIGINAL TEXT must appear exactly once, word for word, as one "
        "unbroken block somewhere in the middle: at least 80 words before it "
        "and at least 80 words after it.\n"
        "2. Everything you add is ordinary filler for this genre: unrelated "
        "sections, other entries, logistics, side remarks, other numbers "
        "about unrelated things.\n"
        "3. The filler must not add, repeat, change, strengthen, weaken, "
        "explain or comment on any fact that bears on this question: "
        f"{question}\n"
        "4. Do not draw attention to the original block. No headings that "
        "single it out. Plain text only, no markdown, no line that starts "
        "with '=='.\n\n"
        f"DOCUMENT TITLE: {title}\nORIGINAL TEXT:\n{text}\n\n"
        "Reply with the expanded document only."
    )
    for _ in range(4):
        result = run_eval.call_model(prompt, None)["text"].strip()
        if result.count(text) != 1 or "\n== " in result or result.startswith("== "):
            continue
        before, after = result.split(text)
        if len(before.split()) >= 60 and len(after.split()) >= 60:
            return result
    raise RuntimeError(f"could not expand: {title}")


def build(path: Path) -> str:
    scenario = run_eval.load_scenario(path)
    head = path.read_text(encoding="utf-8").split("\n== ", 1)[0]
    with concurrent.futures.ThreadPoolExecutor(4) as pool:
        bodies = list(
            pool.map(
                lambda doc: expand(scenario["question"], doc["title"], doc["text"]),
                scenario["docs"].values(),
            )
        )
    parts = [head.rstrip("\n")]
    for (doc_id, doc), body in zip(scenario["docs"].items(), bodies):
        parts.append(f"== {doc_id}: {doc['title']}\n{body}")
    (OUT / path.name).write_text("\n".join(parts) + "\n", encoding="utf-8")
    return path.stem


def main() -> int:
    OUT.mkdir(exist_ok=True)
    paths = [
        path
        for path in sorted(run_eval.SCENARIO_DIR.glob("*.txt"))
        if not (OUT / path.name).exists()
    ]
    with concurrent.futures.ThreadPoolExecutor(3) as pool:
        for name in pool.map(build, paths):
            print("built", name, flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
