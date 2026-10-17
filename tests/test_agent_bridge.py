"""Offline tests for the agent bridge: fake `codex`, fake Claude inbox, fake app-server daemon.

Run: python -m unittest tests.test_agent_bridge   (standard library only; Unix sockets)
"""

import base64
import contextlib
import hashlib
import io
import json
import os
import re
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
raw = sys.argv[1:]
with open(os.environ["FAKE_CODEX_RECORD"] + ".raw", "a") as record:
    record.write(json.dumps(raw) + "\\n")
with open(os.environ["FAKE_CODEX_RECORD"] + ".env", "a") as record:
    record.write(json.dumps(sorted(key for key in os.environ
                                   if key.startswith("CLAUDE_CODE_") or key == "CLAUDECODE")) + "\\n")
# `exec resume ... -` reads the prompt from stdin; record it in its place.
args = raw[:-1] + [sys.stdin.read()] if raw[0] == "exec" and raw[-1] == "-" else raw
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
                 user_turn_seconds=None, fail_methods=(), silent_methods=(),
                 user_turn_status="completed", drop_after_start=False):
        self.thread, self.loaded, self.active_writer = thread, loaded, active_writer
        self.fail_methods = set(fail_methods)  # answered with a JSON-RPC error
        self.silent_methods = set(silent_methods)  # never answered
        self.user_turn_status = user_turn_status
        self.drop_after_start = drop_after_start  # close the connection once turn/start is answered
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
                if message["method"] in self.silent_methods:
                    continue
                try:
                    result = self._handle(message["method"], message.get("params") or {})
                    reply = {"jsonrpc": "2.0", "id": message["id"], "result": result}
                except ValueError as error:
                    reply = {"jsonrpc": "2.0", "id": message["id"],
                             "error": {"code": -32600, "message": str(error)}}
                ws_send(connection, json.dumps(reply))
                if message["method"] == "turn/start" and self.drop_after_start:
                    self.drop_after_start = False
                    break  # the bridge loses this connection while the turn runs
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
        status = self.user_turn_status if turn_id == "user-turn" else "completed"
        turn["status"], self.active = status, None
        self.broadcast("item/completed", {"threadId": self.thread, "turnId": turn_id,
                                          "completedAtMs": 0,
                                          "item": {"type": "agentMessage", "text": turn["final"]}})
        self.broadcast("turn/completed", {"threadId": self.thread,
                                          "turn": {"id": turn_id, "status": status, "items": []}})

    def _handle(self, method, params):
        if method in self.fail_methods:
            raise ValueError(f"unsupported request {method}")
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

    def test_concurrent_creations_keep_one_topic_per_thread(self):
        write, raced = bridge.write_json, []

        def racing_write(path, value):
            if path.name == "pair.json" and not raced:
                raced.append(True)
                # Another command pairs the same thread after this one scanned, before it wrote.
                try:
                    bridge.create_pair("p2", CODEX_ID, None, [], False)
                except bridge.BridgeError as error:
                    raced.append(error)
            write(path, value)

        with mock.patch.object(bridge, "LIFECYCLE_WAIT_SECONDS", 0.5), \
                mock.patch.object(bridge, "write_json", racing_write):
            self.pair("p1")
        self.assertIsInstance(raced[-1], bridge.BridgeError)
        self.assertEqual([item["name"] for item in bridge.all_pairs()], ["p1"])

    def test_pair_updates_are_serialized_with_renames(self):
        self.pair("p1")
        write, outcome = bridge.write_json, []

        def racing_write(path, value):
            if path.name == "pair.json" and not outcome:
                outcome.append(None)  # race once
                # A rename runs after `pair set` read the binding, before it wrote it back.
                try:
                    bridge.rename_pair("p1", "p2")
                    outcome[0] = "renamed"
                except bridge.BridgeError:
                    outcome[0] = "refused"
            write(path, value)

        with mock.patch.object(bridge, "LIFECYCLE_WAIT_SECONDS", 0.5), \
                mock.patch.object(bridge, "write_json", racing_write):
            bridge.update_pair("p1", "exec", None)
        self.assertEqual(outcome, ["refused"])
        self.assertEqual([item["name"] for item in bridge.all_pairs()], ["p1"])

    def test_a_pair_update_logs_under_the_lifecycle_lock(self):
        self.pair("p1")
        log, outcome = bridge.log_event, []

        def racing_log(name, event, **fields):
            if event == "pair_updated" and not outcome:
                outcome.append(None)  # race once
                try:
                    bridge.rename_pair("p1", "p2")  # a rename between the update and its log
                    outcome[0] = "renamed"
                except bridge.BridgeError:
                    outcome[0] = "refused"
            log(name, event, **fields)

        with mock.patch.object(bridge, "LIFECYCLE_WAIT_SECONDS", 0.5), \
                mock.patch.object(bridge, "log_event", racing_log):
            bridge.update_pair("p1", "exec", None)
        self.assertEqual(outcome, ["refused"])

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

    def test_reclaiming_a_stale_lock_never_removes_a_new_owner(self):
        path = Path(self.tmp.name) / "x.lock"
        dead = subprocess.Popen([sys.executable, "-c", "pass"])
        dead.wait()
        path.write_text(f"{dead.pid} {time.time()}")
        late, rival = bridge.Lock(path, 60), {}
        judge = late._stale

        def judged_while_a_rival_tries(*args):
            stale = judge(*args)
            # Another process judges the same lock stale and tries to take it meanwhile.
            thread = threading.Thread(target=lambda: rival.update(lock=bridge.Lock(path, 60).__enter__()))
            thread.start()
            thread.join(0.5)
            rival["thread"], rival["waited"] = thread, thread.is_alive()
            return stale

        late._stale = judged_while_a_rival_tries
        with late as lock:
            rival["thread"].join(5)
            holders = sorted([lock.held, rival["lock"].held])
        self.assertTrue(rival["waited"])  # reclaims are serialized
        self.assertEqual(holders, [False, True])  # and exactly one process holds the lock

    def test_dead_lock_holders_are_detected(self):
        dead = subprocess.Popen([sys.executable, "-c", "pass"])
        dead.wait()
        self.assertTrue(bridge._process_alive(os.getpid()))
        self.assertFalse(bridge._process_alive(dead.pid))
        with mock.patch.object(bridge, "IS_WINDOWS", True):
            # Where the Windows API is missing (as here), the holder cannot be checked.
            self.assertIsNone(bridge._process_alive(dead.pid))

    def test_boot_time_is_the_recorded_boot(self):
        if sys.platform == "darwin":
            out = subprocess.run(["sysctl", "-n", "kern.boottime"], capture_output=True, text=True).stdout
            recorded = float(re.search(r"sec = (\d+)", out).group(1))
        elif sys.platform.startswith("linux"):
            with open("/proc/stat", encoding="ascii") as stream:
                recorded = float(next(line.split()[1] for line in stream if line.startswith("btime")))
        else:
            self.skipTest("the recorded boot time is checked on macOS and Linux")
        self.assertAlmostEqual(bridge._boot_time(), recorded, delta=2)

    def test_a_reused_pid_does_not_keep_a_dead_holders_lock(self):
        start = bridge._process_start(os.getpid())
        self.assertIsNotNone(start)
        self.assertEqual(bridge._process_start(os.getpid()), start)  # stable for one process
        path = Path(self.tmp.name) / "x.lock"
        with bridge.Lock(path, 60) as lock:
            self.assertEqual(len(path.read_text().split()), 3)  # pid, time and process start
            self.assertFalse(lock._stale(path.read_text()))
        lock = bridge.Lock(path, 60)
        # The pid is alive, but it now belongs to a process that started later: another holder.
        self.assertTrue(lock._stale(f"{os.getpid()} {time.time()} {start - 100}"))

    def test_live_owners_keep_their_locks_until_the_machine_restarts(self):
        lock = bridge.Lock(Path(self.tmp.name) / "x.lock", stale_seconds=1)
        boot = bridge._boot_time()
        self.assertGreater(boot, 0)
        # Past its age limit, but the holder is alive and the lock is from this boot.
        self.assertFalse(lock._stale(f"{os.getpid()} {(boot + time.time()) / 2}"))
        # Written before the last boot: that holder is gone, even if its pid is in use again.
        self.assertTrue(lock._stale(f"{os.getpid()} {boot - 3600}"))
        with mock.patch.object(bridge, "_process_alive", return_value=None):
            self.assertTrue(lock._stale(f"{os.getpid()} {time.time() - 5}"))  # unknown: by age

    def test_refresh_updates_every_topic_of_the_session(self):
        self.pair("p1")
        other = "01a1496a-0000-7000-8000-000000000009"
        self._write_rollout(other, busy=False)
        bridge.create_pair("p2", other, None, [], False)
        os.environ["CLAUDE_CODE_MESSAGING_SOCKET"] = "/tmp/new-socket"
        self.assertEqual(sorted(bridge.refresh_claude()), ["p1", "p2"])
        self.assertEqual({item["claude"]["socket"] for item in bridge.all_pairs()}, {"/tmp/new-socket"})

    def test_identifiers_cannot_leave_their_directories(self):
        self.pair("p1")
        message = bridge.queue_message("p1", "codex", "hello")
        self.assertEqual(bridge.find_message("p1", message["id"])["text"], "hello")
        for bad in ("../pair", "../../p1/pair", "*", "x/y"):
            with self.assertRaises(bridge.BridgeError):
                bridge.find_message("p1", bad)  # never another file, e.g. a binding
        self.assertIsNone(bridge.codex_rollout("../*"))

    def test_a_pair_that_vanishes_while_listing_is_skipped(self):
        self.pair("p1")
        other = "01a1496a-0000-7000-8000-000000000009"
        self._write_rollout(other, busy=False)
        bridge.create_pair("p2", other, None, [], False)
        read = bridge.read_json

        def vanishing(path):
            if path.parent.name == "p1":
                raise FileNotFoundError(path)  # renamed or removed after the directory listing
            return read(path)

        with mock.patch.object(bridge, "read_json", vanishing):
            self.assertEqual([item["name"] for item in bridge.all_pairs()], ["p2"])

    def test_a_relative_codex_cwd_is_stored_absolute(self):
        (self.workdir / "checkout").mkdir()
        pair = bridge.create_pair("p1", CODEX_ID, "checkout", [], False)  # relative to here
        self.assertEqual(pair["codex"]["cwd"], str((self.workdir / "checkout").resolve()))
        other = "01a1496a-0000-7000-8000-000000000009"
        self._write_rollout(other, busy=False)
        pair = bridge.create_pair("p2", other, "~", [], False)
        self.assertEqual(pair["codex"]["cwd"], str(Path.home().resolve()))

    def test_a_relative_bridge_home_means_one_directory(self):
        os.environ["AGENT_BRIDGE_HOME"] = "rel-home"
        expected = (self.workdir / "rel-home").resolve()
        self.assertEqual(bridge.home(), expected)
        self.pair("p1")
        # Workers and Codex processes get the absolute path, whatever their own directory.
        self.assertEqual(bridge._codex_env()["AGENT_BRIDGE_HOME"], str(expected))
        with mock.patch.object(bridge.subprocess, "Popen", wraps=subprocess.Popen) as popen:
            bridge.spawn_worker("p1").wait()
        self.assertEqual(popen.call_args.kwargs["env"]["AGENT_BRIDGE_HOME"], str(expected))

    def test_invalid_pair_name(self):
        with self.assertRaises(bridge.BridgeError):
            bridge.create_pair("../x", CODEX_ID, None, [], False)


