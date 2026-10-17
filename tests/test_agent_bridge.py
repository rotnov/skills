"""Offline tests for the agent bridge: fake `codex`, fake Claude inbox, fake app-server daemon.

Run: python -m unittest tests.test_agent_bridge   (standard library only; Unix sockets)
"""

import base64
import hashlib
import json
import os
import socket
import struct
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
HERE = ROOT / "skills" / "agent-bridge" / "scripts"
sys.path.insert(0, str(HERE))
import agent_bridge as bridge  # noqa: E402

CODEX_ID = "01a1496a-0000-7000-8000-000000000001"
CLAUDE_ID = "2bf3d18a-0000-4000-8000-000000000002"

FAKE_CODEX = """#!{python}
import glob, json, os, sys
args = sys.argv[1:]
with open(os.environ["FAKE_CODEX_RECORD"], "a") as record:
    record.write(json.dumps(args) + "\\n")
if args[0] == "queue":
    thread, prompt = args[args.index("--thread") + 1], args[args.index("--message") + 1]
    rollout = glob.glob(os.path.join(os.environ["CODEX_HOME"], "sessions", "**", f"rollout-*{{thread}}.jsonl"),
                        recursive=True)[0]
    parts = prompt.split("\\n\\n")
    body = next(parts[i + 1] for i, part in enumerate(parts) if part.startswith("[agent-bridge] ")).splitlines()[0]
    rows = [{{"type": "response_item", "payload": {{"type": "message", "role": "user",
                                                    "content": [{{"type": "input_text", "text": prompt}}]}}}},
            {{"type": "event_msg", "payload": {{"type": "task_started"}}}},
            {{"type": "response_item", "payload": {{"type": "message", "role": "assistant",
                                                    "content": [{{"type": "output_text", "text": "QUEUE ANSWER: " + body}}]}}}},
            {{"type": "event_msg", "payload": {{"type": "task_complete"}}}}]
    with open(rollout, "a") as stream:
        stream.write("".join(json.dumps(row) + "\\n" for row in rows))
    print(f"Queued message q-1 for thread {{thread}}.")
    sys.exit(0)
if os.environ.get("FAKE_CODEX_ACTIVE_WRITER"):
    print(json.dumps({{"type": "error", "message": f"thread/resume failed: thread {{args[2]}} already has an active writer (code -32600)"}}))
    sys.exit(1)
out = args[args.index("-o") + 1]
prompt = args[-1]
print(json.dumps({{"type": "thread.started", "thread_id": args[2]}}))
if os.environ.get("FAKE_CODEX_FAIL"):
    print(json.dumps({{"type": "turn.failed", "error": {{"message": "simulated failure"}},
                      "message": "simulated failure"}}))
    sys.exit(1)
parts = prompt.split("\\n\\n")
body = next(parts[i + 1] for i, part in enumerate(parts) if part.startswith("[agent-bridge] "))
open(out, "w").write("FAKE ANSWER: " + body.splitlines()[0])
print(json.dumps({{"type": "turn.completed"}}))
"""


class FakeClaudeInbox:
    """A Unix socket that records the frames a Claude session would receive."""

    def __init__(self, path: str):
        self.path, self.frames = path, []
        self.server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.server.bind(path)
        self.server.listen(8)
        self.thread = threading.Thread(target=self._serve, daemon=True)
        self.thread.start()

    def _serve(self):
        while True:
            try:
                connection, _ = self.server.accept()
            except OSError:
                return
            with connection:
                data = b""
                while chunk := connection.recv(65536):
                    data += chunk
            for line in data.decode().splitlines():
                self.frames.append(json.loads(line))

    def texts(self, wait: float = 0.0, count: int = 1) -> list[str]:
        deadline = time.time() + wait
        while len(self.frames) < count and time.time() < deadline:
            time.sleep(0.05)
        return [frame["message"]["content"] for frame in self.frames if frame["type"] == "user"]

    def close(self):
        self.server.close()


WS_GUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"


def ws_send(connection, text):
    data = text.encode()
    header = b"\x81"
    if len(data) < 126:
        header += bytes([len(data)])
    elif len(data) < 65536:
        header += bytes([126]) + struct.pack(">H", len(data))
    else:
        header += bytes([127]) + struct.pack(">Q", len(data))
    connection.sendall(header + data)


def ws_recv(connection):
    def exact(count):
        data = b""
        while len(data) < count:
            chunk = connection.recv(count - len(data))
            if not chunk:
                raise ConnectionError
            data += chunk
        return data
    first, second = exact(2)
    length = second & 0x7F
    if length == 126:
        length = struct.unpack(">H", exact(2))[0]
    elif length == 127:
        length = struct.unpack(">Q", exact(8))[0]
    mask = exact(4) if second & 0x80 else b"\0\0\0\0"
    payload = bytes(byte ^ mask[i % 4] for i, byte in enumerate(exact(length)))
    return None if first & 0x0F == 0x8 else payload.decode()


