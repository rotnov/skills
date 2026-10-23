"""Private Grok Bot webhook target and wake-only relay; never a receipt or a model."""
import http.client
import io
import json
import os
from pathlib import Path
import re
import socket
import ssl
import stat
import threading
import time
import uuid
from quorum_os import FileLock, open_private, protect, reject_link
from quorum_release import ReleaseGuard

# One protected JSON file under the private ledger root, never sqlite settings:
# {"version": 1, "url": "<exact provider URL>", "key": "<bearer token>"}
TARGET_FILE = 'grok-webhook.json'
MAX_TARGET_BYTES = 2048
HOST = 'api2.cursor.sh'
URL_PATTERN = re.compile(r'https://api2\.cursor\.sh(/automations/webhook/'
                         r'[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})')
# RFC 6750 token68; no whitespace or header separators can reach the request.
KEY_PATTERN = re.compile(r'[A-Za-z0-9._~+/-]{16,512}={0,2}')
# Total seconds for DNS, connect, TLS, request, status, headers and body together.
POST_TIMEOUT = 10
MAX_POST_TIMEOUT = 60
MAX_RESPONSE_BYTES = 4096
MAX_BODY_BYTES = 1024
KIND = 'quorum.inbox_changed'
# wake:<message UUID> holds only {'state': one of these}. The parent's own
# delivery:<message UUID> record (offered/acked) is never written here.
WAKE_STATES = ('intent', 'submitted', 'unknown', 'failed', 'counted')


def invalid():
    return ValueError('webhook_config_invalid')


class WebhookTarget:
    """Validated provider endpoint; its representation never includes secrets."""
    __slots__ = ('_url', '_key', '_path')

    def __init__(self, url, key):
        match = URL_PATTERN.fullmatch(url) if isinstance(url, str) and len(url) <= 128 else None
        if match is None or not isinstance(key, str) or not KEY_PATTERN.fullmatch(key): raise invalid()
        self._url, self._key, self._path = url, key, match.group(1)

    url = property(lambda self: self._url)
    key = property(lambda self: self._key)
    path = property(lambda self: self._path)
    host = HOST

    def __repr__(self): return '<WebhookTarget redacted>'
    __str__ = __repr__

    def __reduce__(self): raise TypeError('webhook_target_not_serializable')


def safe_root(root):
    """Require a private root whose ancestors only the owner or the system can change."""
    root = Path(root)
    if not root.is_absolute() or '..' in root.parts: raise invalid()
    try:
        reject_link(root)
        info = root.lstat()
        if not stat.S_ISDIR(info.st_mode): raise invalid()
        if os.name == 'nt': return root
        if info.st_uid != os.geteuid() or stat.S_IMODE(info.st_mode) & 0o077: raise invalid()
        for parent in root.parents:
            info = parent.lstat()
            if stat.S_ISLNK(info.st_mode):
                # Only a system-owned link such as macOS /var -> private/var.
                if info.st_uid != 0: raise invalid()
                continue
            if (not stat.S_ISDIR(info.st_mode) or info.st_uid not in (0, os.geteuid())
                    or (info.st_mode & 0o022 and not info.st_mode & stat.S_ISVTX)):
                raise invalid()
    except (OSError, ValueError):
        raise invalid() from None
    return root


def configure(ledger, values):
    """Store the owner-supplied {url,key}; return only nonsecret diagnostics."""
    from quorum_listener import locked
    if not isinstance(values, dict) or set(values) != {'url', 'key'}: raise invalid()
    target = WebhookTarget(values['url'], values['key'])
    root = safe_root(ledger.root)
    data = json.dumps({'version':1, 'url':target.url, 'key':target.key}, sort_keys=True).encode()
    if len(data) > MAX_TARGET_BYTES: raise invalid()
    try: control = FileLock(root / 'listener-control.lock').__enter__()
    except ValueError as error:
        if str(error) == 'worker_already_running': raise ValueError('cloud_relay_running') from None
        raise invalid() from None
    try:
        if locked(root / 'listener.lock'): raise ValueError('cloud_relay_running')
        write_private(root, data)
    finally: control.__exit__()
    return {'webhook':'configured', 'provider':'grok-bot', 'auth':'bearer'}