class CodexToClaude(BridgeTestCase):
    def test_message_reaches_the_paired_claude_socket(self):
        self.pair()
        os.environ.pop("CLAUDE_CODE_SESSION_ID")  # the sender is Codex
        os.environ["CODEX_THREAD_ID"] = CODEX_ID  # from the paired thread's shell
        result = bridge.send("p1", "claude", "build finished, see report.md", None, False)
        self.assertEqual(result["status"], "delivered")
        text = self.inbox.texts(wait=2)[0]
        self.assertIn('Message from Codex session 01a1496a via pair "p1"', text)
        self.assertIn("build finished, see report.md", text)
        sent = list((bridge.pair_dir("p1") / "mail" / "sent").glob("*.json"))
        self.assertEqual(len(sent), 1)

    def test_unreachable_socket_keeps_the_message(self):
        self.pair()
        os.environ["CODEX_THREAD_ID"] = CODEX_ID  # the sender is the paired Codex thread
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
        os.environ["CODEX_THREAD_ID"] = CODEX_ID  # from the paired thread's shell
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

    def test_a_send_to_claude_reserves_the_topic_before_queueing(self):
        self.pair()
        os.environ.pop("CLAUDE_CODE_SESSION_ID")  # the sender is Codex
        os.environ["CODEX_THREAD_ID"] = CODEX_ID  # from the paired thread's shell
        write, outcome = bridge.write_json, []

        def racing_write(path, value):
            if path.parent.name == "inbox-claude" and not outcome:
                outcome.append(None)  # race once
                try:
                    bridge.rename_pair("p1", "p2")
                    outcome[0] = "renamed"
                except bridge.BridgeError:
                    outcome[0] = "refused"
            write(path, value)

        with mock.patch.object(bridge, "write_json", racing_write):
            result = bridge.send("p1", "claude", "build finished", None, False)
        self.assertEqual((outcome, result["status"]), (["refused"], "delivered"))

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

    def test_an_over_long_answer_still_reaches_claude(self):
        self.pair()
        answer = "y" * (bridge.MAX_TEXT_CHARS + 10)
        with mock.patch.dict(bridge.ROUTES, {"exec": lambda *args: answer}):
            result = bridge.send("p1", "codex", "write a lot", None, wait=True)
        reply = self.inbox.texts(wait=2)[0]
        saved = bridge.pair_dir("p1") / "mail" / "long" / f"{result['id']}.txt"
        self.assertIn(str(saved), reply)  # the beginning, and where the full answer is
        self.assertEqual(saved.read_text(encoding="utf-8"), answer)
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

    def test_a_turn_that_wrote_a_long_log_is_still_busy(self):
        self.pair()
        self._write_rollout(CODEX_ID, busy=True)
        filler = json.dumps({"type": "response_item", "payload": {
            "type": "message", "role": "assistant",
            "content": [{"type": "output_text", "text": "x" * 1000}]}})
        with open(bridge.codex_rollout(CODEX_ID), "a", encoding="utf-8") as stream:
            stream.write((filler + "\n") * 700)  # far more than one read window since the start
        self.assertTrue(bridge.codex_busy(CODEX_ID))
        bridge.queue_message("p1", "codex", "hello")
        bridge.deliver_to_codex("p1", busy_wait=0)
        self.assertEqual(self.codex_calls(), [])
        self.assertIn("stayed busy", self.inbox.texts(wait=2)[0])

    def test_codex_processes_do_not_inherit_the_claude_identity(self):
        self.pair()
        os.environ["CLAUDE_CODE_MESSAGING_TOKEN"] = "not-for-codex"
        bridge.send("p1", "codex", "hello", None, wait=True)  # exec
        os.environ["FAKE_CODEX_ACTIVE_WRITER"] = "1"
        bridge.send("p1", "codex", "again", None, wait=True)  # exec refused, then codex queue
        seen = [json.loads(line) for line in Path(str(self.record) + ".env").read_text().splitlines()]
        # Commands Codex runs could otherwise pass for this Claude session on another topic.
        self.assertEqual(seen, [[], [], []])

    def test_a_long_message_reaches_codex_through_stdin(self):
        self.pair()
        text = "z" * 150_000  # over Linux's 128 KB per-argument and Windows' 32 K limits
        bridge.send("p1", "codex", text, None, wait=True)
        raw = json.loads(Path(str(self.record) + ".raw").read_text().splitlines()[0])
        self.assertEqual(raw[-1], "-")  # the prompt stays off the command line
        self.assertIn(text, self.codex_calls()[0][-1])
        self.assertIn("FAKE ANSWER: " + text, self.inbox.texts(wait=5)[0])

    def test_dangerous_codex_arguments_are_dropped(self):
        self.pair(args=["--dangerously-bypass-approvals-and-sandbox", "-c", 'model="x"',
                        "-c", 'sandbox_mode="danger-full-access"', '--config=profile="yolo"',
                        "--config", 'sandbox_workspace_write.writable_roots=["/"]',
                        "-c", 'default_permissions=":danger-full-access"',
                        "--sandbox", "danger-full-access", "-s", "danger-full-access",
                        "--sandbox=danger-full-access", "-sdanger-full-access", "--full-auto",
                        "--approve-for-me", "--not-so-yolo", "--add-dir", "/", "-p", "yolo",
                        "-C", "/", "--cd=/", "-C/", "--ignore-rules", "--ignore-user-config",
                        "-c", 'notify=["touch", "/tmp/x"]', "-c", 'mcp_servers.x.command="y"',
                        "--enable", "x", "-m", "gpt-x", "--config=model_reasoning_effort=\"high\"",
                        "-c"])
        bridge.send("p1", "codex", "hello", None, wait=True)
        call = self.codex_calls()[0]
        extras = call[call.index("-o") + 2:-1]
        # Only arguments that choose the model survive; the permission profile is pinned too,
        # since it wins over sandbox_mode in Codex 0.138+.
        self.assertEqual(extras, ["-c", 'model="x"', "-m", "gpt-x",
                                  "-c", 'model_reasoning_effort="high"',
                                  "-c", 'default_permissions=":read-only"',
                                  "-c", 'sandbox_mode="read-only"'])

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

    def test_a_sender_without_a_session_identity_is_refused(self):
        self.pair()
        del os.environ["CLAUDE_CODE_SESSION_ID"]  # a plain shell, not the paired Claude session
        with self.assertRaises(bridge.BridgeError):
            bridge.send("p1", "codex", "hello", None, wait=True)
        with self.assertRaises(bridge.BridgeError):
            bridge.send("p1", "claude", "hello", None, False)  # nor visibly the paired thread
        self.assertEqual(self.codex_calls(), [])
        self.assertEqual(self.inbox.texts(), [])

    def test_other_claude_session_is_refused(self):
        self.pair()
        os.environ["CLAUDE_CODE_SESSION_ID"] = "not-the-paired-session"
        with self.assertRaises(bridge.BridgeError):
            bridge.send("p1", "codex", "hello", None, wait=True)

    def test_a_message_queued_during_the_lock_handoff_is_delivered(self):
        self.pair()
        bridge.queue_message("p1", "codex", "first")
        release, raced = bridge.Lock.__exit__, []

        def racing_release(lock, *exc):
            if lock.path.name == "codex.lock" and not raced:
                raced.append(True)
                # A send queues now; its worker still finds the lock taken and leaves.
                bridge.queue_message("p1", "codex", "second")
                self.assertEqual(bridge.deliver_to_codex("p1"), [])
            return release(lock, *exc)

        with mock.patch.object(bridge.Lock, "__exit__", racing_release):
            processed = bridge.deliver_to_codex("p1")
        self.assertEqual(len(processed), 2)
        self.assertEqual(bridge.pending("p1", "codex"), [])

    def test_wait_lasts_while_the_messages_ahead_keep_moving(self):
        self.pair()
        lock = bridge.pair_dir("p1") / "codex.lock"
        lock.write_text(f"{os.getpid()} {time.time()}")  # another live worker owns the pair
        ahead = [bridge.queue_message("p1", "codex", f"earlier {index}") for index in range(3)]

        def worker():  # delivers the messages ahead one by one, each within one bound
            for message in ahead:
                time.sleep(0.8)
                bridge.archive("p1", message, "sent")
            time.sleep(0.8)
            lock.unlink(missing_ok=True)

        threading.Thread(target=worker, daemon=True).start()
        with mock.patch.object(bridge, "WAIT_PER_MESSAGE_SECONDS", 1.5):
            result = bridge.send("p1", "codex", "mine", None, wait=True)  # waits ~3.2 s in all
        self.assertTrue((bridge.pair_dir("p1") / "mail" / "sent" / f"{result['id']}.json").exists())

    def test_wait_waits_for_a_worker_that_holds_the_lock(self):
        self.pair()
        lock = bridge.pair_dir("p1") / "codex.lock"
        lock.write_text(f"{os.getpid()} {time.time()}")  # another live worker owns the pair
        threading.Timer(1.0, lambda: lock.unlink(missing_ok=True)).start()
        started = time.time()
        result = bridge.send("p1", "codex", "hello", None, wait=True)
        self.assertGreater(time.time() - started, 0.9)
        self.assertEqual(bridge.pending("p1", "codex"), [])
        self.assertTrue((bridge.pair_dir("p1") / "mail" / "sent" / f"{result['id']}.json").exists())

    def test_a_spawned_worker_reserves_the_topic_until_it_exits(self):
        self.pair()
        bridge.send("p1", "codex", "async check", None, wait=False)
        with self.assertRaises(bridge.BridgeError):
            bridge.rename_pair("p1", "p2")  # the worker may not hold codex.lock yet
        deadline = time.time() + 30
        while bridge._delivery_in_progress(bridge.pair_dir("p1")) and time.time() < deadline:
            time.sleep(0.1)
        self.assertIn("FAKE ANSWER: async check", self.inbox.texts(wait=5)[0])
        self.assertEqual(list(bridge.pair_dir("p1").glob("worker-*.lock")), [])
        self.assertEqual(bridge.rename_pair("p1", "p2")["name"], "p2")

    def test_a_send_reserves_the_topic_before_queueing(self):
        self.pair()
        write, outcome = bridge.write_json, []

        def racing_write(path, value):
            if path.parent.name == "inbox-codex" and not outcome:
                outcome.append(None)  # race once
                # A rename runs while the message is being written, before any worker exists.
                try:
                    bridge.rename_pair("p1", "p2")
                    outcome[0] = "renamed"
                except bridge.BridgeError:
                    outcome[0] = "refused"
            write(path, value)

        with mock.patch.object(bridge, "write_json", racing_write):
            bridge.send("p1", "codex", "async check", None, wait=False)
        self.assertEqual(outcome, ["refused"])
        self.assertIn("FAKE ANSWER: async check", self.inbox.texts(wait=15)[0])

    def test_watch_survives_a_topic_that_moves_away(self):
        self.pair()
        message = bridge.queue_message("p1", "codex", "hello")
        listed = bridge.all_pairs()
        bridge.remove_pair("p1")  # moved after watch listed it and saw the pending message
        with mock.patch.object(bridge, "all_pairs", lambda: listed), \
                mock.patch.object(bridge, "pending", lambda name, to: [message] if to == "codex" else []), \
                mock.patch.object(bridge, "spawn_worker", side_effect=AssertionError("spawned")):
            bridge.watch_once()  # neither raises nor spawns a worker for the old name

    def test_a_manual_deliver_reserves_the_topic(self):
        self.pair()
        outcome, deliver = [], bridge.deliver_to_codex

        def racing_deliver(name, *args, **kwargs):
            try:
                bridge.rename_pair("p1", "p2")  # a rename while `deliver p1` runs
                outcome.append("renamed")
            except bridge.BridgeError:
                outcome.append("refused")
            return deliver(name, *args, **kwargs)

        with mock.patch.object(bridge, "deliver_to_codex", racing_deliver), \
                contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(bridge.main(["deliver", "p1"]), 0)
        self.assertEqual(outcome, ["refused"])

    def test_watch_reserves_the_topic_while_delivering_to_claude(self):
        self.pair()
        os.environ["CODEX_THREAD_ID"] = CODEX_ID  # from the paired thread's shell
        bridge.queue_message("p1", "claude", "hello")  # waits for watch to deliver it
        outcome, deliver = [], bridge.deliver_to_claude

        def racing_deliver(name):
            try:
                bridge.rename_pair("p1", "p2")  # a rename while watch delivers to Claude
                outcome.append("renamed")
            except bridge.BridgeError:
                outcome.append("refused")
            return deliver(name)

        with mock.patch.object(bridge, "deliver_to_claude", racing_deliver):
            bridge.watch_once()
        self.assertEqual(outcome, ["refused"])
        self.assertIn("hello", self.inbox.texts(wait=2)[0])

    def test_a_send_delivers_here_when_no_worker_can_start(self):
        self.pair()
        with mock.patch.object(bridge, "spawn_worker", side_effect=OSError("no processes left")):
            result = bridge.send("p1", "codex", "check this", None, wait=False)
        self.assertIn("delivered", result["status"])
        self.assertEqual(bridge.pending("p1", "codex"), [])  # not left for a retry to duplicate
        self.assertIn("FAKE ANSWER: check this", self.inbox.texts(wait=2)[0])

    def test_a_killed_worker_is_reaped_at_once(self):
        self.pair()
        worker = bridge.spawn_worker("p1", ["watch", "--interval", "60"])  # runs until killed
        worker.kill()  # killed, it cannot remove its own marker
        deadline = time.time() + 5
        while bridge._process_alive(worker.pid) and time.time() < deadline:
            time.sleep(0.05)
        self.assertFalse(bridge._process_alive(worker.pid))  # reaped, not left as a zombie
        self.assertFalse(bridge._delivery_in_progress(bridge.pair_dir("p1")))  # marker reclaimed

    def test_finished_workers_are_reaped(self):
        self.pair()
        first = bridge.spawn_worker("p1")  # nothing is queued, so it exits at once
        deadline = time.time() + 15
        while time.time() < deadline:
            bridge._reap_workers()
            if first.returncode is not None:
                break
            time.sleep(0.1)
        self.assertIsNotNone(first.returncode)  # reaped, not left as a zombie
        self.assertNotIn(first, bridge._WORKERS)

    def test_detached_worker_delivers_the_reply(self):
        self.pair()
        result = bridge.send("p1", "codex", "async check", None, wait=False)
        self.assertIn("queued", result["status"])
        texts = self.inbox.texts(wait=15)
        self.assertTrue(texts, "no reply from the detached worker")
        self.assertIn("FAKE ANSWER: async check", texts[0])


