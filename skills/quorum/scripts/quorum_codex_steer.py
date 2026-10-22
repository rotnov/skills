"""Experimental steering of the already active turn in an existing Codex chat.

Route: the *already running* local app server's documented control socket,
`$CODEX_HOME/app-server-control/app-server-control.sock`. That Unix socket
carries WebSocket after an HTTP Upgrade; each text frame holds one JSON-RPC
object without the `jsonrpc` member. This module only connects as a client: it
never starts, replaces or stops a server or a model, never creates a substitute
session, and never calls `thread/resume`, `thread/fork`, `thread/start` or
`turn/start`, so it cannot take ownership of the owner's chat. Only
`thread/loaded/list`, metadata-only `thread/read`, `thread/turns/list` and
`turn/steer` with `expectedTurnId` are used. A chat served by another process,
such as a Desktop app server on private stdio, is not reachable here.

Interface: https://learn.chatgpt.com/docs/app-server#steer-an-active-turn
"""
import asyncio
import json
import os
from pathlib import Path
import socket
import stat
import struct
import sys
import time
import uuid

CLIENT = {'name': 'quorum-session', 'version': '1'}
FRAME_LIMIT = 262144
FRAME_COUNT = 512
FRAME_QUEUE = 16
CLOSE_TIMEOUT = 2
PAGE_LIMIT = 100
PAGES = 20
#: None where the platform has no Unix-domain sockets, as on Windows builds.
AF_UNIX = getattr(socket, 'AF_UNIX', None)


class SteerUnavailable(ValueError):
    """Nothing was submitted; the caller may queue this message once."""
    def __init__(self, reason):
        super().__init__('codex_steer_unavailable')
        self.reason = reason


def codex_home():
    configured = os.environ.get('CODEX_HOME')
    return Path(configured if configured else Path.home() / '.codex').resolve()


SHARED_WRITE = stat.S_IWGRP | stat.S_IWOTH
SHARED_ANY = stat.S_IRWXG | stat.S_IRWXO


def control_socket():
    """The documented control socket, if only this user can have placed it there.

    The entry is either the socket itself or, as a managed daemon publishes it,
    one link to a socket in that daemon's private directory. Exactly that one
    hop is followed; the returned path is the real socket, so the link is not
    read again when connecting.
    """
    if os.name == 'nt' or AF_UNIX is None: raise SteerUnavailable('unix_transport_unsupported')
    directory = codex_home() / 'app-server-control'
    path = directory / 'app-server-control.sock'
    try: parent, entry = os.lstat(directory), os.lstat(path)
    except FileNotFoundError: raise SteerUnavailable('endpoint_missing') from None
    except OSError: raise SteerUnavailable('endpoint_invalid') from None
    if not stat.S_ISDIR(parent.st_mode) or not (stat.S_ISSOCK(entry.st_mode) or stat.S_ISLNK(entry.st_mode)):
        raise SteerUnavailable('endpoint_invalid')
    # Another user who owns or may write the directory could substitute the entry.
    if parent.st_uid != os.geteuid() or entry.st_uid != os.geteuid() or parent.st_mode & SHARED_WRITE:
        raise SteerUnavailable('endpoint_not_private')
    return linked_socket(path) if stat.S_ISLNK(entry.st_mode) else path


def linked_socket(link):
    """The private socket one link points to, or SteerUnavailable.

    The target must be an absolute, canonical path to a socket of this user,
    neither shared with group or others, in a directory of this user that is
    equally private. Every directory above it must belong to this user or root
    and must not let anyone else rename entries, unless it is sticky like /tmp.
    Relative, chained, looping and dangling links are refused.
    """
    try: target = Path(os.readlink(link))
    except OSError: raise SteerUnavailable('endpoint_invalid') from None
    if not target.is_absolute(): raise SteerUnavailable('endpoint_invalid')
    folder = target.parent
    try:
        canonical = os.path.realpath(folder, strict=True) == str(folder)
        place, entry = os.lstat(folder), os.lstat(target)
        above = [os.lstat(ancestor) for ancestor in folder.parents]
    except FileNotFoundError: raise SteerUnavailable('endpoint_missing') from None
    except OSError: raise SteerUnavailable('endpoint_invalid') from None
    # A linked ancestor, a second link or anything but a socket is refused.
    if not canonical or not stat.S_ISDIR(place.st_mode) or not stat.S_ISSOCK(entry.st_mode):
        raise SteerUnavailable('endpoint_invalid')
    private = all(info.st_uid == os.geteuid() and not info.st_mode & SHARED_ANY for info in (place, entry))
    safe = all(stat.S_ISDIR(info.st_mode) and info.st_uid in (0, os.geteuid())
               and (not info.st_mode & SHARED_WRITE or info.st_mode & stat.S_ISVTX) for info in above)
    if not private or not safe: raise SteerUnavailable('endpoint_not_private')
    return target


