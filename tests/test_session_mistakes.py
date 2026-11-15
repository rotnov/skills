from __future__ import annotations

import hang_guard  # noqa: F401
import contextlib
import io
import json
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest import mock
from pathlib import Path
from urllib.parse import quote

SKILL_DIR = Path(__file__).resolve().parents[1] / "skills" / "session-mistakes"
SCRIPTS = SKILL_DIR / "scripts"
sys.path.insert(0, str(SCRIPTS))

import harnesses  # noqa: E402
import mistakes  # noqa: E402
import proposals  # noqa: E402
import redact  # noqa: E402

LINK_RE = re.compile(r"\]\((references/[^)#]+)\)")
FORBIDDEN_RE = re.compile(r"/Users/|/home/[a-z]|[A-Z]:\\Users\\", re.I)

# A labeler that flags every message containing "again" and proves it ran.
FAKE_LABELER = """
import json, sys, pathlib
request = json.load(sys.stdin)
pathlib.Path(sys.argv[1]).open("a").write(request["user"] + "\\n")
items = []
for item in json.loads(request["user"])["items"]:
    if "again" in item["message"]:
        items.append({"i": item["i"], "verdict": "mistake", "category": "ignored-instruction",
                      "description": "The agent ignored a stated constraint.",
                      "evidence": "I told you again", "severity": 2, "confidence": 0.9})
    else:
        items.append({"i": item["i"], "verdict": "none"})
print(json.dumps({"items": items}))
"""


