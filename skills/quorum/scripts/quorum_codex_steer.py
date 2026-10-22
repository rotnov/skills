"""Experimental steering of the already active turn in an existing Codex chat.

Route: `codex app-server proxy` connects to the *already running* local
app-server control socket. It never starts a server or a model, never creates a
substitute session, and never calls `thread/resume`, `thread/fork` or
`turn/start`, so it cannot take ownership of the owner's chat. Only
`thread/loaded/list`, metadata-only `thread/read`, `thread/turns/list` and
`turn/steer` with `expectedTurnId` are used. Newline-delimited JSON-RPC frames
omit the `jsonrpc` member, matching the stdio transport.

Interface: https://learn.chatgpt.com/docs/app-server#steer-an-active-turn
"""
import json
import os
from pathlib import Path
import shutil
import subprocess
import threading
import time
import uuid

CLIENT = {'name': 'quorum-session', 'version': '1'}
FRAME_LIMIT = 262144
FRAME_COUNT = 512
PAGE_LIMIT = 100
PAGES = 20


class SteerUnavailable(ValueError):
    """Nothing was submitted; the caller may queue this message once."""
    def __init__(self, reason):
        super().__init__('codex_steer_unavailable')
        self.reason = reason


def codex_command():
    """Attach to the running daemon only. `codex app-server` alone would serve."""
    executable = shutil.which('codex')
    if not executable: raise SteerUnavailable('engine_missing')
    return [executable, 'app-server', 'proxy']


class Client:
    """Bounded stdio JSON-RPC client. A reader thread keeps every wait finite."""

    def __init__(self, command, timeout=25):
        self.deadline = time.monotonic()+timeout
        self.frames, self.ref = [], 0
        try:
            self.process = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL, close_fds=True,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)
        except OSError:
            raise SteerUnavailable('proxy_unavailable') from None
        self.reader = threading.Thread(target=self._drain, daemon=True)
        self.reader.start()

    def _drain(self):
        try:
            for raw in self.process.stdout:
                if len(raw) > FRAME_LIMIT or len(self.frames) >= FRAME_COUNT: break
                try: frame = json.loads(raw)
                except (ValueError, UnicodeError): continue  # Ignore non-JSON proxy chatter.
                if isinstance(frame, dict): self.frames.append(frame)
        except (OSError, ValueError): pass

    def write(self, frame, *, ambiguous=False):
        raw = json.dumps(frame, ensure_ascii=False).encode()+b'\n'
        if len(raw) > FRAME_LIMIT: raise ValueError('invalid_message')
        try:
            self.process.stdin.write(raw)
            self.process.stdin.flush()
        except (OSError, ValueError):
            # A refused or half-written frame carries no terminating newline, so
            # the server never parses it. Only the steering frame is ambiguous.
            if ambiguous: raise ValueError('session_delivery_unknown') from None
            raise SteerUnavailable('proxy_closed') from None

    def reply(self, ref):
        while True:
            for frame in list(self.frames):
                if frame.get('id') == ref: return frame
            exited = self.process.poll() is not None and not self.reader.is_alive()
            if exited or time.monotonic() >= self.deadline:
                for frame in list(self.frames):
                    if frame.get('id') == ref: return frame
                return None
            time.sleep(.02)

    def request(self, method, params):
        self.ref += 1
        self.write({'id': self.ref, 'method': method, 'params': params})
        return self.reply(self.ref)

    def notify(self, method):
        self.write({'method': method})

    def close(self):
        try:
            self.process.kill()
            self.process.wait(timeout=5)
        except (OSError, subprocess.SubprocessError): pass
        for stream in (self.process.stdin, self.process.stdout):
            try: stream.close()
            except OSError: pass
        self.reader.join(timeout=2)


def result_of(frame, reason):
    if frame is None or 'error' in frame or not isinstance(frame.get('result'), dict):
        raise SteerUnavailable(reason)
    return frame['result']