class FakeDaemon:
    """Speaks the app-server JSON-RPC subset the bridge uses, over WebSocket on a Unix socket."""

    def __init__(self, path, thread, loaded=False, active_writer=False, turn_seconds=0.3,
                 user_turn_seconds=None):
        self.thread, self.loaded, self.active_writer = thread, loaded, active_writer
        self.turn_seconds, self.calls, self.turns, self.order = turn_seconds, [], {}, []
        self.active, self.clients, self.count = None, [], 0
        self.server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.server.bind(path)
        self.server.listen(8)
        threading.Thread(target=self._accept, daemon=True).start()
        if user_turn_seconds:
            self._begin("user-turn", "USER TURN DONE", user_turn_seconds)

    def _accept(self):
        while True:
            try:
                connection, _ = self.server.accept()
            except OSError:
                return
            threading.Thread(target=self._serve, args=(connection,), daemon=True).start()

    def _serve(self, connection):
        head = b""
        while b"\r\n\r\n" not in head:
            head += connection.recv(1)
        key = [line.split(b":", 1)[1].strip() for line in head.split(b"\r\n")
               if line.lower().startswith(b"sec-websocket-key")][0]
        accept = base64.b64encode(hashlib.sha1(key + WS_GUID.encode()).digest()).decode()
        connection.sendall(("HTTP/1.1 101 Switching Protocols\r\nUpgrade: websocket\r\n"
                            f"Connection: Upgrade\r\nSec-WebSocket-Accept: {accept}\r\n\r\n").encode())
        self.clients.append(connection)
        try:
            while (text := ws_recv(connection)) is not None:
                message = json.loads(text)
                if "id" not in message:
                    continue
                self.calls.append((message["method"], message.get("params") or {}))
                try:
                    result = self._handle(message["method"], message.get("params") or {})
                    reply = {"jsonrpc": "2.0", "id": message["id"], "result": result}
                except ValueError as error:
                    reply = {"jsonrpc": "2.0", "id": message["id"],
                             "error": {"code": -32600, "message": str(error)}}
                ws_send(connection, json.dumps(reply))
        except (ConnectionError, OSError):
            pass
        finally:
            if connection in self.clients:
                self.clients.remove(connection)
            connection.close()

    def broadcast(self, method, params):
        for connection in list(self.clients):
            try:
                ws_send(connection, json.dumps({"jsonrpc": "2.0", "method": method, "params": params}))
            except OSError:
                pass

    def _begin(self, turn_id, text, seconds):
        self.active = turn_id
        self.turns[turn_id] = {"status": "inProgress", "text": text}
        self.order.append(turn_id)
        threading.Timer(seconds, self._finish, args=(turn_id,)).start()

    def _finish(self, turn_id):
        turn = self.turns[turn_id]
        turn["final"] = "STEERED" if turn.get("steered") else turn["text"]
        turn["status"], self.active = "completed", None
        self.broadcast("item/completed", {"threadId": self.thread, "turnId": turn_id,
                                          "completedAtMs": 0,
                                          "item": {"type": "agentMessage", "text": turn["final"]}})
        self.broadcast("turn/completed", {"threadId": self.thread,
                                          "turn": {"id": turn_id, "status": "completed", "items": []}})

    def _handle(self, method, params):
        if method == "initialize":
            return {"userAgent": "fake"}
        if method == "thread/loaded/list":
            return {"data": [self.thread] if self.loaded else []}
        if method == "thread/resume":
            if self.active_writer:
                raise ValueError(f"thread/resume failed: thread {self.thread} already has an active writer")
            self.loaded = True
            return {"thread": {"id": self.thread}}
        if method == "thread/turns/list":
            if self.active:
                return {"data": [{"id": self.active, "status": "inProgress", "items": []}]}
            done = [turn_id for turn_id in reversed(self.order)]
            return {"data": [{"id": turn_id, "status": self.turns[turn_id]["status"],
                              "items": [{"type": "agentMessage", "text": self.turns[turn_id].get("final", "")}]}
                             for turn_id in done][: params.get("limit") or 5]}
        if method == "turn/start":
            self.count += 1
            turn_id = f"turn-{self.count}"
            parts = params["input"][0]["text"].split("\n\n")
            body = next(parts[i + 1] for i, part in enumerate(parts)
                        if part.startswith("[agent-bridge] ")).splitlines()[0]
            self._begin(turn_id, "DAEMON ANSWER: " + body, self.turn_seconds)
            self.broadcast("turn/started", {"threadId": self.thread, "turn": {"id": turn_id}})
            return {"turn": {"id": turn_id, "status": "inProgress", "items": []}}
        if method == "turn/steer":
            if params.get("expectedTurnId") != self.active:
                raise ValueError("expected turn is not active")
            self.turns[self.active]["steered"] = True
            return {"turnId": self.active}
        if method == "thread/unsubscribe":
            return {}
        raise ValueError(f"method not found: {method}")

    def methods(self):
        return [method for method, _ in self.calls]

    def close(self):
        self.server.close()