def write_private(root, data):
    temporary, path = root / (TARGET_FILE + '.tmp'), root / TARGET_FILE
    try:
        reject_link(path)
        if path.exists() and not stat.S_ISREG(path.lstat().st_mode): raise invalid()
        try: temporary.unlink()
        except FileNotFoundError: pass
        fd = open_private(temporary, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        try:
            view = memoryview(data)
            while view: view = view[os.write(fd, view):]
            os.fsync(fd)
        finally: os.close(fd)
        protect(temporary)
        os.replace(temporary, path)
    except (OSError, ValueError):
        raise invalid() from None


def load_target(ledger):
    """Read and validate the private target; every refusal is a fixed code."""
    root = safe_root(ledger.root)
    path = root / TARGET_FILE
    try: info = path.lstat()
    except FileNotFoundError: raise ValueError('webhook_not_configured') from None
    except OSError: raise invalid() from None
    if not stat.S_ISREG(info.st_mode): raise invalid()
    try:
        # O_NONBLOCK: a FIFO swapped in after lstat cannot stall the relay.
        fd = open_private(path, os.O_RDONLY | getattr(os, 'O_NONBLOCK', 0))
    except FileNotFoundError: raise ValueError('webhook_not_configured') from None
    except (OSError, ValueError): raise invalid() from None
    try:
        opened = os.fstat(fd)
        if (not stat.S_ISREG(opened.st_mode) or (opened.st_dev, opened.st_ino) != (info.st_dev, info.st_ino)
                or opened.st_size > MAX_TARGET_BYTES):
            raise invalid()
        if os.name != 'nt' and (opened.st_uid != os.geteuid() or stat.S_IMODE(opened.st_mode) & 0o077
                                or opened.st_nlink != 1):
            raise invalid()
        raw = b''
        while len(raw) <= MAX_TARGET_BYTES:
            chunk = os.read(fd, MAX_TARGET_BYTES + 1 - len(raw))
            if not chunk: break
            raw += chunk
    except OSError: raise invalid() from None
    finally: os.close(fd)
    if len(raw) > MAX_TARGET_BYTES: raise invalid()
    try: value = json.loads(raw)
    except (ValueError, UnicodeError): raise invalid() from None
    if not isinstance(value, dict) or set(value) != {'version', 'url', 'key'} or value['version'] != 1:
        raise invalid()
    return WebhookTarget(value['url'], value['key'])


class WakeRejected(Exception):
    """A definite answer or a failure before any request byte was written."""


class WakeUnknown(Exception):
    """The request may have been written; the provider may have accepted it."""


def remaining(deadline):
    left = deadline - time.monotonic()
    if left <= 0: raise TimeoutError()
    return left


def resolve(host, port, deadline):
    """getaddrinfo cannot be cancelled; a stalled lookup is abandoned holding no socket or request."""
    found, done = [], threading.Event()
    def lookup():
        try: found.extend(socket.getaddrinfo(host, port, 0, socket.SOCK_STREAM))
        except Exception: pass
        finally: done.set()
    threading.Thread(target=lookup, name='quorum-webhook-dns', daemon=True).start()
    if not done.wait(remaining(deadline)) or not found: raise TimeoutError()
    return list(found)


class BoundedHTTPSConnection(http.client.HTTPSConnection):
    """Direct TLS to the pinned host; DNS, TCP and the handshake share one deadline."""

    def __init__(self, host, port, *, timeout, context):
        super().__init__(host, port, timeout=timeout, context=context)
        self.tls, self.deadline = context, time.monotonic() + timeout

    def connect(self):
        sock = None
        for family, kind, proto, _, address in resolve(self.host, self.port, self.deadline):
            candidate = socket.socket(family, kind, proto)
            try:
                candidate.settimeout(remaining(self.deadline))
                candidate.connect(address)
            except OSError:
                candidate.close()
                continue
            sock = candidate
            break
        if sock is None: raise OSError()
        try:
            sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
            sock = self.tls.wrap_socket(sock, server_hostname=self.host, do_handshake_on_connect=False)
            sock.settimeout(remaining(self.deadline))
            # Since Python 3.5 the socket timeout bounds the whole handshake, not each read.
            sock.do_handshake()
        except BaseException:
            sock.close()
            raise
        self.sock = sock


class DeadlineSocket:
    """The socket view http.client sees: every send and recv shares one total deadline."""

    def __init__(self, sock, deadline): self._sock, self._deadline = sock, deadline

    def sendall(self, data):
        view = memoryview(data).cast('B')
        while view:
            self._sock.settimeout(remaining(self._deadline))
            view = view[self._sock.send(view):]

    def recv_into(self, buffer):
        self._sock.settimeout(remaining(self._deadline))
        return self._sock.recv_into(buffer)

    def makefile(self, mode='r', *args, **kwargs):
        if mode != 'rb': raise ValueError()
        return io.BufferedReader(DeadlineReader(self))

    def close(self): pass  # post_wake owns the transport and aborts it before returning.

    def abort(self): self._sock.close()


class DeadlineReader(io.RawIOBase):
    def __init__(self, transport):
        super().__init__()
        self._transport = transport

    def readable(self): return True

    def readinto(self, buffer): return self._transport.recv_into(buffer)


def post_wake(target, payload, *, connection=BoundedHTTPSConnection, timeout=POST_TIMEOUT):
    """POST once to the pinned host within one total deadline; no redirects, proxies or raw error text."""
    if type(timeout) not in (int, float) or not 0 < timeout <= MAX_POST_TIMEOUT: raise WakeRejected()
    deadline = time.monotonic() + timeout
    body = json.dumps(payload, separators=(',', ':'), sort_keys=True).encode('ascii')
    if len(body) > MAX_BODY_BYTES: raise WakeRejected()
    headers = {'Authorization':'Bearer ' + target.key, 'Content-Type':'application/json',
               'User-Agent':'quorum-grok-relay', 'Connection':'close'}
    conn = transport = response = None
    try:
        try:
            conn = connection(HOST, 443, timeout=timeout, context=ssl.create_default_context())
            conn.connect()
            sock = getattr(conn, 'sock', None)
            # Without a real socket the deadline cannot be enforced, so nothing is sent.
            if not isinstance(sock, socket.socket): raise WakeRejected()
            remaining(deadline)
            transport = conn.sock = DeadlineSocket(sock, deadline)
        except Exception: raise WakeRejected() from None
        try:
            conn.request('POST', target.path, body=body, headers=headers)
            response = conn.getresponse()
            status = response.status
        except Exception: raise WakeUnknown() from None
        try: response.read(MAX_RESPONSE_BYTES)
        except Exception: pass  # The status is already a definite provider answer.
    finally:
        # Close the response and connection, then the socket itself, before returning.
        for close in (getattr(response, 'close', None), getattr(conn, 'close', None),
                      getattr(transport, 'abort', None)):
            try:
                if close: close()
            except Exception: pass
    # http.client never follows a redirect; a 3xx is a definite refusal here.
    if type(status) is not int or not 200 <= status < 300: raise WakeRejected()


def canonical(value):
    try: return isinstance(value, str) and str(uuid.UUID(value)) == value
    except (ValueError, TypeError, AttributeError): return False


class CloudWakeRelay:
    """Listener worker: one wake hint per pending message; the parent receives it."""

    def __init__(self, api, ledger, record, *, connection=BoundedHTTPSConnection, timeout=POST_TIMEOUT):
        if (not isinstance(record, dict)
                or not all(canonical(record.get(key)) for key in ('agent_id', 'quorum_id', 'provider_conversation_id'))
                or record.get('session_ref') != 'grok-bot:' + record['provider_conversation_id']):
            raise ValueError('session_binding_denied')
        self.api, self.ledger = api, ledger
        self.record = {key:record[key] for key in ('agent_id', 'session_ref', 'quorum_id', 'provider_conversation_id')}
        self.connection, self.timeout = connection, timeout
        self.release = ReleaseGuard(api, ledger)

    def scope(self):
        return {'agent_id':self.record['agent_id'], 'session_ref':self.record['session_ref']}

    def wakes(self):
        rows = self.ledger.db.execute("select key,data from settings where key like 'wake:%'").fetchall()
        return [(key, (json.loads(raw) or {}).get('state')) for key, raw in rows]

    def transition(self, key, old, new):
        # Compare-and-set: a concurrent transition is never overwritten.
        with self.ledger.db:
            cursor = self.ledger.db.execute('update settings set data=? where key=? and data=?',
                                            (json.dumps({'state':new}), key, json.dumps({'state':old})))
        return cursor.rowcount == 1

    def acked(self, message_id):
        delivery = self.ledger.setting('delivery:' + message_id)
        return isinstance(delivery, dict) and delivery.get('state') == 'acked'

    def reconcile(self):
        self.release.check()
        # A crash between intent and outcome may have written the request.
        for key, state in self.wakes():
            if state == 'intent': self.transition(key, 'intent', 'unknown')
        self.api.call('session_inbox', self.scope())

    def run_once(self):
        self.release.check()
        wakes = self.wakes()
        # Only the parent's actual acknowledged receipt counts, exactly once.
        for key, state in wakes:
            if state in ('intent', 'submitted', 'unknown', 'failed') and self.acked(key[5:]):
                if self.transition(key, state, 'counted'): return 'delivered'
        for key, state in wakes:
            if state in ('intent', 'unknown'):
                if state == 'intent': self.transition(key, 'intent', 'unknown')
                return 'unknown'
        messages = self.api.call('session_inbox', self.scope())
        if not messages: return 'idle'
        if not isinstance(messages, list): raise ValueError('invalid_response')
        # The durable inbox order already places urgent messages first.
        message = messages[0]
        if not isinstance(message, dict) or not canonical(message.get('id')):
            raise ValueError('invalid_response')
        if message.get('recipient_id') != self.record['agent_id']: raise ValueError('session_binding_denied')
        if message.get('priority', 'normal') not in (None, 'normal', 'urgent'): raise ValueError('invalid_priority')
        key = 'wake:' + message['id']
        existing = self.ledger.setting(key)
        if existing is not None:
            if existing.get('state') == 'failed': raise ValueError('grok_transport_unavailable')
            return 'idle'
        if self.acked(message['id']): return 'idle'
        target = load_target(self.ledger)
        payload = {'kind':KIND, 'quorum_id':self.record['quorum_id'], 'agent_id':self.record['agent_id'],
                   'session_ref':self.record['session_ref'], 'message_id':message['id']}
        self.release.check()
        with self.ledger.db:
            claimed = self.ledger.db.execute('insert or ignore into settings values (?,?)',
                                             (key, json.dumps({'state':'intent'}))).rowcount == 1
        if not claimed: return 'idle'
        try:
            post_wake(target, payload, connection=self.connection, timeout=self.timeout)
        except WakeRejected:
            self.transition(key, 'intent', 'failed')
            raise ValueError('grok_transport_unavailable') from None
        except Exception:
            self.transition(key, 'intent', 'unknown')
            return 'unknown'
        # Provider acceptance is a hint only; the parent must still receive it.
        self.transition(key, 'intent', 'submitted')
        return 'idle'
