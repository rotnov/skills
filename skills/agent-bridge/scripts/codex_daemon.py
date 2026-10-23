"""Client for the local Codex app-server daemon (standard library only).

The daemon (`codex app-server --listen unix:// --managed-daemon`) serves JSON-RPC
over a WebSocket carried by its Unix control socket
(~/.codex/app-server-control/app-server-control.sock). Threads it hosts accept
`turn/start` and `turn/steer`. A thread hosted by another app-server, such as the
Codex desktop app's own, makes `thread/resume` fail with "active writer".
The protocol is marked experimental by Codex and may change.
"""

from __future__ import annotations

import base64
import json
import os
import queue
import socket
import struct
import threading
import time
from pathlib import Path

CLIENT = {"name": "agent-bridge", "version": "0.2.0"}
# Codex 0.138+ permission profiles; a selected profile wins over the legacy sandbox mode.
PERMISSION_PROFILES = {"read-only": ":read-only", "workspace-write": ":workspace"}
REQUEST_TIMEOUT = 60  # seconds to wait for one JSON-RPC answer


class DaemonError(Exception):
    """A daemon request failed; the message is safe to report."""


class DaemonUnavailable(DaemonError):
    """The daemon cannot be used before any turn was requested; another route is safe."""


class DaemonUncertain(DaemonError):
    """The connection failed after a turn or steer was requested, so it may have taken effect."""

    turn_id: str | None = None  # the turn a steer was aimed at, when known


class TurnStillRunning(DaemonUncertain):
    """The turn was still running when the wait for it ended; its answer may come later."""


class ActiveWriter(DaemonError):
    """Another app-server (for example the desktop app) hosts the thread."""


def socket_path() -> str | None:
    configured = os.environ.get("AGENT_BRIDGE_CODEX_SOCKET")
    path = Path(configured).expanduser() if configured else (
        Path(os.environ.get("CODEX_HOME", "~/.codex")).expanduser()
        / "app-server-control" / "app-server-control.sock")
    resolved = os.path.realpath(path)
    return resolved if os.path.exists(resolved) else None


def is_active_writer(text: str) -> bool:
    return "active writer" in text.lower()


class UnixWebSocket:
    """Minimal RFC 6455 client: masked text frames out, fragments, ping/pong, close."""

    def __init__(self, path: str, timeout: float = 10.0):
        self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.sock.settimeout(timeout)
        self.sock.connect(path)
        key = base64.b64encode(os.urandom(16)).decode()
        self.sock.sendall((f"GET / HTTP/1.1\r\nHost: localhost\r\nUpgrade: websocket\r\n"
                           f"Connection: Upgrade\r\nSec-WebSocket-Key: {key}\r\n"
                           f"Sec-WebSocket-Version: 13\r\n\r\n").encode())
        head = b""
        while b"\r\n\r\n" not in head:
            chunk = self.sock.recv(1)
            if not chunk:
                raise ConnectionError("daemon closed the handshake")
            head += chunk
        if b" 101 " not in head.split(b"\r\n")[0]:
            raise ConnectionError(head.split(b"\r\n")[0].decode(errors="replace"))
        self.sock.settimeout(None)
        self.lock = threading.Lock()

    def _exact(self, count: int) -> bytes:
        data = b""
        while len(data) < count:
            chunk = self.sock.recv(count - len(data))
            if not chunk:
                raise ConnectionError("daemon closed the connection")
            data += chunk
        return data

    def send_frame(self, opcode: int, payload: bytes) -> None:
        mask = os.urandom(4)
        length = len(payload)
        header = bytes([0x80 | opcode])
        if length < 126:
            header += bytes([0x80 | length])
        elif length < 65536:
            header += bytes([0x80 | 126]) + struct.pack(">H", length)
        else:
            header += bytes([0x80 | 127]) + struct.pack(">Q", length)
        masked = bytes(byte ^ mask[index % 4] for index, byte in enumerate(payload))
        with self.lock:
            self.sock.sendall(header + mask + masked)

    def send_text(self, text: str) -> None:
        self.send_frame(0x1, text.encode("utf-8"))

    def messages(self):
        buffer = b""
        while True:
            first, second = self._exact(2)
            opcode, length = first & 0x0F, second & 0x7F
            if length == 126:
                length = struct.unpack(">H", self._exact(2))[0]
            elif length == 127:
                length = struct.unpack(">Q", self._exact(8))[0]
            mask = self._exact(4) if second & 0x80 else None
            payload = self._exact(length)
            if mask:
                payload = bytes(byte ^ mask[index % 4] for index, byte in enumerate(payload))
            if opcode == 0x9:
                self.send_frame(0xA, payload)
            elif opcode == 0x8:
                return
            elif opcode in (0x0, 0x1, 0x2):
                buffer += payload
                if first & 0x80:
                    yield buffer.decode("utf-8")
                    buffer = b""

    def close(self) -> None:
        try:
            self.send_frame(0x8, b"")
        except OSError:
            pass
        self.sock.close()