class BridgeTestCase(unittest.TestCase):
    def setUp(self):
        # Short /tmp path: macOS limits Unix socket paths to 104 bytes.
        self.tmp = tempfile.TemporaryDirectory(dir="/tmp", prefix="ab-")
        root = Path(self.tmp.name)
        self.codex_home = root / "codex"
        self._write_rollout(CODEX_ID, busy=False)
        fake = root / "fake-codex"
        fake.write_text(FAKE_CODEX.format(python=sys.executable))
        fake.chmod(0o755)
        self.record = root / "codex-calls.jsonl"
        self.socket_path = str(root / "s")
        self.daemon_path = str(root / "d")
        self.daemon = None
        self.inbox = FakeClaudeInbox(self.socket_path)
        self.saved = dict(os.environ)
        os.environ.update({
            "AGENT_BRIDGE_HOME": str(root / "bridge"), "CODEX_HOME": str(self.codex_home),
            "AGENT_BRIDGE_CODEX": str(fake), "FAKE_CODEX_RECORD": str(self.record),
            "CLAUDE_CODE_SESSION_ID": CLAUDE_ID, "CLAUDE_CODE_MESSAGING_SOCKET": self.socket_path,
            # Never reach the real Codex daemon from tests.
            "AGENT_BRIDGE_CODEX_SOCKET": self.daemon_path, "AGENT_BRIDGE_QUEUE_TIMEOUT": "20",
        })
        os.environ.pop("FAKE_CODEX_ACTIVE_WRITER", None)
        os.environ.pop("FAKE_CODEX_FAIL", None)
        # A test run from a Codex shell inherits both identity variables.
        os.environ.pop("CODEX_THREAD_ID", None)
        os.environ.pop("CODEX_SESSION_ID", None)
        self.workdir = root / "work"
        self.workdir.mkdir()
        os.chdir(self.workdir)

    def tearDown(self):
        self.inbox.close()
        if self.daemon:
            self.daemon.close()
        os.environ.clear()
        os.environ.update(self.saved)
        os.chdir(ROOT)
        self.tmp.cleanup()

    def _write_rollout(self, session_id, busy):
        day = self.codex_home / "sessions" / "2026" / "10" / "17"
        day.mkdir(parents=True, exist_ok=True)
        rows = [{"type": "session_meta", "payload": {
            "id": session_id, "cwd": str(Path(self.tmp.name)), "timestamp": "2026-10-17T10:00:00Z",
            "originator": "codex_exec"}},
            {"type": "event_msg", "payload": {"type": "task_started"}}]
        if not busy:
            rows.append({"type": "event_msg", "payload": {"type": "task_complete"}})
        (day / f"rollout-2026-10-17T10-00-00-{session_id}.jsonl").write_text(
            "\n".join(json.dumps(row) for row in rows) + "\n")

    def pair(self, name="p1", **options):
        return bridge.create_pair(name, CODEX_ID, None, options.get("args", []), False,
                                  options.get("mode", "auto"))

    def start_daemon(self, **options):
        self.daemon = FakeDaemon(self.daemon_path, CODEX_ID, **options)
        return self.daemon

    def codex_calls(self):
        if not self.record.exists():
            return []
        return [json.loads(line) for line in self.record.read_text().splitlines()]