def jsonl(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    # Compact, like the real transcript files.
    lines = (json.dumps(row, separators=(",", ":")) + "\n" for row in rows)
    path.write_text("".join(lines), encoding="utf-8")


def claude_row(kind: str, content, **extra) -> dict:
    row = {
        "type": kind,
        "timestamp": "2026-01-02T03:04:05Z",
        "cwd": "/work/acme-billing",
        "message": {"role": kind, "content": content},
    }
    if kind == "assistant":
        row["message"]["model"] = "model-a"
    row.update(extra)
    return row


def build_home(home: Path) -> None:
    human = {"origin": {"kind": "human"}}
    jsonl(
        home / ".claude/projects/-work-acme-billing/s1.jsonl",
        [
            claude_row("user", "Add a retry to the uploader, do not touch the tests", **human),
            claude_row("assistant", [{"type": "text", "text": "Done. I also rewrote the tests."}]),
            claude_row("user", [{"type": "tool_result", "content": "ok"}], toolUseResult={"x": 1}),
            claude_row("user", "<task-notification><summary>CI passed</summary></task-notification>",
                       origin={"kind": "task-notification"}),
            claude_row("user", "peer says hi", origin={"kind": "peer", "from": "x"}),
            claude_row("user", "<system-reminder>be nice</system-reminder>"),
            claude_row("user", "<command-name>/model</command-name><command-message>model</command-message>"),
            claude_row("user", [{"type": "text", "text": "[Request interrupted by user]"}]),
            claude_row("user", "hook context", isMeta=True),
            claude_row("user", "<system-reminder>worktree</system-reminder>I told you again: leave the tests alone. "
                               "My key is sk-ant-abcdefghijklmnop1234 and mail bob@example.com", **human),
            claude_row("assistant", [{"type": "text", "text": "Sorry, reverting the tests."}]),
            claude_row("user", "thanks", **human),
        ],
    )
    jsonl(home / ".claude/projects/-work-acme-billing/s1/subagents/agent-1.jsonl",
          [claude_row("user", "You are a subagent, do X")])
    jsonl(home / ".claude/projects/-work-bot/s2.jsonl",
          [claude_row("user", "scripted prompt", entrypoint="sdk-cli"),
           claude_row("assistant", [{"type": "text", "text": "ok"}]),
           claude_row("user", "scripted follow-up", entrypoint="sdk-cli")])

    def codex(payload: dict, kind: str = "response_item") -> dict:
        return {"timestamp": "2026-02-03T04:05:06Z", "type": kind, "payload": payload}

    def codex_user(texts: list[str], kinds: list[str] | None = None, item_id: str = "") -> dict:
        payload = {"type": "message", "role": "user",
                   "content": [{"type": "input_text", "text": text} for text in texts]}
        if item_id:
            payload["id"] = item_id
        if kinds:
            payload["internal_chat_message_metadata_passthrough"] = {"content_item_kinds": kinds}
        return codex(payload)

    answer = codex({"type": "message", "role": "assistant",
                    "content": [{"type": "output_text", "text": "Pushed to main."}]})
    jsonl(
        home / ".codex/sessions/2026/02/03/rollout-2026-02-03T04-05-06-aaaa.jsonl",
        [
            codex({"id": "codex-1", "cwd": "/work/acme-billing", "thread_source": "user"}, "session_meta"),
            codex({"model": "model-b", "cwd": "/work/acme-billing"}, "turn_context"),
            codex_user(["# AGENTS.md instructions for /work\n<INSTRUCTIONS>x</INSTRUCTIONS>",
                        "<environment_context><cwd>/work</cwd></environment_context>"],
                       ["agents_md.instructions", "environments.environment_context"]),
            codex_user(["open a pull request, never push to main"], ["user.text"], "m1"),
            answer,
            codex_user(["<heartbeat><automation_id>x</automation_id></heartbeat>"], ["user.heartbeat"]),
            codex_user(["Automation: nightly scan\nScan the repo"]),
            codex_user(["[Quorum · team · lead] This is a participant message"]),
            codex_user(["<realtime_delegation><input>why did you push again</input>"
                        "<transcript_delta>noise</transcript_delta></realtime_delegation>"]),
            codex_user(["open a pull request, never push to main"], ["user.text"], "m1"),
        ],
    )
    jsonl(
        home / ".codex/sessions/2026/02/04/rollout-2026-02-04T04-05-06-bbbb.jsonl",
        [
            codex({"id": "codex-2", "cwd": "/work/x", "thread_source": "subagent",
                   "source": {"subagent": {}}}, "session_meta"),
            codex_user(["You are a worker. Review the diff."], ["user.text"]),
        ],
    )

    session = home / ".grok/sessions" / quote("/work/acme-billing", safe="") / "g1"
    jsonl(
        session / "chat_history.jsonl",
        [
            {"type": "system", "content": "sys"},
            {"type": "user", "content": [{"type": "text", "text": "<user_info>os</user_info>"}],
             "synthetic_reason": "context"},
            {"type": "user", "content": [{"type": "text", "text": "<user_query>\nlist the files\n</user_query>"}],
             "prompt_index": 0},
            {"type": "assistant", "content": "a.txt", "model_id": "model-c"},
            {"type": "user", "content": [{"type": "text", "text": "<user_query>and again, with sizes</user_query>"}],
             "prompt_index": 1},
        ],
    )
    jsonl(session / "events.jsonl",
          [{"ts": "2026-03-01T00:00:00Z", "type": "turn_started"},
           {"ts": "2026-03-01T00:05:00Z", "type": "turn_started"}])
    (session / "summary.json").write_text(json.dumps({"created_at": "2026-03-01T00:00:00Z"}))
    headless = home / ".grok/sessions" / quote("/work/ci", safe="") / "g2"
    jsonl(headless / "chat_history.jsonl",
          [{"type": "user", "content": [{"type": "text", "text": "<user_query>run</user_query>"}], "prompt_index": 0}])
    (headless / "prompt_context.json").write_text(json.dumps({"is_non_interactive": True}))

    jsonl(
        home / ".cursor/projects/work-acme/agent-transcripts/c1/c1.jsonl",
        [
            {"role": "user", "message": {"content": [{"type": "text", "text": "<user_query>hi</user_query>"}]}},
            {"role": "assistant", "message": {"content": [{"type": "text", "text": "hello"}]}},
        ],
    )


def run(module, *argv: str) -> tuple[int, str]:
    out = io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(io.StringIO()):
        code = module.main(list(argv))
    return code, out.getvalue()


class Workspace(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="session-mistakes-test-"))
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.home = self.tmp / "home"
        self.data = self.tmp / "data"
        build_home(self.home)
        self.labeler = self.tmp / "labeler.py"
        self.labeler.write_text(FAKE_LABELER, encoding="utf-8")
        self.sent = self.tmp / "sent.log"

    def tool(self, *argv: str) -> tuple[int, str]:
        return run(mistakes, "--data-dir", str(self.data), *argv)

    def collect(self) -> dict:
        code, out = self.tool("collect", "--home", str(self.home))
        self.assertEqual(code, 0)
        return json.loads(out)

    def label(self, *extra: str) -> tuple[int, str]:
        command = f"{sys.executable} {self.labeler} {self.sent}"
        return self.tool("label", "--labeler-cmd", command, "--concurrency", "1", *extra)

    def messages(self) -> list[dict]:
        db = mistakes.open_db(self.data)
        return [dict(row) for row in db.execute("SELECT * FROM messages ORDER BY harness, session_id, turn_index")]