class Daemon:
    """One JSON-RPC session with the daemon; tracks agent messages and finished turns."""

    def __init__(self, path: str | None = None):
        path = path or socket_path()
        if not path:
            raise DaemonUnavailable("no Codex app-server daemon socket")
        try:
            self.connection = UnixWebSocket(path)
        except OSError as error:
            raise DaemonUnavailable(f"daemon socket unreachable: {error}") from error
        self.inbox: queue.Queue = queue.Queue()
        self.next_id = 0
        self.turn_requested = False  # once set, a lost connection may have started a turn
        self.agent_text: dict[str, list[str]] = {}
        self.finished: dict[str, str] = {}
        self.started: set[str] = set()
        threading.Thread(target=self._read, daemon=True).start()
        try:
            self.request("initialize", {"clientInfo": CLIENT})
            self.notify("initialized")
        except DaemonError:
            self.close()  # the reader thread ends with the socket instead of leaking it
            raise

    def _read(self) -> None:
        try:
            for text in self.connection.messages():
                self.inbox.put(json.loads(text))
        except (OSError, ConnectionError, ValueError):
            pass
        self.inbox.put(None)

    def _lost(self, what: str) -> DaemonError:
        kind = DaemonUncertain if self.turn_requested else DaemonUnavailable
        return kind(f"{what}: daemon connection lost")

    def send(self, message: dict) -> None:
        try:
            self.connection.send_text(json.dumps({"jsonrpc": "2.0", **message}))
        except OSError as error:
            raise self._lost(message.get("method") or "reply") from error

    def notify(self, method: str, params: dict | None = None) -> None:
        self.send({"method": method, **({"params": params} if params is not None else {})})

    def _handle(self, message: dict) -> None:
        method = message.get("method")
        if not method:
            return
        params = message.get("params") or {}
        if "id" in message:
            # Approvals and other server requests are never granted by the bridge.
            self.send({"id": message["id"],
                       "error": {"code": -32601, "message": "agent-bridge declines server requests"}})
            return
        item = params.get("item") or {}
        if method == "turn/started":
            self.started.add((params.get("turn") or {}).get("id") or "")
        elif method == "item/completed" and item.get("type") == "agentMessage":
            self.agent_text.setdefault(params.get("turnId") or "", []).append(item.get("text", ""))
        elif method == "turn/completed":
            turn = params.get("turn") or {}
            self.finished[turn.get("id") or ""] = turn.get("status", "completed")

    def request(self, method: str, params: dict, timeout: float | None = None) -> dict:
        timeout = REQUEST_TIMEOUT if timeout is None else timeout
        self.next_id += 1
        identifier = self.next_id
        if method in ("turn/start", "turn/steer"):
            self.turn_requested = True
        self.send({"id": identifier, "method": method, "params": params})
        deadline = time.time() + timeout
        while time.time() < deadline:
            try:
                message = self.inbox.get(timeout=1)
            except queue.Empty:
                continue
            if message is None:
                raise self._lost(method)
            if message.get("id") == identifier and "method" not in message:
                if "error" in message:
                    text = str(message["error"].get("message", message["error"]))
                    if is_active_writer(text):
                        raise ActiveWriter(f"{method}: {text}")
                    # Refused before any turn was requested: another route is still safe.
                    raise (DaemonError if self.turn_requested else DaemonUnavailable)(
                        f"{method}: {text}")
                return message.get("result") or {}
            self._handle(message)
        # Unanswered before any turn was requested: another route is still safe; afterwards the
        # request may have taken effect.
        raise (DaemonUncertain if self.turn_requested else DaemonUnavailable)(
            f"{method}: no answer within {timeout:g}s")

    def pump(self, until, timeout: float) -> bool:
        deadline = time.time() + timeout
        while time.time() < deadline:
            if until():
                return True
            try:
                message = self.inbox.get(timeout=1)
            except queue.Empty:
                continue
            if message is None:
                raise self._lost("waiting")
            self._handle(message)
        return until()

    def close(self) -> None:
        self.connection.close()

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()

    # ------------------------------------------------------------ thread helpers

    def loaded(self, thread_id: str) -> bool:
        cursor, seen = None, []
        while True:
            page = self.request("thread/loaded/list", {"cursor": cursor} if cursor else {})
            seen.extend(page.get("data") or [])
            cursor = page.get("nextCursor")
            if not cursor:
                return thread_id in seen

    def subscribe(self, thread_id: str, sandbox: str | None, cwd: str | None = None) -> bool:
        """Subscribe to the thread; load it with bridge settings only if nobody hosts it yet."""
        if self.loaded(thread_id):
            self.request("thread/resume", {"threadId": thread_id, "excludeTurns": True})
            return False
        sandbox = sandbox if sandbox in PERMISSION_PROFILES else "read-only"
        params = {"threadId": thread_id, "excludeTurns": True, "approvalPolicy": "never",
                  "sandbox": sandbox,
                  "config": {"default_permissions": PERMISSION_PROFILES[sandbox]}}
        if cwd:
            params["cwd"] = cwd  # the same working directory the exec route uses
        self.request("thread/resume", params)
        return True

    def active_turn(self, thread_id: str) -> str | None:
        page = self.request("thread/turns/list", {"threadId": thread_id, "limit": 1,
                                                  "sortDirection": "desc", "itemsView": "notLoaded"})
        turns = page.get("data") or []
        if turns and turns[0].get("status") == "inProgress":
            return turns[0].get("id")
        return None

    def turn_answer(self, thread_id: str, turn_id: str) -> tuple[str | None, str]:
        """Status and final agent message of a turn, from events or from the thread history."""
        if turn_id in self.finished:
            texts = self.agent_text.get(turn_id) or []
            return self.finished[turn_id], texts[-1] if texts else ""
        page = self.request("thread/turns/list", {"threadId": thread_id, "limit": 5,
                                                  "sortDirection": "desc", "itemsView": "full"})
        for turn in page.get("data") or []:
            if turn.get("id") == turn_id and turn.get("status") != "inProgress":
                texts = [item.get("text", "") for item in turn.get("items") or []
                         if item.get("type") == "agentMessage"]
                return turn.get("status"), texts[-1] if texts else ""
        return None, ""