class Pairs(BridgeTestCase):
    def test_create_requires_claude_session_environment(self):
        del os.environ["CLAUDE_CODE_MESSAGING_SOCKET"]
        with self.assertRaises(bridge.BridgeError):
            self.pair()

    def test_unknown_codex_session_is_refused(self):
        with self.assertRaises(bridge.BridgeError):
            bridge.create_pair("p1", "no-such-session", None, [], False)

    def test_codex_thread_has_one_topic_claude_session_many(self):
        self.pair("p1")
        with self.assertRaises(bridge.BridgeError):
            self.pair("p2")  # the same Codex thread cannot get a second topic
        other = "01a1496a-0000-7000-8000-000000000009"
        self._write_rollout(other, busy=False)
        self.assertEqual(bridge.create_pair("p3", other, None, [], False)["name"], "p3")
        sessions = {item["claude"]["session_id"] for item in bridge.all_pairs()}
        self.assertEqual(sessions, {CLAUDE_ID})  # one Claude session, two topics

    def test_a_removed_topic_name_starts_with_an_empty_mailbox(self):
        self.pair("p1")
        bridge.queue_message("p1", "codex", "belongs to the old binding")
        retired = bridge.remove_pair("p1")
        self.assertTrue((retired / "pair.json").exists())
        self.assertEqual(len(list((retired / "mail" / "inbox-codex").glob("*.json"))), 1)
        self.assertFalse(bridge.pair_dir("p1").exists())
        self.pair("p1")
        self.assertEqual(bridge.pending("p1", "codex"), [])
        events = [json.loads(line)["event"] for line in
                  (bridge.pair_dir("p1") / "log.jsonl").read_text().splitlines()]
        self.assertEqual(events, ["pair_created"])

    def test_force_replaces_only_the_named_topic(self):
        self.pair("p1")
        bridge.queue_message("p1", "codex", "old binding")
        with self.assertRaises(bridge.BridgeError):
            bridge.create_pair("p2", CODEX_ID, None, [], True)  # thread already has topic p1
        bridge.create_pair("p1", CODEX_ID, None, [], True)
        self.assertEqual(bridge.pending("p1", "codex"), [])
        self.assertEqual(len(list((bridge.home() / "retired").iterdir())), 1)

    def test_rename_moves_the_topic_and_its_history(self):
        self.pair("p1")
        bridge.queue_message("p1", "codex", "kept in history")
        renamed = bridge.rename_pair("p1", "geometry-oracle")
        self.assertEqual(renamed["renamed_from"][0]["name"], "p1")
        self.assertFalse(bridge.pair_dir("p1").exists())
        self.assertEqual(len(bridge.pending("geometry-oracle", "codex")), 1)
        (bridge.pair_dir("geometry-oracle") / "codex.lock").write_text(f"{os.getpid()} {time.time()}")
        with self.assertRaises(bridge.BridgeError):
            bridge.rename_pair("geometry-oracle", "again")
        with self.assertRaises(bridge.BridgeError):
            bridge.rename_pair("geometry-oracle", "Bad Name")

    def test_a_killed_worker_does_not_block_the_topic(self):
        self.pair("p1")
        directory = bridge.pair_dir("p1")
        (directory / "codex.lock").write_text(f"{os.getpid()} {time.time()}")
        with self.assertRaises(bridge.BridgeError):
            bridge.remove_pair("p1")  # a live worker still owns the topic
        dead = subprocess.Popen([sys.executable, "-c", "pass"])
        dead.wait()
        (directory / "codex.lock").write_text(f"{dead.pid} {time.time()}")
        bridge.write_json(directory / "active_turn.json", {"turn_id": "t", "message_id": "m"})
        bridge.rename_pair("p1", "p2")
        self.assertEqual(sorted(path.name for path in bridge.pair_dir("p2").glob("*.json")), ["pair.json"])
        self.assertFalse((bridge.pair_dir("p2") / "codex.lock").exists())
        bridge.write_json(bridge.pair_dir("p2") / "active_turn.json", {"turn_id": "t", "message_id": "m"})
        retired = bridge.remove_pair("p2")  # an orphaned turn marker without a worker
        self.assertFalse((retired / "active_turn.json").exists())

    def test_dead_lock_holders_are_detected(self):
        dead = subprocess.Popen([sys.executable, "-c", "pass"])
        dead.wait()
        self.assertTrue(bridge._process_alive(os.getpid()))
        self.assertFalse(bridge._process_alive(dead.pid))
        with mock.patch.object(bridge, "IS_WINDOWS", True):
            # Where the Windows API is missing (as here), a holder that cannot be checked is alive.
            self.assertTrue(bridge._process_alive(dead.pid))

    def test_refresh_updates_every_topic_of_the_session(self):
        self.pair("p1")
        other = "01a1496a-0000-7000-8000-000000000009"
        self._write_rollout(other, busy=False)
        bridge.create_pair("p2", other, None, [], False)
        os.environ["CLAUDE_CODE_MESSAGING_SOCKET"] = "/tmp/new-socket"
        self.assertEqual(sorted(bridge.refresh_claude()), ["p1", "p2"])
        self.assertEqual({item["claude"]["socket"] for item in bridge.all_pairs()}, {"/tmp/new-socket"})

    def test_invalid_pair_name(self):
        with self.assertRaises(bridge.BridgeError):
            bridge.create_pair("../x", CODEX_ID, None, [], False)


class CodexToClaude(BridgeTestCase):
    def test_message_reaches_the_paired_claude_socket(self):
        self.pair()
        os.environ.pop("CLAUDE_CODE_SESSION_ID")  # the sender is Codex
        result = bridge.send("p1", "claude", "build finished, see report.md", None, False)
        self.assertEqual(result["status"], "delivered")
        text = self.inbox.texts(wait=2)[0]
        self.assertIn('Message from Codex session 01a1496a via pair "p1"', text)
        self.assertIn("build finished, see report.md", text)
        sent = list((bridge.pair_dir("p1") / "mail" / "sent").glob("*.json"))
        self.assertEqual(len(sent), 1)

    def test_unreachable_socket_keeps_the_message(self):
        self.pair()
        self.inbox.close()
        os.unlink(self.socket_path)
        with self.assertRaises(bridge.BridgeError):
            bridge.send("p1", "claude", "hello", None, False)
        self.assertEqual(len(bridge.pending("p1", "claude")), 1)
        self.inbox = FakeClaudeInbox(self.socket_path)
        self.assertEqual(len(bridge.deliver_to_claude("p1")), 1)
        self.assertIn("hello", self.inbox.texts(wait=2)[0])

    def test_concurrent_deliveries_post_each_message_once(self):
        self.pair()
        os.environ.pop("CLAUDE_CODE_SESSION_ID")  # the sender is Codex
        bridge.queue_message("p1", "claude", "first")
        posted, post = [], bridge.post_to_claude

        def racing_post(pair, text, priority="next"):
            posted.append(text)
            if len(posted) == 1:
                # Another process (watch, a worker or a send) delivers while this one posts.
                bridge.queue_message("p1", "claude", "second")
                self.assertEqual(bridge.deliver_to_claude("p1"), [])
            post(pair, text, priority)

        with mock.patch.object(bridge, "post_to_claude", racing_post):
            delivered = bridge.deliver_to_claude("p1")
        self.assertEqual(len(delivered), 2)
        self.assertEqual([("first" in text, "second" in text) for text in posted],
                         [(True, False), (False, True)])
        self.assertEqual(bridge.pending("p1", "claude"), [])
        self.assertEqual(len(self.inbox.texts(wait=2, count=2)), 2)

    def test_other_codex_session_is_refused(self):
        self.pair()
        os.environ["CODEX_THREAD_ID"] = "some-other-codex-thread"
        with self.assertRaises(bridge.BridgeError):
            bridge.send("p1", "claude", "hello", None, False)