def peer_uid(connection):
    """Kernel-reported owner of the listening peer, or None where unsupported."""
    if sys.platform == 'darwin':
        # getsockopt(SOL_LOCAL, LOCAL_PEERCRED) returns struct xucred.
        version, uid = struct.unpack_from('=II', connection.getsockopt(0, 1, 76))
        return uid if version == 0 else -1
    if sys.platform.startswith('linux'):
        return struct.unpack('3i', connection.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED,
                                                         struct.calcsize('3i')))[1]
    return None


class Client:
    """Bounded WebSocket JSON-RPC client over the private control socket.

    Every wait, including each write, runs on an asyncio loop under one overall
    deadline. A write that cannot finish in time aborts the transport, so no
    thread, lock or socket stays blocked behind a peer that stopped reading.
    """

    def __init__(self, timeout=25):
        self.deadline = time.monotonic()+timeout
        self.ref, self.received = 0, 0
        self.connection, self.aborted = None, False

    async def open(self, path):
        try: from websockets.asyncio.client import connect
        except ImportError: raise SteerUnavailable('websockets_missing') from None
        raw = socket.socket(AF_UNIX, socket.SOCK_STREAM)
        try:
            raw.settimeout(self.remaining())
            raw.connect(str(path))
            try: owner = peer_uid(raw)
            except (OSError, struct.error): owner = -1
            if owner is not None and owner != os.geteuid():
                raise SteerUnavailable('endpoint_not_private')
            # A supplied socket disables redirects and proxies; keepalive pings
            # stay off so this short exchange carries only our own frames.
            self.connection = await connect('ws://localhost', sock=raw, compression=None,
                proxy=None, open_timeout=self.remaining(), ping_interval=None,
                close_timeout=CLOSE_TIMEOUT, max_size=FRAME_LIMIT, max_queue=FRAME_QUEUE)
        except SteerUnavailable:
            raw.close()
            raise
        except Exception:
            raw.close()
            raise SteerUnavailable('daemon_unreachable') from None

    def remaining(self):
        left = self.deadline-time.monotonic()
        if left <= 0: raise SteerUnavailable('daemon_unreachable')
        return left

    async def write(self, frame, *, ambiguous=False):
        from websockets.protocol import State
        raw = json.dumps(frame, ensure_ascii=False, separators=(',', ':'))
        if len(raw.encode()) > FRAME_LIMIT: raise ValueError('invalid_message')
        # A closed connection or an elapsed deadline means nothing was written.
        if self.connection.protocol.state is not State.OPEN: raise SteerUnavailable('daemon_closed')
        left = self.remaining()
        try:
            # send() returns once the frame is handed to the transport and its
            # buffer has drained below the write limit; both wait under the deadline.
            async with asyncio.timeout(left): await self.connection.send(raw)
        except Exception as failure:
            self.abort()
            # Only the steering frame is ambiguous: part of it may have left.
            if ambiguous: raise ValueError('session_delivery_unknown') from None
            reason = 'daemon_write_timeout' if isinstance(failure, TimeoutError) else 'daemon_closed'
            raise SteerUnavailable(reason) from None

    async def reply(self, ref):
        """The response to `ref`, skipping notifications and server requests."""
        while self.received < FRAME_COUNT:
            left = self.deadline-time.monotonic()
            if left <= 0: return None
            try:
                async with asyncio.timeout(left): raw = await self.connection.recv()
            except Exception: return None  # Timeout, closure or an oversized frame.
            self.received += 1
            if not isinstance(raw, str): continue
            try: frame = json.loads(raw)
            except ValueError: continue
            if (isinstance(frame, dict) and type(frame.get('id')) is int and frame['id'] == ref
                    and 'method' not in frame):
                return frame
        return None

    async def request(self, method, params):
        self.ref += 1
        await self.write({'id': self.ref, 'method': method, 'params': params})
        return await self.reply(self.ref)

    async def notify(self, method):
        await self.write({'method': method})

    def abort(self):
        """Drop this connection at once, discarding anything still buffered."""
        self.aborted = True
        self.connection.transport.abort()

    async def close(self):
        """Close this client connection only; the server keeps running."""
        if self.connection is None: return
        try:
            # A peer that stopped reading never completes a closing handshake.
            if not self.aborted:
                async with asyncio.timeout(CLOSE_TIMEOUT): await self.connection.close()
        except Exception: pass
        self.abort()
        try:
            async with asyncio.timeout(CLOSE_TIMEOUT): await self.connection.wait_closed()
        except Exception: pass