class CollectTests(Workspace):
    def test_only_human_messages_are_kept(self) -> None:
        report = self.collect()
        texts = {(m["harness"], m["text"]) for m in self.messages()}
        self.assertEqual(
            texts,
            {
                ("claude", "Add a retry to the uploader, do not touch the tests"),
                ("claude", "I told you again: leave the tests alone. My key is sk-ant-abcdefghijklmnop1234 "
                           "and mail bob@example.com"),
                ("claude", "thanks"),
                ("codex", "open a pull request, never push to main"),
                ("codex", "why did you push again"),
                ("grok", "list the files"),
                ("grok", "and again, with sizes"),
                ("cursor", "hi"),
            },
        )
        kinds = report["run"]["claude"]["kinds"]
        self.assertEqual(kinds["tool"], 1)
        self.assertEqual(kinds["automated"], 1)
        self.assertEqual(kinds["interrupt"], 1)
        self.assertEqual(kinds["command"], 1)
        self.assertEqual(kinds["agent"], 3)  # one peer message, two programmatic prompts
        self.assertEqual(kinds["injected"], 2)
        self.assertEqual(report["run"]["claude"]["excluded"], {"subagent transcripts": 1})
        self.assertEqual(report["run"]["codex"]["kinds"]["agent"], 2)  # subagent brief, peer message
        self.assertEqual(report["run"]["codex"]["kinds"]["automated"], 2)
        self.assertEqual(report["run"]["grok"]["kinds"]["agent"], 1)  # headless prompt

    def test_common_record_fields(self) -> None:
        self.collect()
        by_text = {m["text"]: m for m in self.messages()}
        correction = next(m for text, m in by_text.items() if text.startswith("I told you again"))
        self.assertEqual(correction["session_id"], "s1")
        self.assertEqual(correction["project"], "/work/acme-billing")
        self.assertEqual(correction["turn_index"], 1)
        self.assertEqual(correction["model"], "model-a")
        self.assertEqual(correction["interrupted"], 1)
        self.assertEqual(correction["agent_context"], "Done. I also rewrote the tests.")
        self.assertEqual(correction["prev_user"], "Add a retry to the uploader, do not touch the tests")
        voice = by_text["why did you push again"]
        self.assertEqual((voice["session_id"], voice["model"], voice["ts"]),
                         ("codex-1", "model-b", "2026-02-03T04:05:06Z"))
        self.assertEqual(by_text["and again, with sizes"]["ts"], "2026-03-01T00:05:00Z")
        self.assertEqual(by_text["and again, with sizes"]["model"], "model-c")
        self.assertEqual(by_text["and again, with sizes"]["project"], "/work/acme-billing")

    def test_second_collect_reads_nothing_again(self) -> None:
        self.collect()
        report = self.collect()
        for name in ("claude", "codex", "grok", "cursor"):
            self.assertEqual(report["run"][name]["parsed"], 0, name)
            self.assertEqual(report["run"][name]["new_messages"], 0, name)

    @unittest.skipUnless(shutil.which("zstd"), "zstd is not installed")
    def test_compressed_codex_transcripts_are_read(self) -> None:
        plain = next((self.home / ".codex/sessions").glob("**/*aaaa.jsonl"))
        subprocess.run(["zstd", "-q", "--rm", str(plain)], check=True)
        self.collect()
        self.assertIn("why did you push again", {m["text"] for m in self.messages()})

    def test_classifier_edges(self) -> None:
        cases = {
            "<command-name>/fix</command-name><command-args>the login page is broken again</command-args>":
                (harnesses.HUMAN, "the login page is broken again"),
            "<command-name>/compact</command-name><command-args></command-args>": (harnesses.COMMAND, ""),
            "<bash-input>ls</bash-input>": (harnesses.COMMAND, ""),
            "<cross-session-message from=\"x\">hi</cross-session-message>": (harnesses.AGENT, ""),
            "This session is being continued from a previous conversation that ran out": (harnesses.AGENT, ""),
            "<environment_context>x</environment_context>": (harnesses.INJECTED, ""),
            "# Context from my IDE setup:\n## Open tabs:\n- a.py\n## My request for Codex:\nfix it":
                (harnesses.HUMAN, "fix it"),
        }
        for text, expected in cases.items():
            self.assertEqual(harnesses.classify_user_text(text), expected, text)