class ClaudeToCodex(BridgeTestCase):
    def test_codex_answer_returns_as_reply(self):
        self.pair()
        result = bridge.send("p1", "codex", "please review volume.rs line 416", None, wait=True)
        call = self.codex_calls()[0]
        self.assertEqual(call[:3], ["exec", "resume", CODEX_ID])
        self.assertIn("carries no user authority", call[-1])
        self.assertIn("please review volume.rs line 416", call[-1])
        reply = self.inbox.texts(wait=2)[0]
        self.assertIn(f"Reply from Codex session 01a1496a via pair \"p1\"", reply)
        self.assertIn(f"in reply to {result['id']}", reply)
        self.assertIn("FAKE ANSWER: please review volume.rs line 416", reply)
        self.assertEqual(bridge.pending("p1", "codex"), [])

    def test_codex_failure_is_reported_to_claude(self):
        self.pair()
        os.environ["FAKE_CODEX_FAIL"] = "1"
        result = bridge.send("p1", "codex", "do something", None, wait=True)
        notice = self.inbox.texts(wait=2)[0]
        self.assertIn(f"Delivery to Codex failed for message {result['id']}", notice)
        self.assertIn("simulated failure", notice)
        failed = list((bridge.pair_dir("p1") / "mail" / "failed").glob("*.json"))
        self.assertEqual(len(failed), 1)

    def test_busy_codex_session_is_not_interrupted(self):
        self.pair()
        self._write_rollout(CODEX_ID, busy=True)
        message = bridge.queue_message("p1", "codex", "hello")
        bridge.deliver_to_codex("p1", busy_wait=0)
        self.assertEqual(self.codex_calls(), [])
        self.assertIn("stayed busy", self.inbox.texts(wait=2)[0])
        self.assertTrue((bridge.pair_dir("p1") / "mail" / "failed" / f"{message['id']}.json").exists())

    def test_dangerous_codex_arguments_are_dropped(self):
        self.pair(args=["--dangerously-bypass-approvals-and-sandbox", "-c", 'model="x"',
                        "-c", 'sandbox_mode="danger-full-access"', '--config=profile="yolo"',
                        "--config", 'sandbox_workspace_write.writable_roots=["/"]',
                        "-c", 'default_permissions=":danger-full-access"', "-c"])
        bridge.send("p1", "codex", "hello", None, wait=True)
        call = self.codex_calls()[0]
        self.assertNotIn("--dangerously-bypass-approvals-and-sandbox", call)
        self.assertNotIn('--config=profile="yolo"', call)
        settings = [call[index + 1] for index, arg in enumerate(call) if arg in ("-c", "--config")]
        # The permission profile is pinned too: it wins over sandbox_mode in Codex 0.138+.
        self.assertEqual(settings, ['model="x"', 'default_permissions=":read-only"',
                                    'sandbox_mode="read-only"'])

    def test_sandbox_is_only_ever_read_only_or_workspace_write(self):
        legacy = {"codex": {"args": ["-c", 'sandbox_mode="danger-full-access"']}}
        self.assertEqual(bridge.codex_sandbox(legacy), "read-only")
        legacy = {"codex": {"args": ["-c", 'sandbox_mode="workspace-write"']}}
        self.assertEqual(bridge.codex_sandbox(legacy), "workspace-write")
        self.assertEqual(bridge.codex_sandbox({"codex": {"sandbox": "danger-full-access"}}),
                         "read-only")

    def test_messages_keep_their_enqueue_order(self):
        self.pair()
        suffixes = [mock.Mock(hex="f" * 32), mock.Mock(hex="0" * 32)]  # a reversed tie-breaker
        with mock.patch.object(bridge.uuid, "uuid4", side_effect=suffixes):
            first = bridge.queue_message("p1", "codex", "first")
            second = bridge.queue_message("p1", "codex", "second")
        self.assertEqual([item["id"] for item in bridge.pending("p1", "codex")],
                         [first["id"], second["id"]])

    def test_other_claude_session_is_refused(self):
        self.pair()
        os.environ["CLAUDE_CODE_SESSION_ID"] = "not-the-paired-session"
        with self.assertRaises(bridge.BridgeError):
            bridge.send("p1", "codex", "hello", None, wait=True)

    def test_detached_worker_delivers_the_reply(self):
        self.pair()
        result = bridge.send("p1", "codex", "async check", None, wait=False)
        self.assertIn("queued", result["status"])
        texts = self.inbox.texts(wait=15)
        self.assertTrue(texts, "no reply from the detached worker")
        self.assertIn("FAKE ANSWER: async check", texts[0])