def head_turn(client, session_ref):
    """ID of the turn currently running, or None when the chat is not busy."""
    turns = result_of(client.request('thread/turns/list', {'threadId': session_ref, 'limit': 1,
        'sortDirection': 'desc', 'itemsView': 'notLoaded'}), 'turns_unreadable').get('data')
    if not isinstance(turns, list) or not turns: return None
    head = turns[0]
    if not isinstance(head, dict) or head.get('status') != 'inProgress': return None
    turn_id = head.get('id')
    return turn_id if isinstance(turn_id, str) and turn_id else None


def active_turn(client, record, session_ref):
    """Bind this exact loaded chat and return its active turn, or None."""
    cursor = None
    for _ in range(PAGES):
        listed = result_of(client.request('thread/loaded/list', {'cursor': cursor, 'limit': PAGE_LIMIT}),
                           'daemon_unreachable')
        data = listed.get('data')
        if not isinstance(data, list): raise SteerUnavailable('invalid_daemon_response')
        if session_ref in data: break
        cursor = listed.get('nextCursor')
        # An absent thread means this daemon does not serve the owner's chat.
        if not isinstance(cursor, str) or not cursor: raise SteerUnavailable('thread_not_loaded')
    else: raise SteerUnavailable('thread_not_loaded')
    thread = result_of(client.request('thread/read', {'threadId': session_ref, 'includeTurns': False}),
                       'thread_unreadable').get('thread')
    if not isinstance(thread, dict) or thread.get('id') != session_ref:
        raise SteerUnavailable('thread_not_loaded')
    cwd = thread.get('cwd')
    try: matches = isinstance(cwd, str) and Path(cwd).resolve() == Path(record['cwd']).resolve()
    except (OSError, ValueError): matches = False
    # The daemon must agree with the registered working directory for this chat.
    # A disagreement refuses the steering route without writing anything; the
    # caller still delivers through the ordinary queue, as it does for every
    # normal message. Caller identity is checked separately by require_caller.
    if not matches: raise SteerUnavailable('thread_directory_mismatch')
    status = thread.get('status')
    if not isinstance(status, dict) or status.get('type') != 'active': return None
    return head_turn(client, session_ref)


def not_steerable(data):
    """Documented proof that the server refused to append anything at all."""
    if data == 'activeTurnNotSteerable': return True
    if not isinstance(data, dict): return False
    if 'activeTurnNotSteerable' in data: return True
    return not_steerable(data.get('codexErrorInfo'))


def steer(client, record, session_ref, turn_id, message_id, text):
    if not isinstance(text, str) or not text or len(text.encode()) > 48000:
        raise ValueError('invalid_message')
    client.ref += 1
    ref = client.ref
    client.write({'id': ref, 'method': 'turn/steer', 'params': {'threadId': session_ref,
        'expectedTurnId': turn_id, 'input': [{'type': 'text', 'text': text}],
        'clientUserMessageId': 'quorum:'+message_id}}, ambiguous=True)
    frame = client.reply(ref)
    if frame is None: raise ValueError('session_delivery_unknown')
    answer = frame.get('result')
    if isinstance(answer, dict) and answer.get('turnId') == turn_id: return 'submitted'
    error = frame.get('error')
    if isinstance(error, dict) and not_steerable(error.get('data')): return 'rejected'
    # Any other error is opaque. A later completion or replacement of that turn
    # does not prove the append never happened, so it is never a rejection.
    raise ValueError('session_delivery_unknown')


def submit(record, message_id, build, timeout=25):
    """Steer one message into the running turn of this exact existing chat.

    Returns 'submitted' or 'rejected'. Only the documented not-steerable
    refusal is a rejection. A rejection and SteerUnavailable both prove that
    nothing reached the chat, so the caller queues the message once. Every
    other post-write outcome raises session_delivery_unknown for reconciliation.
    """
    session_ref = str(uuid.UUID(record['session_ref']))
    message_id = str(uuid.UUID(message_id))
    client = Client(codex_command(), timeout)
    try:
        result_of(client.request('initialize', {'clientInfo': CLIENT}), 'daemon_unreachable')
        client.notify('initialized')
        turn_id = active_turn(client, record, session_ref)
        # Queue order is preserved whenever the chat is not inside a turn.
        if turn_id is None: raise SteerUnavailable('no_active_turn')
        return steer(client, record, session_ref, turn_id, message_id, build())
    finally: client.close()