class LabelTests(Workspace):
    def test_dry_run_sends_nothing_and_shows_the_payload(self) -> None:
        self.collect()
        code, out = self.label("--dry-run")
        self.assertEqual(code, 0)
        self.assertFalse(self.sent.exists())
        self.assertIn('"dry_run": true', out)
        self.assertIn("I told you again", out)
        self.assertIn("Nothing was sent.", out)
        db = mistakes.open_db(self.data)
        self.assertEqual(db.execute("SELECT COUNT(*) FROM labels").fetchone()[0], 0)

    def test_secrets_never_reach_the_model(self) -> None:
        self.collect()
        self.label()
        sent = self.sent.read_text()
        self.assertNotIn("sk-ant-abcdefghijklmnop1234", sent)
        self.assertNotIn("bob@example.com", sent)
        self.assertIn("<secret>", sent)

    def test_labels_are_incremental_and_limits_hold(self) -> None:
        self.collect()
        code, out = self.label("--batch-size", "1", "--max-calls", "2")
        self.assertEqual((code, json.loads(out)["labeled"]), (0, 2))
        code, out = self.label()
        first_pass = json.loads(out)["labeled"]
        # Session openers have no agent reply to judge and are skipped.
        self.assertEqual(first_pass, 2)
        calls = len(self.sent.read_text().splitlines())
        code, out = self.label()
        self.assertEqual(json.loads(out)["labeled"], 0)
        self.assertEqual(len(self.sent.read_text().splitlines()), calls)
        code, out = self.label("--include-first")
        self.assertEqual(json.loads(out)["labeled"], 4)

    def test_table_counts_and_is_anonymized(self) -> None:
        self.collect()
        self.label()
        out_dir = self.tmp / "report"
        code, out = self.tool("table", "--out", str(out_dir))
        self.assertEqual(code, 0)
        table = json.loads((out_dir / "table.json").read_text())
        row = next(r for r in table["rows"] if r["category"] == "ignored-instruction")
        self.assertEqual(row["by_harness"]["claude"], {"count": 1, "sessions": 1, "models": {"model-a": 1}})
        self.assertEqual(row["by_harness"]["codex"]["models"], {"model-b": 1})
        self.assertEqual(row["by_harness"]["grok"]["count"], 1)
        self.assertEqual((row["count"], row["sessions"]), (3, 3))
        published = "".join(p.read_text() for p in out_dir.iterdir())
        for private in ("acme", "billing", "sk-ant", "bob@example.com", "/work"):
            self.assertNotIn(private, published)
        self.assertIn("examples.md#ex-", published)
        code, out = self.tool("rank")
        self.assertEqual(json.loads(out)["ranked"][0]["category"], "ignored-instruction")

    def test_a_forked_session_is_counted_once(self) -> None:
        original = self.home / ".claude/projects/-work-acme-billing/s1.jsonl"
        shutil.copy(original, original.with_name("s1-fork.jsonl"))
        self.collect()
        self.label()
        out_dir = self.tmp / "report"
        self.tool("table", "--out", str(out_dir))
        table = json.loads((out_dir / "table.json").read_text())
        row = next(r for r in table["rows"] if r["category"] == "ignored-instruction")
        self.assertEqual(row["by_harness"]["claude"]["count"], 1)
        self.assertEqual(table["denominators"]["claude"]["labeled"], 2)

    def test_reply_validation(self) -> None:
        taxonomy = mistakes.load_taxonomy()
        rows = [{"id": "a", "text": "No, I said   keep the old name"}, {"id": "b", "text": "ok"}]
        reply = json.dumps({"items": [
            {"i": 0, "verdict": "mistake", "category": "made-up", "evidence": "I said keep the old name",
             "description": "d", "severity": 9, "confidence": 0.7},
            {"i": 1, "verdict": "mistake", "category": "false-claim", "evidence": "never said",
             "description": "d", "severity": 1, "confidence": 0.7},
        ]})
        first, second = mistakes.parse_reply("```json\n" + reply + "\n```", rows, taxonomy)
        self.assertEqual((first["category"], first["other_label"], first["severity"]), ("other", "made-up", 2))
        self.assertEqual(first["evidence_ok"], 1)
        self.assertEqual(second["evidence_ok"], 0)  # a quote the message does not contain
        with self.assertRaises(mistakes.LabelerError):
            mistakes.parse_reply('{"items":[{"i":0,"verdict":"none"}]}', rows, taxonomy)
        with self.assertRaises(mistakes.LabelerError):
            mistakes.parse_reply("no json here", rows, taxonomy)
        # Text after the object, even with braces, does not break parsing.
        trailing = '{"items":[{"i":0,"verdict":"none"},{"i":1,"verdict":"none"}]}\n\nNote: {done}'
        self.assertEqual(len(mistakes.parse_reply(trailing, rows, taxonomy)), 2)

    def test_sample_and_score(self) -> None:
        self.collect()
        self.label()
        code, out = self.tool("sample", "--mistakes", "5", "--negatives", "5")
        path = Path(json.loads(out)["sample"])
        self.assertEqual(path.stat().st_mode & 0o077, 0)
        entries = [json.loads(line) for line in path.read_text().splitlines()]
        for entry in entries:
            entry["human_verdict"] = entry["verdict"]
            entry["human_category"] = "false-claim" if entry["verdict"] == "mistake" else None
        entries[0]["human_verdict"] = "none"
        path.write_text("".join(json.dumps(e) + "\n" for e in entries))
        code, out = self.tool("score", str(path))
        score = json.loads(out)
        self.assertEqual(score["extraction_precision"], {"n": 2, "of": 3, "value": 0.667})
        self.assertEqual(score["classification_precision"]["value"], 0.0)