def text_input(text: str) -> list[dict]:
    return [{"type": "text", "text": text, "text_elements": []}]


def run_turn(thread_id: str, prompt: str, sandbox: str | None, timeout: float,
             busy_wait: float, on_started=None, path: str | None = None,
             cwd: str | None = None) -> tuple[str, str]:
    """Start a turn (waiting while another turn runs) and return (status, final answer)."""
    with Daemon(path) as daemon:
        daemon.subscribe(thread_id, sandbox, cwd)
        try:
            deadline = time.time() + busy_wait
            while daemon.active_turn(thread_id):
                if time.time() >= deadline:
                    raise DaemonError("the Codex thread stayed busy; message not started")
                daemon.pump(lambda: False, timeout=3)
            started = daemon.request("turn/start", {"threadId": thread_id,
                                                    "input": text_input(prompt)})
            turn_id = started["turn"]["id"]
            if on_started:
                on_started(turn_id)
            daemon.pump(lambda: turn_id in daemon.finished, timeout=timeout)
            status, answer = daemon.turn_answer(thread_id, turn_id)
            if status is None:
                raise TurnStillRunning(f"turn {turn_id} still running after {timeout:g}s")
            return status, answer
        finally:
            try:
                daemon.request("thread/unsubscribe", {"threadId": thread_id}, timeout=15)
            except DaemonError:
                pass


def steer(thread_id: str, text: str, path: str | None = None) -> str:
    """Add input to the thread's active turn; returns the steered turn id."""
    with Daemon(path) as daemon:
        if not daemon.loaded(thread_id):
            raise DaemonError("steer needs a thread hosted by the daemon (it is not loaded there)")
        daemon.subscribe(thread_id, None)
        try:
            turn_id = daemon.active_turn(thread_id)
            if not turn_id:
                raise DaemonError("the thread has no active turn to steer")
            try:
                daemon.request("turn/steer", {"threadId": thread_id, "expectedTurnId": turn_id,
                                              "input": text_input(text)})
            except DaemonUncertain as error:
                error.turn_id = turn_id  # the steer may be in this turn
                raise
            return turn_id
        finally:
            try:
                daemon.request("thread/unsubscribe", {"threadId": thread_id}, timeout=15)
            except DaemonError:
                pass


def await_turn(thread_id: str, turn_id: str, timeout: float,
               path: str | None = None) -> tuple[str, str]:
    """Wait for a running turn (started by anyone) and return (status, final answer)."""
    with Daemon(path) as daemon:
        daemon.subscribe(thread_id, None)
        try:
            deadline = time.time() + timeout
            while time.time() < deadline:
                status, answer = daemon.turn_answer(thread_id, turn_id)
                if status is not None:
                    return status, answer
                daemon.pump(lambda: turn_id in daemon.finished, timeout=10)
            raise TurnStillRunning(f"turn {turn_id} still running after {timeout:g}s")
        finally:
            try:
                daemon.request("thread/unsubscribe", {"threadId": thread_id}, timeout=15)
            except DaemonError:
                pass