class Limits(BridgeTestCase):
    def test_size_and_rate_limits(self):
        self.pair()
        with self.assertRaises(bridge.BridgeError):
            bridge.queue_message("p1", "codex", "x" * (bridge.MAX_TEXT_CHARS + 1))
        for _ in range(bridge.RATE_LIMIT):
            bridge.queue_message("p1", "codex", "ping")
        with self.assertRaises(bridge.BridgeError):
            bridge.queue_message("p1", "codex", "one too many")


class DeliveryRoutes(BridgeTestCase):
    def test_daemon_route_when_the_daemon_is_up(self):
        daemon = self.start_daemon()
        self.pair()
        result = bridge.send("p1", "codex", "check the build", None, wait=True)
        reply = self.inbox.texts(wait=3)[0]
        self.assertIn("DAEMON ANSWER: check the build", reply)
        self.assertIn("(delivered by daemon)", reply)
        self.assertEqual(self.codex_calls(), [])
        resume = dict(daemon.calls)["thread/resume"]
        self.assertEqual((resume["sandbox"], resume["approvalPolicy"]), ("read-only", "never"))
        self.assertEqual(resume["config"], {"default_permissions": ":read-only"})
        self.assertIn("thread/unsubscribe", daemon.methods())
        self.assertTrue((bridge.pair_dir("p1") / "mail" / "sent" / f"{result['id']}.json").exists())

    def test_loaded_thread_keeps_its_own_settings(self):
        daemon = self.start_daemon(loaded=True)
        self.pair()
        bridge.send("p1", "codex", "hello", None, wait=True)
        resume = dict(daemon.calls)["thread/resume"]
        self.assertNotIn("sandbox", resume)
        self.assertNotIn("approvalPolicy", resume)
        self.assertNotIn("config", resume)

    def test_desktop_hosted_thread_falls_back_to_queue(self):
        self.start_daemon(active_writer=True)
        self.pair()
        bridge.send("p1", "codex", "path question", None, wait=True)
        texts = self.inbox.texts(wait=3, count=2)
        self.assertIn("Queued in the Codex thread", texts[0])
        self.assertIn("QUEUE ANSWER: path question", texts[1])
        self.assertIn("(delivered by queue)", texts[1])
        self.assertEqual([call[0] for call in self.codex_calls()], ["queue"])

    def test_without_daemon_exec_is_used(self):
        self.pair()
        bridge.send("p1", "codex", "hello", None, wait=True)
        self.assertEqual(self.codex_calls()[0][:2], ["exec", "resume"])
        self.assertIn("(delivered by exec)", self.inbox.texts(wait=2)[0])

    def test_exec_active_writer_falls_back_to_queue(self):
        self.pair()
        os.environ["FAKE_CODEX_ACTIVE_WRITER"] = "1"
        bridge.send("p1", "codex", "hello", None, wait=True)
        self.assertEqual([call[0] for call in self.codex_calls()], ["exec", "queue"])
        self.assertIn("QUEUE ANSWER: hello", self.inbox.texts(wait=3, count=2)[-1])

    def test_pinned_exec_mode_skips_the_daemon(self):
        daemon = self.start_daemon()
        self.pair(mode="exec")
        bridge.send("p1", "codex", "hello", None, wait=True)
        self.assertNotIn("turn/start", daemon.methods())
        self.assertEqual(self.codex_calls()[0][:2], ["exec", "resume"])

    def test_pinned_daemon_mode_does_not_queue(self):
        self.start_daemon(active_writer=True)
        self.pair(mode="daemon")
        bridge.send("p1", "codex", "hello", None, wait=True)
        self.assertEqual(self.codex_calls(), [])
        self.assertIn("hosted by another app-server", self.inbox.texts(wait=2)[0])

    def test_a_lost_daemon_connection_falls_back_only_before_the_turn(self):
        self.start_daemon()
        self.pair()
        send_text = bridge.codex_daemon.UnixWebSocket.send_text

        def failing(method):
            def send(connection, text):
                if f'"method": "{method}"' in text:
                    raise BrokenPipeError
                send_text(connection, text)
            return send

        with mock.patch.object(bridge.codex_daemon.UnixWebSocket, "send_text", failing("thread/resume")):
            bridge.send("p1", "codex", "first", None, wait=True)
        self.assertIn("(delivered by exec)", self.inbox.texts(wait=2)[0])  # nothing had started
        with mock.patch.object(bridge.codex_daemon.UnixWebSocket, "send_text", failing("turn/start")):
            second = bridge.send("p1", "codex", "second", None, wait=True)
        self.assertEqual(len(self.codex_calls()), 1)  # the turn may have started: no exec retry
        self.assertIn(f"Delivery to Codex failed for message {second['id']}",
                      self.inbox.texts(wait=2, count=2)[1])

    def test_daemon_waits_for_a_running_turn(self):
        daemon = self.start_daemon(loaded=True, user_turn_seconds=1.5)
        self.pair()
        started = time.time()
        bridge.send("p1", "codex", "after you", None, wait=True)
        self.assertGreater(time.time() - started, 1.2)
        self.assertEqual(daemon.turns["user-turn"]["status"], "completed")
        self.assertIn("DAEMON ANSWER: after you", self.inbox.texts(wait=2)[0])