class RedactTests(unittest.TestCase):
    def test_secrets(self) -> None:
        for secret in (
            "ghp_abcdefghijklmnopqrstuvwxyz0123456789",
            "AKIAIOSFODNN7EXAMPLE",
            "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.abcdefghijk",
            "Authorization: Bearer abcdefghijklmnopqrstuvwxyz",
            "password = hunter2hunter2",
            "content api key 866fb6c23954aa01bb23cc45",
            "postgres://admin:s3cretpass@db.internal/app",
        ):
            cleaned = redact.strip_secrets(f"use {secret} now")
            self.assertIn("<secret>", cleaned, secret)
        self.assertEqual(redact.strip_secrets("plain words stay"), "plain words stay")

    def test_anonymize(self) -> None:
        text = ("Ask @octo about ~/code/acme/app.py on https://acme.example.com/x, host db.acme.io, "
                "id 123e4567-e89b-12d3-a456-426614174000, Acme-Billing.")
        cleaned = redact.anonymize(text, {"acme", "Acme-Billing"})
        for private in ("octo", "acme", "Acme", "example.com", "123e4567"):
            self.assertNotIn(private, cleaned)


class ProposalTests(unittest.TestCase):
    def test_coverage_finds_overlapping_skills(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            skill = Path(tmp) / "skills" / "check-first"
            skill.mkdir(parents=True)
            (skill / "SKILL.md").write_text(
                "---\nname: check-first\ndescription: >-\n  Use before any claim that work is done:\n"
                "  verify with evidence.\n---\n\n# Body\n\nRun the test and read the output.\n"
            )
            other = Path(tmp) / "skills" / "unrelated"
            other.mkdir()
            (other / "SKILL.md").write_text("---\nname: unrelated\ndescription: Draw diagrams.\n---\n\nBody\n")
            code, out = run(proposals, "coverage", "--category", "false-claim",
                            "--skills-dir", str(Path(tmp) / "skills"))
        result = json.loads(out)
        self.assertEqual([c["name"] for c in result["candidates"]], ["check-first"])
        self.assertIn("verify", result["candidates"][0]["matched_in_description"])
        self.assertIn("Keyword overlap only", result["note"])

    def test_transcript_rendering(self) -> None:
        stream = "\n".join(json.dumps(event) for event in (
            {"type": "assistant", "message": {"content": [
                {"type": "text", "text": "Running tests"},
                {"type": "tool_use", "name": "Bash", "input": {"command": "pytest -q"}}]}},
            {"type": "user", "message": {"content": [{"type": "tool_result", "content": "1 failed"}]}},
            {"type": "result", "result": "One test fails.", "total_cost_usd": 0.01},
        ))
        transcript, final, cost = proposals.render_transcript(stream)
        self.assertEqual(transcript, "AGENT: Running tests\nTOOL Bash: pytest -q\nRESULT: 1 failed")
        self.assertEqual(proposals.turns_of({"prompt": "one"}), ["one"])
        self.assertEqual(proposals.turns_of({"prompt": ["one", "two"]}), ["one", "two"])
        self.assertEqual((final, cost), ("One test fails.", 0.01))

    def test_turns_are_sent_one_at_a_time(self) -> None:
        # A fake CLI that reports whether the next turn was already waiting
        # when it answered the current one.
        fake = (
            "import json, select, sys, time\n"
            "while True:\n"
            "    line = sys.stdin.readline()\n"
            "    if not line:\n"
            "        break\n"
            "    time.sleep(0.2)\n"
            "    early = bool(select.select([sys.stdin], [], [], 0)[0])\n"
            "    text = json.loads(line)['message']['content']\n"
            # The real CLI does not put "type" first in a result event.
            "    print(json.dumps({'cost': 1, 'result': f'{text} early={early}', 'type': 'result'}), flush=True)\n"
        )
        with tempfile.TemporaryDirectory() as tmp:
            script = Path(tmp) / "fake.py"
            script.write_text(fake)
            turns = [json.dumps({"type": "user", "message": {"content": name}}) + "\n" for name in ("one", "two", "three")]
            stream = proposals.converse([sys.executable, str(script)], Path(tmp), turns, timeout=30)
        results = [json.loads(line)["result"] for line in stream.splitlines()]
        self.assertEqual(results[:2], ["one early=False", "two early=False"])
        self.assertTrue(results[2].startswith("three"))

    def test_codex_transcript_rendering(self) -> None:
        first = "\n".join(json.dumps(event) for event in (
            {"type": "thread.started", "thread_id": "t-1"},
            {"type": "item.completed", "item": {"type": "agent_message", "text": "Running tests"}},
            {"type": "item.completed", "item": {"type": "command_execution", "command": "pytest -q",
                                                "aggregated_output": "1 failed", "exit_code": 1}},
            {"type": "item.completed", "item": {"type": "file_change", "changes": [{"path": "a.py", "kind": "update"}]}},
            {"type": "item.completed", "item": {"type": "agent_message", "text": "One test fails."}},
        ))
        second = json.dumps({"type": "item.completed", "item": {"type": "agent_message", "text": "Done."}})
        self.assertEqual(proposals.codex_thread_id(first), "t-1")
        transcript, final = proposals.render_codex_transcript([first, second])
        self.assertEqual(transcript.splitlines(), [
            "AGENT: Running tests", "TOOL Bash: pytest -q", "RESULT: 1 failed", "TOOL Edit: a.py",
            "AGENT: One test fails.", "--- end of turn 1 ---", "AGENT: Done.",
        ])
        self.assertEqual(final, "Done.")

    def test_codex_turns_resume_one_isolated_thread(self) -> None:
        # A fake codex that records how it was called and answers each turn.
        fake = (
            "#!" + sys.executable + "\n"
            "import json, os, sys\n"
            "log = os.path.join(os.environ['FAKE_LOG'])\n"
            "with open(log, 'a') as handle:\n"
            "    handle.write(json.dumps({'argv': sys.argv[1:], 'home': os.environ['HOME'],\n"
            "        'codex_home': os.environ['CODEX_HOME'], 'stdin': sys.stdin.read()}) + '\\n')\n"
            "print(json.dumps({'type': 'thread.started', 'thread_id': 't-9'}))\n"
            "print(json.dumps({'type': 'item.completed', 'item': {'type': 'agent_message', 'text': 'ok'}}))\n"
        )
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "bin").mkdir()
            (root / "bin" / "codex").write_text(fake)
            (root / "bin" / "codex").chmod(0o755)
            (root / "real").mkdir()
            (root / "real" / "auth.json").write_text("{}")
            (root / "run").mkdir()
            (root / "skill").mkdir()
            (root / "skill" / "SKILL.md").write_text("Answer first.")
            env = {"PATH": f"{root / 'bin'}:/usr/bin:/bin", "CODEX_HOME": str(root / "real"),
                   "FAKE_LOG": str(root / "log.jsonl")}
            args = proposals.parser().parse_args(["compare", "--agent", "codex", "--agent-model", "gpt-x",
                                                  "--skill", "s", "--scenarios", "s"])
            with mock.patch.dict("os.environ", env):
                streams = proposals.converse_codex(root / "run", root / "run", ["one", "two"], root / "skill", args)
            calls = [json.loads(line) for line in (root / "log.jsonl").read_text().splitlines()]
            isolated_home = root / "run" / "home"
            self.assertTrue((isolated_home / ".codex" / "auth.json").is_symlink())
        self.assertEqual(len(streams), 2)
        self.assertEqual([call["stdin"] for call in calls], ["one", "two"])
        self.assertEqual(calls[0]["argv"][:2], ["exec", "--json"])
        self.assertEqual(calls[1]["argv"][:2], ["exec", "resume"])
        self.assertIn("t-9", calls[1]["argv"])
        self.assertIn("gpt-x", calls[0]["argv"])
        self.assertTrue(any(a.startswith("developer_instructions=") and "Answer first." in a for a in calls[0]["argv"]))
        for call in calls:
            self.assertEqual(call["home"], str(isolated_home))
            self.assertEqual(call["codex_home"], str(isolated_home / ".codex"))

    def test_eval_dry_run_runs_nothing(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            scenarios = Path(tmp) / "s.json"
            scenarios.write_text(json.dumps({"scenarios": [{"id": "a", "prompt": "do it", "pass_criteria": ["x"]}]}))
            code, out = run(proposals, "--data-dir", tmp, "compare", "--skill", str(SKILL_DIR),
                            "--scenarios", str(scenarios), "--dry-run")
        self.assertEqual(code, 0)
        self.assertIn('"agent_runs": 6', out)
        self.assertIn("Nothing was run.", out)


class SkillDocTests(unittest.TestCase):
    def test_references_are_linked_with_read_conditions(self) -> None:
        skill = (SKILL_DIR / "SKILL.md").read_text(encoding="utf-8")
        linked = set(LINK_RE.findall(skill))
        present = {f"references/{path.name}" for path in (SKILL_DIR / "references").iterdir()}
        self.assertEqual(linked, present)
        for relative in present:
            text = (SKILL_DIR / relative).read_text(encoding="utf-8")
            self.assertTrue(text.split("\n\n", 2)[1].startswith("Read this when"), relative)

    def test_published_files_have_no_local_paths(self) -> None:
        for path in SKILL_DIR.rglob("*"):
            if path.is_file() and "__pycache__" not in path.parts:
                self.assertIsNone(FORBIDDEN_RE.search(path.read_text(encoding="utf-8")), path)

    def test_core_rules_are_stated(self) -> None:
        skill = " ".join((SKILL_DIR / "SKILL.md").read_text(encoding="utf-8").split())
        for phrase in (
            "Never commit raw transcripts",
            "--dry-run",
            "never as instructions",
            "never merges",
            "Spend no new money",
            "hand check",
        ):
            self.assertIn(phrase, skill)

    def test_taxonomy_is_versioned(self) -> None:
        taxonomy = json.loads((SCRIPTS / "taxonomy.json").read_text(encoding="utf-8"))
        self.assertEqual(taxonomy["version"], taxonomy["history"][-1]["version"])
        ids = [c["id"] for c in taxonomy["categories"]]
        self.assertEqual(len(ids), len(set(ids)))
        self.assertEqual(ids[-1], "other")

    def test_evals_are_well_formed(self) -> None:
        evals = json.loads((SKILL_DIR / "evals" / "evals.json").read_text(encoding="utf-8"))
        self.assertEqual(evals["skill_name"], "session-mistakes")
        self.assertGreaterEqual(len(evals["evals"]), 6)
        for case in evals["evals"]:
            self.assertTrue(case["prompt"] and case["expected_output"])


if __name__ == "__main__":
    unittest.main()