def result_of(frame, reason):
    if frame is None or 'error' in frame or not isinstance(frame.get('result'), dict):
        raise SteerUnavailable(reason)
    return frame['result']


async def head_turn(client, session_ref):
    """ID of the turn currently running, or None when the chat is not busy."""
    turns = result_of(await client.request('thread/turns/list', {'threadId': session_ref, 'limit': 1,
        'sortDirection': 'desc', 'itemsView': 'notLoaded'}), 'turns_unreadable').get('data')
    if not isinstance(turns, list) or not turns: return None
    head = turns[0]
    if not isinstance(head, dict) or head.get('status') != 'inProgress': return None
    turn_id = head.get('id')
    return turn_id if isinstance(turn_id, str) and turn_id else None


async def active_turn(client, record, session_ref):
    """Bind this exact loaded chat and return its active turn, or None."""
    cursor = None
    for _ in range(PAGES):
        listed = result_of(await client.request('thread/loaded/list', {'cursor': cursor, 'limit': PAGE_LIMIT}),
                           'daemon_unreachable')
        data = listed.get('data')
        if not isinstance(data, list): raise SteerUnavailable('invalid_daemon_response')
        if session_ref in data: break
        cursor = listed.get('nextCursor')
        # An absent thread means this daemon does not serve the owner's chat.
        if not isinstance(cursor, str) or not cursor: raise SteerUnavailable('thread_not_loaded')
    else: raise SteerUnavailable('thread_not_loaded')
    thread = result_of(await client.request('thread/read', {'threadId': session_ref, 'includeTurns': False}),
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
    return await head_turn(client, session_ref)


def not_steerable(data):
    """Documented proof that the server refused to append anything at all."""
    if data == 'activeTurnNotSteerable': return True
    if not isinstance(data, dict): return False
    if 'activeTurnNotSteerable' in data: return True
    return not_steerable(data.get('codexErrorInfo'))


async def steer(client, record, session_ref, turn_id, message_id, text):
    if not isinstance(text, str) or not text or len(text.encode()) > 48000:
        raise ValueError('invalid_message')
    client.ref += 1
    ref = client.ref
    await client.write({'id': ref, 'method': 'turn/steer', 'params': {'threadId': session_ref,
        'expectedTurnId': turn_id, 'input': [{'type': 'text', 'text': text}],
        'clientUserMessageId': 'quorum:'+message_id}}, ambiguous=True)
    frame = await client.reply(ref)
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
    path = control_socket()
    return asyncio.run(exchange(path, record, session_ref, message_id, build, timeout))


async def exchange(path, record, session_ref, message_id, build, timeout):
    client = Client(timeout)
    await client.open(path)
    try:
        hello = result_of(await client.request('initialize', {'clientInfo': CLIENT}), 'daemon_unreachable')
        home = hello.get('codexHome')
        # A daemon that reports another home serves another account's chats.
        if home is not None and (not isinstance(home, str) or Path(home).resolve() != codex_home()):
            raise SteerUnavailable('daemon_home_mismatch')
        await client.notify('initialized')
        turn_id = await active_turn(client, record, session_ref)
        # Queue order is preserved whenever the chat is not inside a turn.
        if turn_id is None: raise SteerUnavailable('no_active_turn')
        return await steer(client, record, session_ref, turn_id, message_id, build())
    finally: await client.close()