class Steering(BridgeTestCase):
    def test_steer_into_a_bridge_turn(self):
        daemon = self.start_daemon(turn_seconds=4)
        self.pair()
        first = bridge.send("p1", "codex", "long task", None, wait=False)
        state = bridge.pair_dir("p1") / "active_turn.json"
        deadline = time.time() + 10
        while not state.exists() and time.time() < deadline:
            time.sleep(0.1)
        self.assertTrue(state.exists(), "the worker never started a daemon turn")
        result = bridge.send("p1", "codex", "change of plan", None, False, steer=True)
        self.assertIn(f"answering message {first['id']}", result["status"])
        self.assertEqual(list(bridge.pair_dir("p1").glob("steer-*.lock")), [])  # no worker owes it
        steer = [params for method, params in daemon.calls if method == "turn/steer"][0]
        self.assertEqual(steer["expectedTurnId"], "turn-1")
        reply = self.inbox.texts(wait=10)[0]
        self.assertIn("STEERED", reply)
        self.assertIn(f"in reply to {first['id']}", reply)

    def test_steer_into_another_turn_gets_its_own_reply(self):
        self.start_daemon(loaded=True, user_turn_seconds=1.5)
        self.pair()
        result = bridge.send("p1", "codex", "also check X", None, False, steer=True)
        self.assertIn("steered into turn user-turn", result["status"])
        reply = self.inbox.texts(wait=10)[0]
        self.assertIn("STEERED", reply)
        self.assertIn(f"in reply to {result['id']}", reply)

    def test_a_steer_never_enters_the_delivery_queue(self):
        self.start_daemon(loaded=True, user_turn_seconds=1.5)
        self.pair()
        seen, steer = [], bridge.codex_daemon.steer

        def watched_steer(thread, text):
            # A worker draining the queue now must not pick the steer up as a new turn.
            seen.append(bridge.pending("p1", "codex"))
            return steer(thread, text)

        with mock.patch.object(bridge.codex_daemon, "steer", watched_steer):
            result = bridge.send("p1", "codex", "also check X", None, False, steer=True)
        self.assertEqual(seen, [[]])
        self.assertTrue((bridge.pair_dir("p1") / "mail" / "sent" / f"{result['id']}.json").exists())
        self.assertIn("STEERED", self.inbox.texts(wait=10)[0])

    def test_a_steered_reply_still_owed_keeps_the_topic_busy(self):
        self.start_daemon(loaded=True, user_turn_seconds=2)
        self.pair()
        bridge.send("p1", "codex", "also check X", None, False, steer=True)
        with self.assertRaises(bridge.BridgeError):
            bridge.rename_pair("p1", "p2")  # the await-turn worker has not posted the reply yet
        self.assertIn("STEERED", self.inbox.texts(wait=10)[0])
        deadline = time.time() + 5
        while list(bridge.pair_dir("p1").glob("steer-*.lock")) and time.time() < deadline:
            time.sleep(0.1)
        self.assertEqual(bridge.rename_pair("p1", "p2")["name"], "p2")

    def test_steer_needs_an_active_turn(self):
        self.start_daemon(loaded=True)
        self.pair()
        with self.assertRaises(bridge.BridgeError):
            bridge.send("p1", "codex", "steer nothing", None, False, steer=True)
        self.assertEqual(len(list((bridge.pair_dir("p1") / "mail" / "failed").glob("*.json"))), 1)
        self.assertEqual(bridge.pending("p1", "codex"), [])  # a refused steer is not sent later
        self.assertEqual(list(bridge.pair_dir("p1").glob("steer-*.lock")), [])

    def test_steer_needs_the_daemon(self):
        self.pair()
        with self.assertRaises(bridge.BridgeError):
            bridge.send("p1", "codex", "steer", None, False, steer=True)

    def test_steer_is_for_codex_only(self):
        self.pair()
        with self.assertRaises(bridge.BridgeError):
            bridge.send("p1", "claude", "x", None, False, steer=True)