class Invocation(unittest.TestCase):
    def test_runs_with_a_plain_python_interpreter(self):
        # The documented fallback when uv is absent: any Python 3.10+, standard library only.
        result = subprocess.run([sys.executable, "-I", str(HERE / "agent_bridge.py"), "--version"],
                                capture_output=True, text=True, timeout=30)
        self.assertEqual((result.returncode, result.stdout.strip()), (0, bridge.VERSION))


class ShellCommands(unittest.TestCase):
    def test_codex_on_windows_gets_a_powershell_command(self):
        python = "C:\\Program Files\\It's\\python.exe"
        with mock.patch.object(bridge, "IS_WINDOWS", True), \
                mock.patch.object(bridge.sys, "executable", python):
            for_codex = bridge.bridge_invocation(for_codex=True)
            for_claude = bridge.bridge_invocation()
        # PowerShell, Codex's shell on Windows, runs a quoted path only with the call operator.
        self.assertTrue(for_codex.startswith("& 'C:\\Program Files\\It''s\\python.exe' -I '"),
                        for_codex)
        # Claude Code's Bash tool is a POSIX shell, also on Windows (Git Bash).
        self.assertTrue(for_claude.startswith(
            "'C:\\Program Files\\It'\"'\"'s\\python.exe' -I "), for_claude)

    def test_posix_commands_are_shell_quoted(self):
        with mock.patch.object(bridge, "IS_WINDOWS", False):
            self.assertEqual(bridge.bridge_invocation(for_codex=True), bridge.bridge_invocation())