class ClaudePriority(BridgeTestCase):
    def test_priority_is_carried_in_the_frame(self):
        self.pair()
        os.environ.pop("CLAUDE_CODE_SESSION_ID")
        bridge.send("p1", "claude", "after your turn", None, False, priority="later")
        self.inbox.texts(wait=2)
        self.assertEqual(self.inbox.frames[-1]["priority"], "later")
        with self.assertRaises(bridge.BridgeError):
            bridge.send("p1", "claude", "x", None, False, priority="now")


class Protocol(BridgeTestCase):
    def last_prompt(self):
        return self.codex_calls()[-1][-1]

    def test_first_message_carries_the_protocol_then_only_the_footer(self):
        self.pair()
        bridge.send("p1", "codex", "first", None, wait=True)
        self.assertIn("[agent-bridge protocol]", self.last_prompt())
        bridge.send("p1", "codex", "second", None, wait=True)
        prompt = self.last_prompt()
        self.assertNotIn("[agent-bridge protocol]", prompt)
        self.assertIn('(bridge: topic "p1"; your final message in this turn is returned', prompt)
        self.assertIn("send p1 --to claude", prompt)

    def test_compaction_brings_the_protocol_back(self):
        self.pair()
        bridge.send("p1", "codex", "first", None, wait=True)
        rollout = bridge.codex_rollout(CODEX_ID)
        with open(rollout, "a") as stream:
            stream.write(json.dumps({"timestamp": "2099-01-01T00:00:00.000Z", "type": "compacted",
                                     "payload": {"message": ""}}) + "\n")
        bridge.send("p1", "codex", "after compaction", None, wait=True)
        self.assertIn("[agent-bridge protocol]", self.last_prompt())
        bridge.send("p1", "codex", "later", None, wait=True)
        # The compaction above is dated in the future, so it keeps asking; a real one is in the past.
        self.assertIn("[agent-bridge protocol]", self.last_prompt())

    def test_handshake_asks_for_the_return_path_and_records_it(self):
        self.pair()
        result = bridge.send_handshake("p1", wait=True)
        prompt = self.last_prompt()
        self.assertIn("[agent-bridge protocol]", prompt)
        self.assertIn(f"--reply-to {result['id']} --text 'handshake ok'", prompt)
        self.assertIn(f"in reply to {result['id']}", self.inbox.texts(wait=2)[0])
        os.environ.pop("CLAUDE_CODE_SESSION_ID")
        os.environ["CODEX_THREAD_ID"] = CODEX_ID  # Codex runs the command from its shell
        bridge.send("p1", "claude", "handshake ok", result["id"], False)
        handshake = bridge.load_pair("p1")["handshake"]
        self.assertIsNotNone(handshake["return_path_verified"])
        self.assertTrue(handshake["verified_caller"])
        self.assertIsNotNone(handshake["last_protocol_at"])


class McpServer(BridgeTestCase):
    def rpc(self, requests, *server_args):
        lines = "".join(json.dumps(request) + "\n" for request in requests)
        result = subprocess.run(
            [sys.executable, "-I", str(HERE / "agent_bridge_mcp.py"), *server_args],
            input=lines, capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stderr)
        return [json.loads(line) for line in result.stdout.splitlines()]

    def test_codex_side_tools(self):
        self.pair()
        env_backup = os.environ.pop("CLAUDE_CODE_SESSION_ID")
        try:
            responses = self.rpc([
                {"jsonrpc": "2.0", "id": 1, "method": "initialize",
                 "params": {"protocolVersion": "2025-06-18", "capabilities": {},
                            "clientInfo": {"name": "test", "version": "0"}}},
                {"jsonrpc": "2.0", "method": "notifications/initialized"},
                {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
                {"jsonrpc": "2.0", "id": 3, "method": "tools/call",
                 "params": {"name": "bridge_send", "arguments": {"pair": "p1", "text": "via mcp"}}},
                {"jsonrpc": "2.0", "id": 4, "method": "tools/call",
                 "params": {"name": "bridge_send", "arguments": {"pair": "zz", "text": "x"}}},
                {"jsonrpc": "2.0", "id": 5, "method": "nope"},
            ], "--side", "codex", "--pair", "p1")
        finally:
            os.environ["CLAUDE_CODE_SESSION_ID"] = env_backup
        by_id = {response["id"]: response for response in responses}
        self.assertEqual(set(by_id), {1, 2, 3, 4, 5})  # the notification got no response
        self.assertEqual(by_id[1]["result"]["serverInfo"]["name"], "agent-bridge")
        names = [tool["name"] for tool in by_id[2]["result"]["tools"]]
        self.assertEqual(names, ["bridge_pairs", "bridge_send", "bridge_inbox", "bridge_read"])
        self.assertFalse(by_id[3]["result"]["isError"], by_id[3])
        self.assertIn("via mcp", self.inbox.texts(wait=2)[0])
        self.assertTrue(by_id[4]["result"]["isError"])
        self.assertEqual(by_id[5]["error"]["code"], -32601)


if __name__ == "__main__":
    unittest.main(verbosity=2)