class Limits(BridgeTestCase):
    def test_size_and_rate_limits(self):
        self.pair()
        with self.assertRaises(bridge.BridgeError):
            bridge.queue_message("p1", "codex", "x" * (bridge.MAX_TEXT_CHARS + 1))
        for _ in range(bridge.RATE_LIMIT):
            bridge.queue_message("p1", "codex", "ping")
        with self.assertRaises(bridge.BridgeError):
            bridge.queue_message("p1", "codex", "one too many")

    def test_reply_to_must_be_a_message_id(self):
        self.pair()
        for bad in ("../x", "a\nb", "x" * 1000):
            with self.assertRaises(bridge.BridgeError):
                bridge.queue_message("p1", "codex", "hello", reply_to=bad)
        first = bridge.queue_message("p1", "codex", "hello")
        self.assertEqual(bridge.queue_message("p1", "codex", "again", reply_to=first["id"])["reply_to"],
                         first["id"])

    def test_concurrent_sends_respect_the_rate_limit(self):
        self.pair()
        check, results = bridge.check_rate, []

        def slow_check(name, sender):
            check(name, sender)
            time.sleep(0.05)  # widen the gap between the check and the queued event

        def send():
            try:
                bridge.queue_message("p1", "codex", "ping")
                results.append(True)
            except bridge.BridgeError:
                results.append(False)

        with mock.patch.object(bridge, "check_rate", slow_check):
            threads = [threading.Thread(target=send) for _ in range(bridge.RATE_LIMIT + 5)]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join()
        self.assertEqual(results.count(True), bridge.RATE_LIMIT)


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
        self.assertEqual(resume["cwd"], bridge.load_pair("p1")["codex"]["cwd"])  # as for exec
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
        self.assertNotIn("cwd", resume)

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

    def test_queue_answer_follows_the_log_as_it_grows(self):
        rollout = bridge.codex_rollout(CODEX_ID)
        rows = [{"type": "response_item", "payload": {"type": "message", "role": "user",
                                                       "content": [{"type": "input_text", "text": "MARK-1"}]}},
                {"type": "response_item", "payload": {"type": "message", "role": "assistant",
                                                       "content": [{"type": "output_text", "text": "the answer"}]}},
                {"type": "event_msg", "payload": {"type": "task_complete"}}]
        text = "".join(json.dumps(row) + "\n" for row in rows)

        def append():
            for piece in (text[:40], text[40:-30], text[-30:]):  # records split mid-line
                time.sleep(0.3)
                with open(rollout, "a", encoding="utf-8") as stream:
                    stream.write(piece)

        threading.Thread(target=append, daemon=True).start()
        self.assertEqual(bridge.watch_thread_answer(CODEX_ID, "MARK-1", 10, poll=0.1), "the answer")

    def test_queue_answer_is_read_from_after_the_enqueue_point(self):
        rollout = bridge.codex_rollout(CODEX_ID)

        def turn(answer):
            rows = [{"type": "response_item", "payload": {"type": "message", "role": "user",
                                                           "content": [{"type": "input_text", "text": "MARK-1"}]}},
                    {"type": "response_item", "payload": {"type": "message", "role": "assistant",
                                                           "content": [{"type": "output_text", "text": answer}]}},
                    {"type": "event_msg", "payload": {"type": "task_complete"}}]
            with open(rollout, "a", encoding="utf-8") as stream:
                stream.write("".join(json.dumps(row) + "\n" for row in rows))

        turn("an older turn")
        start = rollout.stat().st_size  # where the log ended when the message was queued
        turn("the queued turn")
        self.assertEqual(bridge.watch_thread_answer(CODEX_ID, "MARK-1", 5, poll=0.1, start=start),
                         "the queued turn")

    def test_a_message_too_long_for_the_desktop_queue_is_refused_clearly(self):
        self.pair()
        os.environ["FAKE_CODEX_ACTIVE_WRITER"] = "1"  # exec sees the desktop app: queue route
        with mock.patch.object(bridge, "QUEUE_PROMPT_LIMIT", 2000):
            result = bridge.send("p1", "codex", "w" * 3000, None, wait=True)
        self.assertEqual([call[0] for call in self.codex_calls()], ["exec"])  # no codex queue
        notice = self.inbox.texts(wait=2)[0]
        self.assertIn(f"Delivery to Codex failed for message {result['id']}", notice)
        self.assertIn("too long for the Codex desktop queue", notice)

    def test_the_windows_queue_limit_counts_the_escaped_command_line(self):
        prompt = '"' * 20_000  # each quote is escaped on a Windows command line
        command = ["codex", "queue", "--thread", CODEX_ID, "--message", prompt]
        with mock.patch.object(bridge, "IS_WINDOWS", True):
            self.assertGreater(bridge._queue_command_size(command, prompt), 40_000)
        with mock.patch.object(bridge, "IS_WINDOWS", False):
            self.assertEqual(bridge._queue_command_size(command, prompt), 20_000)

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
        notice = self.inbox.texts(wait=2, count=2)[1]
        self.assertIn(f"Status of message {second['id']}", notice)  # unknown, not failed
        self.assertIn("may have started", notice)

    def test_a_daemon_that_rejects_a_request_before_the_turn_falls_back(self):
        self.start_daemon(fail_methods={"thread/resume"})
        self.pair()
        bridge.send("p1", "codex", "hello", None, wait=True)
        self.assertEqual(self.codex_calls()[0][:2], ["exec", "resume"])  # nothing had started
        self.assertIn("(delivered by exec)", self.inbox.texts(wait=2)[0])

    def test_a_daemon_that_stops_answering_before_the_turn_falls_back(self):
        self.start_daemon(silent_methods={"thread/resume"})
        self.pair()
        with mock.patch.object(bridge.codex_daemon, "REQUEST_TIMEOUT", 1):
            bridge.send("p1", "codex", "hello", None, wait=True)
        self.assertEqual(self.codex_calls()[0][:2], ["exec", "resume"])  # nothing had started
        self.assertIn("(delivered by exec)", self.inbox.texts(wait=2)[0])

    def test_a_daemon_that_refuses_initialize_is_disconnected(self):
        daemon = self.start_daemon(fail_methods={"initialize"})
        with self.assertRaises(bridge.codex_daemon.DaemonError):
            bridge.codex_daemon.Daemon(self.daemon_path)
        deadline = time.time() + 2
        while daemon.clients and time.time() < deadline:
            time.sleep(0.05)
        self.assertEqual(daemon.clients, [])  # no reader thread keeps the socket open

    def test_a_turn_whose_connection_drops_is_followed_to_its_answer(self):
        self.start_daemon(turn_seconds=1.5, drop_after_start=True)
        self.pair()
        bridge.send("p1", "codex", "long job", None, wait=True)
        reply = self.inbox.texts(wait=5)[0]
        self.assertIn("DAEMON ANSWER: long job", reply)  # followed on a new connection
        self.assertEqual(self.codex_calls(), [])  # and never sent a second time

    def test_an_unconfirmed_turn_start_is_reported_as_unknown(self):
        self.start_daemon(silent_methods={"turn/start"})
        self.pair()
        with mock.patch.object(bridge.codex_daemon, "REQUEST_TIMEOUT", 0.5):
            result = bridge.send("p1", "codex", "maybe started", None, wait=True)
        self.assertEqual(self.codex_calls(), [])  # no second attempt by exec
        self.assertTrue((bridge.pair_dir("p1") / "mail" / "sent" / f"{result['id']}.json").exists())
        notice = self.inbox.texts(wait=2)[0]
        self.assertIn("may have started", notice)
        self.assertNotIn("Delivery to Codex failed", notice)

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
        # Only the delivering worker reserves the topic; the steer left no reservation behind.
        self.assertEqual(len(list(bridge.pair_dir("p1").glob("worker-*.lock"))), 1)
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
        # Wait for the worker itself (it removes its marker last), not for a fixed delay.
        deadline = time.time() + 60
        while bridge._delivery_in_progress(bridge.pair_dir("p1")) and time.time() < deadline:
            time.sleep(0.1)
        log = bridge.pair_dir("p1") / "worker.log"
        texts = self.inbox.texts(wait=5)
        self.assertTrue(texts, log.read_text() if log.exists() else "no worker log")
        self.assertIn("STEERED", texts[0])
        self.assertEqual(bridge.rename_pair("p1", "p2")["name"], "p2")

    def test_a_steer_waits_here_when_no_worker_can_start(self):
        self.start_daemon(loaded=True, user_turn_seconds=1.5)
        self.pair()
        with mock.patch.object(bridge, "spawn_worker", side_effect=OSError("no processes left")):
            result = bridge.send("p1", "codex", "also check X", None, False, steer=True)
        self.assertIn("delivered", result["status"])
        reply = self.inbox.texts(wait=2)[0]  # already posted when the send returned
        self.assertIn("STEERED", reply)
        self.assertIn(f"in reply to {result['id']}", reply)

    def test_a_steered_turn_that_does_not_complete_is_a_failure(self):
        self.start_daemon(loaded=True, user_turn_seconds=1.5, user_turn_status="interrupted")
        self.pair()
        result = bridge.send("p1", "codex", "also check X", None, False, steer=True)
        notice = self.inbox.texts(wait=10)[0]
        self.assertIn(f"Delivery to Codex failed for message {result['id']}", notice)
        self.assertIn("interrupted", notice)  # not passed off as the turn's final answer

    def test_an_unconfirmed_steer_is_still_followed(self):
        self.start_daemon(loaded=True, user_turn_seconds=2, silent_methods={"turn/steer"})
        self.pair()
        with mock.patch.object(bridge.codex_daemon, "REQUEST_TIMEOUT", 0.5):
            result = bridge.send("p1", "codex", "also check X", None, False, steer=True)
        self.assertIn("did not confirm", result["status"])  # it may be in the turn: no retry
        self.assertTrue((bridge.pair_dir("p1") / "mail" / "sent" / f"{result['id']}.json").exists())
        self.assertIn(f"in reply to {result['id']}", self.inbox.texts(wait=10)[0])

    def test_a_steer_carries_the_protocol_when_it_is_due(self):
        daemon = self.start_daemon(loaded=True, user_turn_seconds=3)
        self.pair()  # no handshake yet: the next message has to carry the protocol
        bridge.send("p1", "codex", "first steer", None, False, steer=True)
        bridge.send("p1", "codex", "second steer", None, False, steer=True)
        steers = [params["input"][0]["text"] for method, params in daemon.calls
                  if method == "turn/steer"]
        self.assertIn("[agent-bridge protocol]", steers[0])
        self.assertNotIn("[agent-bridge protocol]", steers[1])  # recorded as delivered
        self.inbox.texts(wait=10, count=2)  # let both await-turn workers finish

    def test_steer_needs_an_active_turn(self):
        self.start_daemon(loaded=True)
        self.pair()
        with self.assertRaises(bridge.BridgeError):
            bridge.send("p1", "codex", "steer nothing", None, False, steer=True)
        self.assertEqual(len(list((bridge.pair_dir("p1") / "mail" / "failed").glob("*.json"))), 1)
        self.assertEqual(bridge.pending("p1", "codex"), [])  # a refused steer is not sent later
        self.assertEqual(list(bridge.pair_dir("p1").glob("worker-*.lock")), [])

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
        os.environ["CODEX_THREAD_ID"] = CODEX_ID  # from the paired thread's shell
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

    def test_a_renamed_topic_resends_the_protocol(self):
        self.pair("p1")
        bridge.send("p1", "codex", "first", None, wait=True)
        bridge.rename_pair("p1", "p2")
        bridge.send("p2", "codex", "second", None, wait=True)
        prompt = self.last_prompt()
        self.assertIn("[agent-bridge protocol]", prompt)  # the thread learns the new name
        self.assertIn("send p2 --to claude", prompt)

    def test_compaction_check_reads_only_since_the_cutoff(self):
        old = {"timestamp": "2026-10-17T09:00:00.000Z", "type": "compacted", "payload": {}}
        new = {"timestamp": "2026-10-17T11:00:00.000Z", "type": "event_msg", "payload": {}}
        with open(bridge.codex_rollout(CODEX_ID), "a", encoding="utf-8") as stream:
            stream.write((json.dumps(old) + "\n") * 5000 + json.dumps(new) + "\n")
        seen, lines = [], bridge._lines_backwards

        def counting(path, *args):
            for line in lines(path, *args):
                seen.append(line)
                yield line

        with mock.patch.object(bridge, "_lines_backwards", counting):
            self.assertFalse(bridge.compacted_since(CODEX_ID, "2026-10-17T10:00:00+00:00"))
        self.assertTrue(0 < len(seen) < 10, len(seen))  # stopped at the first older record

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
        send = by_id[2]["result"]["tools"][1]["inputSchema"]  # schema keywords at the top level
        self.assertEqual((sorted(send["properties"]), send["required"], send["additionalProperties"]),
                         (["after_turn", "pair", "reply_to", "text"], ["pair", "text"], False))
        self.assertFalse(by_id[3]["result"]["isError"], by_id[3])
        self.assertIn("via mcp", self.inbox.texts(wait=2)[0])
        self.assertTrue(by_id[4]["result"]["isError"])
        self.assertEqual(by_id[5]["error"]["code"], -32601)

    def test_malformed_requests_do_not_stop_the_server(self):
        self.pair()
        responses = self.rpc([
            [],
            {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": []},
            {"jsonrpc": "2.0", "id": 2, "method": "tools/call",
             "params": {"name": "bridge_send", "arguments": ["p1"]}},
            {"jsonrpc": "2.0", "id": 3, "method": "tools/call",
             "params": {"name": "bridge_send", "arguments": {"pair": "p1", "text": 5}}},
            {"jsonrpc": "2.0", "id": 4, "method": "tools/list"},
        ], "--side", "codex", "--pair", "p1")
        self.assertEqual(responses[0]["error"]["code"], -32600)
        by_id = {response["id"]: response for response in responses[1:]}
        self.assertEqual([by_id[1]["error"]["code"], by_id[2]["error"]["code"]], [-32602, -32602])
        self.assertTrue(by_id[3]["result"]["isError"])
        self.assertEqual(len(by_id[4]["result"]["tools"]), 4)  # still serving


if __name__ == "__main__":
    unittest.main(verbosity=2)
