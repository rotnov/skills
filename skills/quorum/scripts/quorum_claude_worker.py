"""Claude native-worker foreground inbox: pending slots, owner-parent attestation and scoped capabilities.

A logical actor is derived from the actual shared CLAUDE_CODE_SESSION_ID and the exact native
handle that the owner-parent's own tool returned. Possession of the generated capability file
delegates only its scoped operations to the local same-user caller; it never authenticates a
native child independently. Nothing here reads a provider transcript or starts a model, child,
pipe, hook or daemon.
"""
from contextlib import contextmanager, nullcontext
import hashlib
import hmac
import json
import os
from pathlib import Path
import re
import secrets
import sqlite3
import stat
import sys
import time
import unicodedata
from urllib.parse import quote
import uuid
from quorum_native_inbox import CANONICAL, TRANSPORT, NativeInboxError, Guarded
from quorum_api import QuorumError, guarded_call

PROVIDER = 'claude'
PREFIX = 'claude-worker:'
SLOT_KEY = 'claude-worker-slot'
INDEX = 'claude-worker-actor:'
JSON_LIMIT = 16384
PENDING_SECONDS = 120
DEFAULT_TTL, TTL_LIMIT = 900, 3600
AWAIT_DEFAULT, AWAIT_LIMIT = 60, 60
LABEL_LIMIT = 256
OPERATIONS = {'create': ('create', 'connect', 'receive', 'received', 'reply', 'status', 'stop'),
              'accept': ('accept', 'connect', 'receive', 'received', 'reply', 'status', 'stop')}
SESSION_ACTIONS = ('connect', 'receive', 'received', 'reply', 'status', 'stop')
COMPETING = ('CODEX_THREAD_ID', 'GROK_SESSION_ID', 'GROK_LEADER_SOCKET', 'CURSOR_CONVERSATION_ID')
NAME = re.compile('[a-z][a-z0-9-]{0,47}')
DIGEST = re.compile('[0-9a-f]{64}')
# The process's open-descriptor listing; without it a SQLite connection's identity is unprovable.
DESCRIPTOR_LISTING = '/dev/fd' if sys.platform == 'darwin' else '/proc/self/fd'
SIDECARS = ('ledger.sqlite-journal', 'ledger.sqlite-wal', 'ledger.sqlite-shm')
# Patchable seams for fake clocks in tests; wall-clock deadlines are persisted.
now = time.time
sleep = time.sleep


def _denied(reason=None): return NativeInboxError('native_worker_binding_denied', reason)
def _unavailable(reason=None): return NativeInboxError('native_worker_unavailable', reason)
def _terminal(reason=None): return NativeInboxError('native_worker_slot_terminal', reason)


def _digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def _strict_json(raw, reason):
    """One bounded UTF-8 JSON object; duplicate keys and non-finite numbers are refused."""
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result: raise ValueError('duplicate')
            result[key] = value
        return result
    def constant(_): raise ValueError('constant')
    try: value = json.loads(raw.decode('utf-8'), object_pairs_hook=pairs, parse_constant=constant)
    except (ValueError, UnicodeError, RecursionError): raise _denied(reason) from None
    if not isinstance(value, dict): raise _denied(reason)
    return value


def proof_available():
    """Owner/permission proof uses POSIX descriptors; elsewhere this route fails closed (never chmods)."""
    return (os.name == 'posix' and hasattr(os, 'O_NOFOLLOW') and hasattr(os, 'O_DIRECTORY')
            and all(function in os.supports_dir_fd for function in (os.open, os.mkdir, os.rename, os.unlink)))


def actual_session():
    """The one unambiguous actual Claude export; copied or competing client exports never qualify."""
    if not proof_available(): raise _unavailable('ownership_unverified')
    value = os.environ.get('CLAUDE_CODE_SESSION_ID')
    if any(os.environ.get(name) for name in COMPETING):
        raise _unavailable('competing_exports' if value else 'provider_unsupported')
    if not value: raise _unavailable('session_missing')
    if not CANONICAL.fullmatch(value): raise _unavailable('session_invalid')
    return value


def _label(value):
    """Exact bounded native string: 1..256 UTF-8 bytes, no control characters, never normalized."""
    try: size = len(value.encode('utf-8')) if isinstance(value, str) else 0
    except UnicodeError: size = 0
    if not 1 <= size <= LABEL_LIMIT or any(unicodedata.category(char) == 'Cc' for char in value):
        raise _denied('handle_invalid')
    return value


def _slot_uuid(value):
    if not isinstance(value, str) or not CANONICAL.fullmatch(value): raise _denied('slot_invalid')
    return value


def _state_root(state_root):
    root = Path(state_root)
    if not root.is_absolute() or os.path.normpath(str(root)) != str(root): raise _unavailable('path_unsafe')
    return root


# --- Verified, no-follow private paths -------------------------------------------------------

def _verify(info, kind):
    """`kind`: 'ancestor' (root- or user-owned; shared only if sticky), 'private' directory or 'file'."""
    if not (stat.S_ISREG(info.st_mode) and info.st_nlink == 1 if kind == 'file' else stat.S_ISDIR(info.st_mode)):
        raise _unavailable('path_unsafe')
    if kind == 'ancestor':
        if info.st_uid not in (0, os.getuid()) or (info.st_mode & 0o022 and not info.st_mode & stat.S_ISVTX):
            raise _unavailable('path_unsafe')
    elif info.st_uid != os.getuid() or info.st_mode & 0o077:
        raise _unavailable('path_unsafe')


def _open(name, parent, kind, *, create=False):
    """Open one component relative to its verified parent; a link, swap or loose mode is refused unchanged."""
    if create:
        try: os.mkdir(name, 0o700, dir_fd=parent)
        except FileExistsError: pass
        except OSError: raise _unavailable('path_unsafe') from None
    flags = os.O_RDONLY | os.O_NOFOLLOW | (os.O_NONBLOCK if kind == 'file' else os.O_DIRECTORY)
    try: fd = os.open(name, flags, dir_fd=parent)
    except FileNotFoundError: return None
    except OSError: raise _unavailable('path_unsafe') from None
    try: _verify(os.fstat(fd), kind)
    except BaseException:
        os.close(fd)
        raise
    return fd


def _root_fd(root, *, create=False):
    """Descriptor of the private state root, every component opened without following links."""
    opened = [os.open('/', os.O_RDONLY | os.O_DIRECTORY)]
    try:
        _verify(os.fstat(opened[0]), 'ancestor')
        parts = root.parts[1:]
        for index, name in enumerate(parts):
            last = index == len(parts) - 1
            fd = _open(name, opened[-1], 'private' if last else 'ancestor', create=create)
            if fd is None: raise _unavailable('path_unsafe')
            opened.append(fd)
        return opened.pop()
    finally:
        for fd in opened: os.close(fd)


@contextmanager
def _directory(root, *names, create=False, kinds=None):
    """Yield the verified descriptor of root/names...; None when a component is missing."""
    fds = [_root_fd(root)]
    try:
        for index, name in enumerate(names):
            kind = kinds[index] if kinds else 'private'
            fd = _open(name, fds[-1], kind, create=create)
            if fd is None:
                yield None
                return
            fds.append(fd)
        yield fds[-1]
    finally:
        for fd in fds: os.close(fd)


def _read_file(directory, name, reason):
    fd = _open(name, directory, 'file')
    if fd is None: raise _denied(reason)
    with os.fdopen(fd, 'rb') as source: raw = source.read(JSON_LIMIT + 1)
    if len(raw) > JSON_LIMIT: raise _unavailable('path_unsafe')
    return raw


def _read_db(directory, path, read, *, ledger='slot'):
    """Run `read(db)` on a verified, stable private LocalLedger opened read-only; None when absent."""
    fd = _open('ledger.sqlite', directory, 'file')
    if fd is None: return None
    try:
        journal = _sidecars(directory)
        try:
            db = _connect(path, _identity([fd])[0], 'ro')
            try: return read(db)
            finally: db.close()
        except (sqlite3.Error, NativeInboxError):
            # A read-only view cannot roll back a hot journal: name the supported writable recovery route.
            if journal or _sidecars(directory):
                raise NativeInboxError('native_worker_unavailable', 'recovery_needed', recovery=ledger) from None
            raise _unavailable('path_unsafe') from None
    finally: os.close(fd)


def _sidecars(directory):
    """Validate existing SQLite sidecars (private, owned, single-link regular files) without changing them;
    True when a rollback journal is present for SQLite's own recovery."""
    journal = False
    for name in SIDECARS:
        fd = _open(name, directory, 'file')
        if fd is not None:
            os.close(fd)
            journal = journal or name.endswith('-journal')
    return journal


class _AnchoredConnection(sqlite3.Connection):
    """Closes the held verified directory descriptor together with the connection."""
    anchor = None

    def close(self):
        try: super().close()
        finally:
            if self.anchor is not None:
                os.close(self.anchor)
                self.anchor = None


def _descriptors():
    """(device, inode) of every open regular-file descriptor; fails closed without the platform listing."""
    try: names = os.listdir(DESCRIPTOR_LISTING)
    except OSError: raise _unavailable('ownership_unverified') from None
    result = {}
    for name in names:
        try: info = os.fstat(int(name))
        except (OSError, ValueError): continue
        if stat.S_ISREG(info.st_mode): result[int(name)] = (info.st_dev, info.st_ino)
    return result


def _connect(path, expected, mode):
    """Open SQLite on `path`, then prove the descriptor it actually opened is the verified file.

    The connection is returned only when exactly one new regular descriptor appeared and its
    (device, inode) equals `expected`; any other observation closes it unused.
    """
    before = _descriptors()
    try: db = sqlite3.connect('file:' + quote(str(path)) + '?mode=' + mode, uri=True, timeout=5,
                              factory=_AnchoredConnection)
    except sqlite3.Error: raise _unavailable('path_unsafe') from None
    try:
        if [identity for fd, identity in _descriptors().items() if before.get(fd) != identity] != [expected]:
            raise _unavailable('path_unsafe')
    except BaseException:
        db.close()
        raise
    return db


def _read_setting(directory, path, key, *, ledger='slot'):
    """Read one LocalLedger setting read-only from a verified, stable private database."""
    row = _read_db(directory, path, lambda db: db.execute('select data from settings where key=?', (key,)).fetchone(),
                   ledger=ledger)
    return None if row is None else json.loads(row[0])


@contextmanager
def _components(root, names, kinds, *, create=False):
    """Verified descriptors of the state root and each named component; None when one is missing."""
    fds = [_root_fd(root)]
    try:
        for name, kind in zip(names, kinds):
            fd = _open(name, fds[-1], kind, create=create and kind != 'file')
            if fd is None:
                yield None
                return
            fds.append(fd)
        yield fds
    finally:
        for fd in fds: os.close(fd)


def _identity(fds):
    return [(info.st_dev, info.st_ino) for info in map(os.fstat, fds)]


ACTOR_KINDS = ('ancestor', 'private', 'file')
SLOT_KINDS = ('private', 'private')


def _actor_ledger(root, session_ref, *, create=False):
    """The logical actor's ledger; see `_ledger`."""
    return _ledger(root, ('sessions', session_ref), ACTOR_KINDS[:2], create=create)


def root_ledger(state_root):
    """The Claude route's verified state-root ledger (it must already exist); legacy routes keep LocalLedger."""
    ledger = _ledger(_state_root(state_root))
    if ledger is None: raise _unavailable('path_unsafe')
    return ledger


def check_root(state_root):
    """Static checks of the existing state-root ledger and its sidecars, before any construction or work."""
    with _components(_state_root(state_root), (), ()) as fds:
        fd = _open('ledger.sqlite', fds[-1], 'file')
        if fd is None: raise _unavailable('path_unsafe')
        os.close(fd)
        _sidecars(fds[-1])


def _ledger(root, names=(), kinds=(), *, create=False):
    """A Claude worker writable ledger (state root, slot or actor), reached only through a verified link-free path.

    Every ancestor and existing file, including any recovery sidecar, is verified without following links
    or changing a mode. A new database is created exclusively as 0600; SQLite opens it without creating,
    and the main file it actually opened must be the verified inode (`_connect`). SQLite keeps its ordinary
    disk rollback journal and crash recovery. Its native journal I/O is pathname-based: the contract
    requires the private actor directory and its ancestors to stay in place while a connection is in use.
    Locks and logs are anchored to the held directory descriptor. None when absent.
    """
    from quorum_os import AnchoredPath
    from quorum_state import LocalLedger
    path = root.joinpath(*names, 'ledger.sqlite')
    with _components(root, names, kinds, create=create) as fds:
        if fds is None: return None
        directory = fds[-1]
        fd = _open('ledger.sqlite', directory, 'file')
        if fd is None:
            if not create: return None
            try: fd = os.open('ledger.sqlite', os.O_RDWR | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=directory)
            except OSError: raise _unavailable('path_unsafe') from None
            fds.append(fd)
            _verify(os.fstat(fd), 'file')
        else: fds.append(fd)
        # A legitimate recovery journal is a private, owned, single-link regular file and is kept for
        # SQLite's recovery; a link, loose mode, other owner or other type refuses before mutable use.
        _sidecars(directory)
        db = _connect(path, _identity([fd])[0], 'rw')
        try:
            db.anchor = os.dup(directory)
            # Locks and logs under this root open relative to the held verified directory descriptor.
            return LocalLedger.adopt(AnchoredPath(path.parent, held=(db.anchor, str(path.parent))), db)
        except BaseException:
            db.close()
            raise


def _slot_path(root, slot_id):
    return root / 'claude-workers' / slot_id


def _view(root, slot_id):
    """Verified read-only slot record; no constructor creates or chmods anything."""
    with _directory(root, 'claude-workers', slot_id) as directory:
        record = None if directory is None else _read_setting(directory, _slot_path(root, slot_id) / 'ledger.sqlite',
                                                               SLOT_KEY)
    if not isinstance(record, dict) or record.get('slot_id') != slot_id: raise _denied('slot_invalid')
    return record


@contextmanager
def _lock(root, names, name, seconds=10):
    """Bounded lock acquisition; slot/index locks protect short transitions, never API waits.

    The separate create.lock serializes API attempts without preventing owner stop.
    The lock file opens through the held verified directory; an existing unsafe one refuses unchanged.
    """
    from quorum_os import AnchoredPath, FileLock
    with _components(root, names, ('private',) * len(names)) as fds:
        if fds is None: raise _denied('slot_invalid')
        existing = _open(name, fds[-1], 'file')
        if existing is not None: os.close(existing)
        base = root.joinpath(*names)
        path = AnchoredPath(base, held=(fds[-1], str(base))) / name
        deadline = time.monotonic() + seconds
        while True:
            guard = FileLock(path)
            try:
                guard.__enter__()
                break
            except ValueError as error:
                if str(error) != 'worker_already_running' or time.monotonic() >= deadline: raise
                time.sleep(.05)
        try:
            _verify(os.fstat(guard.fd), 'file')
            yield
        finally: guard.__exit__()


def _recover(root, slot_id):
    """Controller-ruled narrow recovery of an existing Claude worker slot database's hot rollback journal.

    Runs only after caller export form, slot syntax and the static path, file and sidecar checks; under the
    fixed root-before-slot locks it opens the existing verified database (main-file proof) and lets SQLite
    restore its last committed state. No schema, setting, grant, handle or generation is written; the record
    is read and fully validated afterwards. A request later denied by that record may already have caused
    this local rollback.
    """
    names = ('claude-workers', slot_id)
    path = _slot_path(root, slot_id) / 'ledger.sqlite'
    def hot():
        with _components(root, names, SLOT_KINDS) as fds:
            if fds is None: return False
            fd = _open('ledger.sqlite', fds[-1], 'file')
            if fd is None: return False
            os.close(fd)
            return _sidecars(fds[-1])
    if not hot(): return
    with _lock(root, names[:1], 'index.lock'), _lock(root, names, 'slot.lock'):
        with _components(root, names, SLOT_KINDS) as fds:
            fd = None if fds is None else _open('ledger.sqlite', fds[-1], 'file')
            if fd is None: return
            try:
                if not _sidecars(fds[-1]): return
                db = _connect(path, _identity([fd])[0], 'rw')
                try: db.execute('pragma schema_version').fetchone()
                except sqlite3.Error: raise _unavailable('path_unsafe') from None
                finally: db.close()
            finally: os.close(fd)


@contextmanager
def _slot(root, slot_id, sid=None, *, index=False):
    """Fixed root-before-slot lock order; the ledger opens only after the path was verified."""
    _recover(root, slot_id)
    record = _view(root, slot_id)
    if sid is not None and record.get('provider_session_id') != sid: raise _denied('session_mismatch')
    with (_lock(root, ('claude-workers',), 'index.lock') if index else nullcontext()):
        with _lock(root, ('claude-workers', slot_id), 'slot.lock'):
            ledger = _ledger(root, ('claude-workers', slot_id), SLOT_KINDS)
            if ledger is None: raise _denied('slot_invalid')
            try: yield ledger
            finally: ledger.db.close()


# --- Scope and records -----------------------------------------------------------------------

def _name(value):
    """The one admissible slug/role domain shared by scopes and worker create input."""
    return isinstance(value, str) and bool(NAME.fullmatch(value))


def normalize_scope(raw):
    """Strict scope schema with explicit defaults; nothing here grants project authority."""
    from quorum_api import ServiceConfig
    if not isinstance(raw, dict) or raw.get('mode') not in OPERATIONS: raise _denied('scope_invalid')
    mode = raw['mode']
    required = {'mode', 'service', 'quorum_slug', 'role', 'project_ref', 'cwd'} | (
        {'access'} if mode == 'create' else {'invite_id'})
    optional = {'coordinator_role', 'dispatch_mode', 'lifetime', 'ttl_seconds', 'operations'}
    if not required <= set(raw) or set(raw) - required - optional: raise _denied('scope_invalid')
    service = raw['service']
    if not isinstance(service, dict) or set(service) != {'origin', 'publishable_key_sha256'}:
        raise _denied('scope_invalid')
    try: ServiceConfig(service['origin'], 'public')
    except (ValueError, TypeError, AttributeError): raise _denied('scope_invalid') from None
    if not isinstance(service['publishable_key_sha256'], str) or not DIGEST.fullmatch(service['publishable_key_sha256']):
        raise _denied('scope_invalid')
    if not _name(raw['quorum_slug']) or not _name(raw['role']): raise _denied('scope_invalid')
    project = raw['project_ref']
    if (not isinstance(project, str) or not 1 <= len(project.encode('utf-8', 'replace')) <= 256
            or any(unicodedata.category(char) == 'Cc' for char in project)):
        raise _denied('scope_invalid')
    cwd = raw['cwd']
    if (not isinstance(cwd, str) or not os.path.isabs(cwd) or os.path.normpath(cwd) != cwd
            or str(Path(cwd).resolve()) != cwd):
        raise _denied('scope_invalid')
    scope = {'mode': mode, 'service': dict(service), 'quorum_slug': raw['quorum_slug'], 'role': raw['role'],
             'project_ref': project, 'cwd': cwd}
    if mode == 'create':
        if raw['access'] not in ('github', 'open'): raise _denied('scope_invalid')
        scope['access'] = raw['access']
    else:
        if not isinstance(raw['invite_id'], str) or not CANONICAL.fullmatch(raw['invite_id']):
            raise _denied('scope_invalid')
        scope['invite_id'] = raw['invite_id']
    coordinator = raw.get('coordinator_role')
    if coordinator is not None and not _name(coordinator): raise _denied('scope_invalid')
    dispatch_mode = raw.get('dispatch_mode', 'inline')
    if dispatch_mode not in ('inline', 'native-subagent') or (dispatch_mode == 'native-subagent' and not coordinator):
        raise _denied('scope_invalid')
    lifetime = raw.get('lifetime', 'bounded')
    if lifetime not in ('bounded', 'until-owner-stop'): raise _denied('scope_invalid')
    if 'ttl_seconds' in raw:
        ttl = raw['ttl_seconds']
        if lifetime != 'bounded' or type(ttl) is not int or not 1 <= ttl <= TTL_LIMIT: raise _denied('scope_invalid')
    else: ttl = DEFAULT_TTL if lifetime == 'bounded' else None
    allowed = OPERATIONS[mode]
    operations = raw.get('operations', list(allowed))
    if (not isinstance(operations, list) or not operations or len(set(map(str, operations))) != len(operations)
            or any(not isinstance(item, str) or item not in allowed for item in operations)):
        raise _denied('scope_invalid')
    scope.update(coordinator_role=coordinator, dispatch_mode=dispatch_mode, lifetime=lifetime, ttl_seconds=ttl,
                 operations=[item for item in allowed if item in operations])
    return scope


def _effective(record):
    """Terminal state, or the applicable deadline: pending expiry only while pending, permission once active."""
    if record['state'] == 'terminal': return 'terminal', record.get('terminal_reason')
    if record['state'] == 'pending' and now() >= record['pending_expires_at']: return 'terminal', 'pending_expired'
    if (record['state'] == 'active' and record['permission_expires_at'] is not None
            and now() >= record['permission_expires_at']):
        return 'terminal', 'permission_expired'
    return record['state'], None


def _settle(ledger, record):
    """Persist an elapsed deadline as terminal under the slot lock; terminal never reopens."""
    state, reason = _effective(record)
    if state == 'terminal' and record['state'] != 'terminal':
        record = {**record, 'state': 'terminal', 'terminal_reason': reason, 'terminal_at': now()}
        ledger.setting(SLOT_KEY, record)
    return record


def _public(record):
    if not record.get('binding'): return None
    state, _ = _effective(record)
    return {**record['binding'], 'state': state, 'lifetime': record['lifetime'],
            'pending_expires_at': record['pending_expires_at'],
            'permission_expires_at': record['permission_expires_at']}


def _create_state(record, state):
    create = record.get('create')
    if not create: return None
    return 'ambiguous' if create.get('state') == 'intent' and state == 'terminal' else create.get('state')


def _summary(root, record, *, session=True):
    state, reason = _effective(record)
    return {'slot_id': record['slot_id'], 'state': state, 'terminal_reason': reason,
            'lifetime': record['lifetime'], 'pending_expires_at': record['pending_expires_at'],
            'permission_expires_at': record['permission_expires_at'], 'binding': _public(record),
            'target': dict(record.get('target') or {}), 'create': _create_state(record, state),
            'session': _session_status(root, record) if session else None}


def _held(directory, name):
    """Whether another holder has the verified lock file; probed with a shared lock, nothing created."""
    import fcntl
    fd = _open(name, directory, 'file')
    if fd is None: return False
    try:
        fcntl.flock(fd, fcntl.LOCK_SH | fcntl.LOCK_NB)
        return False
    except OSError: return True
    finally: os.close(fd)


def _session_status(root, record):
    """Last observed run/offer/offline from a read-only verified view; never liveness, a body or a write."""
    from quorum_listener import LIVE_STATES
    from quorum_native_inbox import CONTROL
    binding = record.get('binding')
    if not binding: return None
    names = ('sessions', binding['session_ref'])
    with _components(root, names, ACTOR_KINDS[:2]) as fds:
        if fds is None: return None
        rows = _read_db(fds[-1], root.joinpath(*names, 'ledger.sqlite'), lambda db: db.execute(
            "select key,data from settings where key in ('listener', ?) or key like 'delivery:%'", (CONTROL,)).fetchall(),
                        ledger='actor')
        if rows is None: return None
        running = _held(fds[-1], 'listener.lock')
    settings = {key: json.loads(data) for key, data in rows}
    listener = settings.get('listener') or {}
    state = listener.get('state', 'stopped')
    # A recorded live run whose lock is free ended without reporting; reported, not rewritten here.
    if not running and state in LIVE_STATES: state = 'exited'
    return {'listener': 'running' if running else 'offline', 'state': state, 'run_id': listener.get('run_id'),
            'reception': 'enabled' if (settings.get(CONTROL) or {}).get('enabled') is True else 'disabled',
            'pending_receipts': sum(value.get('state') == 'offered' for key, value in settings.items()
                                    if key.startswith('delivery:'))}


def _bootstrap(root, slot_id):
    return [sys.executable, str(Path(__file__).with_name('quorum.py')), '--state', str(root),
            'session', 'worker', 'await-binding', slot_id, '--wait-seconds', str(AWAIT_DEFAULT)]


# --- Owner-parent lifecycle ------------------------------------------------------------------

def prepare(state_root, scope):
    """Pending slot only: no profile, auth, API, membership or receive right."""
    sid = actual_session()
    root = _state_root(state_root)
    scope = normalize_scope(scope)
    if scope['cwd'] != str(Path.cwd().resolve()): raise _denied('cwd_mismatch')
    prepared = now()
    permission = None if scope['lifetime'] == 'until-owner-stop' else prepared + scope['ttl_seconds']
    pending = prepared + PENDING_SECONDS if permission is None else min(prepared + PENDING_SECONDS, permission)
    slot_id = str(uuid.uuid4())
    # The ordinary private state root may be created on this parent route, then verified like every worker path.
    os.close(_root_fd(root, create=True))
    _ledger(root, create=True).db.close()
    with _directory(root, 'claude-workers', create=True) as workers:
        try: os.mkdir(slot_id, 0o700, dir_fd=workers)
        except OSError: raise _unavailable('path_unsafe') from None
        os.close(_open(slot_id, workers, 'private'))
    record = {'slot_id': slot_id, 'state': 'pending', 'terminal_reason': None, 'provider_session_id': sid,
              'state_root': str(root), 'scope': scope, 'scope_hash': _digest(scope), 'lifetime': scope['lifetime'],
              'prepared_at': prepared, 'pending_expires_at': pending, 'permission_expires_at': permission,
              'generation': 1, 'binding': None, 'token_hash': None, 'target': {}}
    if scope['mode'] == 'create': record.update(create_request_id=str(uuid.uuid4()), create=None)
    ledger = _ledger(root, ('claude-workers', slot_id), SLOT_KINDS, create=True)
    try: ledger.setting(SLOT_KEY, record)
    finally: ledger.db.close()
    return {'slot_id': slot_id, 'state': 'pending', 'lifetime': scope['lifetime'], 'pending_expires_at': pending,
            'permission_expires_at': permission, 'bootstrap_command': _bootstrap(root, slot_id)}


def _write_capability(root, slot_id, token):
    """Descriptor-relative private temporary, then atomic replacement; only while the slot is pending."""
    data = json.dumps({'version': 1, 'slot_id': slot_id, 'state_root': str(root), 'token': token}).encode()
    with _directory(root, 'claude-workers', slot_id) as directory:
        if directory is None: raise _denied('slot_invalid')
        name = '.capability-' + secrets.token_hex(8) + '.tmp'
        fd = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=directory)
        try:
            try:
                os.fchmod(fd, 0o600)
                view = memoryview(data)
                while view: view = view[os.write(fd, view):]
                os.fsync(fd)
            finally: os.close(fd)
            # POSIX rename atomically replaces an earlier unactivated attempt's file.
            os.rename(name, 'capability.json', src_dir_fd=directory, dst_dir_fd=directory)
        except BaseException:
            try: os.unlink(name, dir_fd=directory)
            except FileNotFoundError: pass
            raise


def attest(state_root, slot_id, native_handle, spawn_tool_use_id=None):
    """Same actual SID, one-time locked activation from the owner-parent's exact native receipt."""
    sid = actual_session()
    root = _state_root(state_root)
    slot_id = _slot_uuid(slot_id)
    handle = _label(native_handle)
    tool = None if spawn_tool_use_id is None else _label(spawn_tool_use_id)
    check_root(root)
    with _slot(root, slot_id, sid, index=True) as ledger:
        record = _settle(ledger, ledger.setting(SLOT_KEY))
        if record['state'] == 'terminal': raise _terminal(record['terminal_reason'])
        if record['state'] == 'active':
            binding = record['binding']
            if binding['native_worker_handle'] != handle or binding.get('spawn_tool_use_id') != tool:
                raise _denied('binding_changed')
            return _public(record)
        actor = str(uuid.uuid5(uuid.UUID(sid), handle))
        session_ref = PREFIX + actor
        index = _ledger(root)
        if index is None: raise _unavailable('path_unsafe')
        try:
            claimed = index.setting(INDEX + session_ref)
            # One actor, one slot: a stopped or terminal slot is never regranted to another slot.
            if claimed is not None and claimed != {'slot_id': slot_id, 'provider_session_id': sid}:
                raise _denied('actor_claimed')
            binding = {'provider': PROVIDER, 'binding_version': 1, 'provider_session_id': sid,
                       'native_worker_handle': handle, 'logical_actor_id': actor, 'session_ref': session_ref,
                       'slot_id': slot_id, 'generation': record['generation'],
                       'attestation': 'owner-parent-native-receipt'}
            if tool is not None: binding['spawn_tool_use_id'] = tool
            binding['authority_hash'] = _digest({**binding, 'scope_hash': record['scope_hash'],
                                                 'lifetime': record['lifetime'], 'state_root': record['state_root'],
                                                 'permission_expires_at': record['permission_expires_at']})
            index.setting(INDEX + session_ref, {'slot_id': slot_id, 'provider_session_id': sid})
        finally: index.db.close()
        token = secrets.token_urlsafe(32)
        _write_capability(root, slot_id, token)
        record = {**record, 'state': 'active', 'binding': binding, 'activated_at': now(),
                  'token_hash': hashlib.sha256(token.encode()).hexdigest()}
        ledger.setting(SLOT_KEY, record)
        return _public(record)


def await_binding(state_root, slot_id, wait_seconds=AWAIT_DEFAULT):
    """Bounded idle wait in the child's own foreground tool; timeout tombstones the pending slot."""
    sid = actual_session()
    root = _state_root(state_root)
    slot_id = _slot_uuid(slot_id)
    if type(wait_seconds) is not int or not 1 <= wait_seconds <= AWAIT_LIMIT: raise _denied('wait_invalid')
    waited = 0.0
    while True:
        with _slot(root, slot_id, sid) as ledger:
            record = _settle(ledger, ledger.setting(SLOT_KEY))
            if record['state'] == 'active':
                return {'state': 'active', 'binding': _public(record),
                        'capability_file': str(_slot_path(root, slot_id) / 'capability.json')}
            if record['state'] == 'terminal': raise _terminal(record['terminal_reason'])
            if waited >= wait_seconds:
                # The same slot lock serializes this with attest: a late attest cannot revive it.
                ledger.setting(SLOT_KEY, {**record, 'state': 'terminal', 'terminal_reason': 'await_timeout',
                                          'terminal_at': now()})
                raise _terminal('await_timeout')
        step = min(.25, wait_seconds - waited)
        sleep(step)
        waited += step


def slot_status(state_root, slot_id):
    """Owner metadata by the exact actual SID; never an envelope, body or bearer."""
    sid = actual_session()
    root = _state_root(state_root)
    record = _view(root, _slot_uuid(slot_id))
    if record.get('provider_session_id') != sid: raise _denied('session_mismatch')
    return _summary(root, record)


def stop_slot(state_root, slot_id, *, sid=None):
    """Terminal revocation of this slot, then a stop request for its exact active run only."""
    import quorum_native_inbox as inbox
    sid = sid or actual_session()
    root = _state_root(state_root)
    slot_id = _slot_uuid(slot_id)
    with _slot(root, slot_id, sid) as ledger:
        record = _settle(ledger, ledger.setting(SLOT_KEY))
        if record['state'] != 'terminal':
            record = {**record, 'state': 'terminal', 'terminal_reason': 'stopped', 'terminal_at': now()}
            ledger.setting(SLOT_KEY, record)
    binding = record.get('binding')
    try: session = binding and _actor_ledger(root, binding['session_ref'])
    except NativeInboxError as error:
        # The slot is already terminal locally; an unsafe actor path is reported and left unchanged.
        return {**_summary(root, record, session=False), 'run_stop': error.reason}
    if session:
        try:
            agent = session.setting('session-agent')
            # Unresolved offers stay; nothing is acknowledged automatically.
            if agent: inbox.stop(session, agent)
        finally: session.db.close()
    return _summary(root, record)


def resolve_actor(state_root, session_ref):
    """A logical selector resolves only through the verified root index and its exact owning SID."""
    sid = actual_session()
    root = _state_root(state_root)
    with _directory(root) as directory:
        claimed = _read_setting(directory, root / 'ledger.sqlite', INDEX + session_ref, ledger='state')
    if not isinstance(claimed, dict) or claimed.get('provider_session_id') != sid:
        raise _denied('session_mismatch')
    return _slot_uuid(claimed.get('slot_id'))


# --- Capability authority --------------------------------------------------------------------

def _same_service(scope, origin, publishable_key):
    service = scope['service']
    return (origin == service['origin'] and isinstance(publishable_key, str)
            and hmac.compare_digest(hashlib.sha256(publishable_key.encode()).hexdigest(),
                                    service['publishable_key_sha256']))


def _check_syntax(operation, arguments):
    """Argument rules that need no authority record: forbidden modes, flags and request overrides."""
    get = arguments.get
    if operation == 'create' and get('request_id') is not None: raise _denied('request_override')
    if operation == 'accept' and not (get('transport') == TRANSPORT and get('parent_thread_id') is None
                                      and not get('membership_only') and get('client') is None
                                      and not get('anonymous') and bool(get('origin')) == bool(get('publishable_key'))):
        raise _denied('scope_mismatch')
    if operation == 'connect' and not (get('transport') == TRANSPORT and get('parent_thread_id') is None):
        raise _denied('scope_mismatch')


def _check_arguments(record, operation, arguments):
    scope, target = record['scope'], record.get('target') or {}
    get = arguments.get
    def require(condition):
        if not condition: raise _denied('scope_mismatch')
    _check_syntax(operation, arguments)
    if operation == 'create':
        require(get('quorum') == scope['quorum_slug'] and get('access') == scope['access']
                and get('role') in (None, scope['role']))
    elif operation == 'accept':
        require(get('invite_id') == scope['invite_id'])
        if get('origin'): require(_same_service(scope, get('origin'), get('publishable_key')))
    elif operation == 'connect':
        require(get('quorum') == scope['quorum_slug'] and get('role') == scope['role']
                and get('project_ref') == scope['project_ref'] and get('accept_tasks_from') == scope['coordinator_role']
                and get('dispatch_mode', 'inline') == scope['dispatch_mode'])
        # No registration until the approved membership or creation (including open access) is known.
        if not target.get('quorum_id') or (scope['mode'] == 'create'
                                           and (record.get('create') or {}).get('state') != 'done'):
            raise _denied('target_pending')
    elif operation in ('receive', 'received', 'reply') and not target.get('agent_id'):
        raise _denied('target_pending')


def _capability_token(raw, root, slot_id):
    """Bounded strict capability JSON with its full structure; returns the bearer, never compares it."""
    capability = _strict_json(raw, 'capability_invalid')
    token = capability.get('token')
    if (set(capability) != {'version', 'slot_id', 'state_root', 'token'} or type(capability['version']) is not int
            or capability['version'] != 1 or capability['slot_id'] != slot_id or capability['state_root'] != str(root)
            or not isinstance(token, str) or not 1 <= len(token) <= 256):
        raise _denied('capability_invalid')
    return token


def _generated_slot(root, capability_path):
    """Only the generated slot path under this state root; arbitrary external capability files are denied."""
    path = Path(os.path.abspath(str(capability_path)))
    if (path.name != 'capability.json' or path.parent.parent != root / 'claude-workers'
            or not CANONICAL.fullmatch(path.parent.name)):
        raise _denied('capability_invalid')
    return path, path.parent.name


def authorize(state_root, capability_path, operation, arguments):
    """Read-only validation before any worker ledger, profile, auth or API; never returns the bearer."""
    sid = actual_session()
    root = _state_root(state_root)
    if operation not in OPERATIONS['create'] + ('accept',): raise _denied('operation_denied')
    path, slot_id = _generated_slot(root, capability_path)
    with _directory(root, 'claude-workers', slot_id) as directory:
        if directory is None: raise _denied('slot_invalid')
        record = _read_setting(directory, path.parent / 'ledger.sqlite', SLOT_KEY)
        if not isinstance(record, dict) or record.get('slot_id') != slot_id: raise _denied('slot_invalid')
        state, reason = _effective(record)
        if state == 'terminal': raise _terminal(reason)
        if state != 'active': raise _denied('slot_pending')
        token = _capability_token(_read_file(directory, 'capability.json', 'capability_invalid'), root, slot_id)
    binding = record['binding']
    # Record-dependent: the bearer must match this slot's stored hash.
    if (not isinstance(record.get('token_hash'), str)
            or not hmac.compare_digest(hashlib.sha256(token.encode()).hexdigest(), record['token_hash'])):
        raise _denied('capability_invalid')
    expected = {key: value for key, value in binding.items() if key != 'authority_hash'}
    if (record['generation'] != 1 or binding.get('generation') != 1 or record['state_root'] != str(root)
            or record['scope_hash'] != _digest(record['scope'])
            or binding.get('authority_hash') != _digest({**expected, 'scope_hash': record['scope_hash'],
                                                         'lifetime': record['lifetime'],
                                                         'state_root': record['state_root'],
                                                         'permission_expires_at': record['permission_expires_at']})):
        raise _denied('binding_changed')
    if record['provider_session_id'] != sid or binding['provider_session_id'] != sid:
        raise _denied('session_mismatch')
    if str(Path.cwd().resolve()) != record['scope']['cwd']: raise _denied('cwd_mismatch')
    if operation not in record['scope']['operations']: raise _denied('operation_denied')
    _check_arguments(record, operation, arguments)
    return {'authority': 'same-user-capability', 'operation': operation, 'arguments': dict(arguments),
            'state_root': str(root), 'slot_id': slot_id, 'capability_file': str(path), 'binding': _public(record),
            'scope': record['scope'], 'target': dict(record.get('target') or {}),
            'create': record.get('create'), 'session_ref': binding['session_ref']}


def guard(context):
    """Recheck binding, scope, generation, token and deadlines at a dispatch or blocking boundary."""
    try:
        current = authorize(context['state_root'], context['capability_file'], context['operation'],
                            context['arguments'])
    except NativeInboxError: raise
    except Exception: raise _denied('binding_changed') from None
    if (current['binding']['authority_hash'] != context['binding']['authority_hash']
            or current['slot_id'] != context['slot_id']):
        raise _denied('binding_changed')
    return current


def pin_target(context, quorum_id, agent_id=None, *, membership_id=None):
    """Locked once-only fill of the declared target; a changed ID is denied."""
    root = _state_root(context['state_root'])
    with _slot(root, context['slot_id'], context['binding']['provider_session_id']) as ledger:
        record = ledger.setting(SLOT_KEY)
        target = dict(record.get('target') or {})
        for key, value in (('quorum_id', quorum_id), ('agent_id', agent_id), ('membership_id', membership_id)):
            if value is None: continue
            if not isinstance(value, str) or not value or len(value) > 128: raise _denied('target_mismatch')
            if target.get(key) not in (None, value): raise _denied('target_mismatch')
            target[key] = value
        if target != record.get('target'): ledger.setting(SLOT_KEY, {**record, 'target': target})
    context['target'] = target
    return target


def service_check(context, explicit=None):
    """Pinned public service identity before resolve_profile, a legacy credential or any token.

    Reads only this state's bounded nonsecret config; no registry, credential or proof is touched.
    """
    scope = context['scope']
    if explicit is not None and not _same_service(scope, explicit.origin, explicit.publishable_key):
        raise _denied('service_mismatch')
    root = _state_root(context['state_root'])
    with _directory(root) as directory:
        fd = _open('config.json', directory, 'file')
        if fd is None:
            if explicit is None: raise _unavailable('service_unconfigured')
            return
        with os.fdopen(fd, 'rb') as source: raw = source.read(JSON_LIMIT + 1)
    if len(raw) > JSON_LIMIT: raise _unavailable('path_unsafe')
    config = _strict_json(raw, 'service_mismatch')
    if set(config) != {'origin', 'publishable_key'} or not _same_service(scope, config['origin'],
                                                                         config['publishable_key']):
        raise _denied('service_mismatch')


def same_resolved(context, config):
    if not _same_service(context['scope'], config.origin, config.publishable_key): raise _denied('service_mismatch')


# --- CLI routing: every worker route validates before the root ledger constructor -------------

def selected(args):
    """This invocation names a Claude worker route (capability, slot management or logical selector)."""
    return (getattr(args, 'worker_capability', None) is not None
            or (args.command == 'session' and (args.action == 'worker'
                                               or str(args.session_id or '').startswith(PREFIX))))


def manage(args):
    """Owner-parent slot management and logical status/stop; None when a capability route follows."""
    if args.command != 'session' or getattr(args, 'worker_capability', None) is not None:
        if args.command == 'session' and str(args.session_id or '').startswith(PREFIX):
            raise _denied('selector_conflict')
        return None
    if args.action == 'worker':
        action = args.worker_action
        if action == 'prepare':
            actual_session()
            raw = sys.stdin.buffer.read(JSON_LIMIT + 1)
            if len(raw) > JSON_LIMIT: raise _denied('scope_invalid')
            return prepare(args.state, _strict_json(raw, 'scope_invalid'))
        if action == 'attest': return attest(args.state, args.slot, args.native_handle, args.spawn_tool_use_id)
        if action == 'await-binding': return await_binding(args.state, args.slot, args.wait_seconds)
        if action == 'status': return slot_status(args.state, args.slot)
        if action == 'stop': return stop_slot(args.state, args.slot)
        raise _denied('operation_denied')
    # A logical selector is metadata-only supervision by the exact owning actual SID.
    if args.action not in ('status', 'stop'): raise _denied('management_only')
    slot_id = resolve_actor(args.state, args.session_id)
    return slot_status(args.state, slot_id) if args.action == 'status' else stop_slot(args.state, slot_id)


def preflight(args):
    """Capability, scope and operation arguments, before any ledger, profile, auth or API."""
    if args.command == 'session':
        if args.session_id is not None: raise _denied('selector_conflict')
        if args.action not in SESSION_ACTIONS:
            raise NativeInboxError('native_inbox_transport_conflict')
        operation = args.action
        names = {'connect': ('quorum', 'role', 'project_ref', 'transport', 'parent_thread_id', 'accept_tasks_from',
                             'dispatch_mode'),
                 'receive': ('wait_seconds',), 'received': ('message_id',), 'reply': ('message_id', 'priority')}
        arguments = {name: getattr(args, name, None) for name in names.get(operation, ())}
    elif args.command == 'create':
        operation = 'create'
        arguments = {'quorum': args.quorum, 'access': args.access, 'role': args.role, 'request_id': args.request_id}
        context = _authorize_route(args, operation, arguments)
        # The existing create JSON is read once here, so a local denial still precedes every ledger.
        args.worker_create_body = create_body(args, context)
        return context
    elif args.command == 'accept-id':
        operation = 'accept'
        try: invite = str(uuid.UUID(args.invite_id))
        except (ValueError, TypeError, AttributeError): raise _denied('scope_mismatch') from None
        arguments = {'invite_id': invite, **{name: getattr(args, name, None) for name in (
            'transport', 'parent_thread_id', 'membership_only', 'client', 'anonymous', 'origin', 'publishable_key')}}
    else: raise NativeInboxError('native_inbox_transport_conflict')
    return _authorize_route(args, operation, arguments)


def _authorize_route(args, operation, arguments):
    """Every independently decidable check first; only then the narrow slot recovery and full authorization.

    Static root files, the generated capability path, its bounded strict JSON and full structure, forbidden
    modes/flags/request overrides and the create input (stdin read once) all deny before recovery. Only the
    record-dependent bearer hash, SID, scope, state, target and expiry checks follow it (controller ruling).
    """
    check_root(args.state)
    slot_id = _preverify(args.state, args.worker_capability, operation, arguments)
    if operation == 'create': args.worker_create_input = create_input(args)
    if operation != 'status' and slot_id: _recover(_state_root(args.state), slot_id)
    return authorize(args.state, args.worker_capability, operation, arguments)


def _preverify(state_root, capability_path, operation, arguments):
    """Record-independent capability and argument checks; the slot ID when a capability file exists."""
    actual_session()
    root = _state_root(state_root)
    if operation not in OPERATIONS['create'] + ('accept',): raise _denied('operation_denied')
    _check_syntax(operation, arguments)
    _, slot_id = _generated_slot(root, capability_path)
    with _components(root, ('claude-workers', slot_id), SLOT_KINDS) as fds:
        if fds is None: return None
        fd = _open('capability.json', fds[-1], 'file')
        if fd is None: return None  # A pending slot has none; authorization denies it without recovery.
        os.close(fd)
        _capability_token(_read_file(fds[-1], 'capability.json', 'capability_invalid'), root, slot_id)
    return slot_id


# --- Worker operations through the existing create/accept/registration/delivery code ---------

def _worker_binding(context):
    binding = context['binding']
    return {key: binding[key] for key in ('provider', 'session_ref', 'slot_id', 'logical_actor_id',
                                          'provider_session_id', 'native_worker_handle', 'authority_hash')} | {
        'capability_file': context['capability_file']}


def _record_matches(record, context):
    if (record.get('engine') != PROVIDER or record.get('transport') != TRANSPORT
            or record.get('session_ref') != context['session_ref']
            or (record.get('registration') or {}).get('session_ref') != context['session_ref']
            or record.get('worker_binding') != _worker_binding(context)):
        raise _denied('binding_invalid')


def _api(auth_ledger, context, explicit=None):
    """Pinned public service first (R2), then the existing profile/auth seam, then a guarded API."""
    from quorum_live import api_for
    service_check(context, explicit)
    api, config, store = api_for(auth_ledger)
    same_resolved(context, config)
    return api, config, store


def capability_status(context):
    root = _state_root(context['state_root'])
    return {**_summary(root, _view(root, context['slot_id'])), 'session_ref': context['session_ref'],
            'transport': TRANSPORT}


def session_dispatch(args, auth_ledger, register_agent, context):
    """Capability-scoped session actions for the logical actor; never the parent pipe, ledger or device."""
    action = args.action
    if action == 'status': return capability_status(context)
    if action == 'stop':
        return stop_slot(context['state_root'], context['slot_id'], sid=context['binding']['provider_session_id'])
    context = {**guard(context), 'arguments': context['arguments']}
    check = lambda: guard(context)
    session_ref = context['session_ref']
    # Verified before any profile or API work; only connect may create the actor's private ledger.
    ledger = _actor_ledger(_state_root(context['state_root']), session_ref, create=action == 'connect')
    if ledger is None: raise ValueError('session_not_connected')
    try:
        record = ledger.setting('session-agent')
        if record is not None: _record_matches(record, context)
        api, _, _ = _api(auth_ledger, context)
        guarded = Guarded(api, check)
        with _effect(guarded.effect):
            return _session_action(args, auth_ledger, register_agent, context, ledger, record, guarded, check)
    finally: ledger.db.close()


def _session_action(args, auth_ledger, register_agent, context, ledger, record, guarded, check):
    """One capability-scoped action over the shared delivery code; `guarded` tracks what was dispatched."""
    import quorum_native_inbox as inbox
    from quorum_delegation import grant
    from quorum_release import ReleaseGuard
    from quorum_session import PRIORITIES
    action, session_ref, scope = args.action, context['session_ref'], context['scope']
    if action in ('connect', 'receive'):
        release = ReleaseGuard(guarded, ledger)
        release.check()
        release.running()
    if action == 'connect':
        ledger.setting('listener-mode', 'claude_tool_inbox')
        data = {'engine': PROVIDER, 'session_ref': session_ref, 'project_ref': scope['project_ref'],
                'cwd': scope['cwd']}
        if record and (record['registration'] != data or record['role'] != scope['role']
                       or record['quorum'] != scope['quorum_slug']):
            raise ValueError('binding_conflict')
        result = register_agent(guarded, auth_ledger, scope['quorum_slug'], scope['role'], data,
                                device_key='session-device:' + session_ref,
                                expected_quorum_id=context['target']['quorum_id'])
        with _effect({'state': 'completed', 'operation': 'agent_join', 'quorum_id': result['quorum_id'],
                      'agent_id': result['agent_id']}):
            pin_target(context, result['quorum_id'], result['agent_id'])
            guarded.check()
        fresh = {**result, **data, 'registration': data, 'role': scope['role'], 'quorum': scope['quorum_slug'],
                 'transport': TRANSPORT, 'worker_binding': _worker_binding(context)}
        # Only this scope's coordinator, resolved now: no grant is copied from a parent or another binding.
        if scope['coordinator_role']:
            peer = guarded.call('resolve_role', {'quorum_id': fresh['quorum_id'], 'role': scope['coordinator_role'],
                                                 'project_ref': fresh['project_ref']})
            previous = (record or {}).get('delegation')
            if previous and previous['coordinator_id'] != peer['agent_id']: raise ValueError('coordinator_changed')
            fresh['delegation'] = grant(fresh, peer['agent_id'], scope['coordinator_role'],
                                        dispatch_mode=scope['dispatch_mode'])
        ledger.setting('session-agent', fresh)
        reception = inbox.initialize_control(ledger, new_binding=record is None)
        return {**result, 'session_ref': session_ref, 'transport': TRANSPORT, 'reception': reception,
                'listener': 'offline', 'worker_binding': fresh['worker_binding'],
                'delegation': fresh.get('delegation')}
    if not record: raise ValueError('session_not_connected')
    # The shared delivery code reuses this tracker, so every dispatched operation stays known.
    if action == 'receive':
        return inbox.receive(guarded, auth_ledger, ledger, record, args.wait_seconds, authorize=check)
    ReleaseGuard(guarded, ledger).check()
    if action == 'received': return inbox.confirm(guarded, ledger, record, args.message_id, authorize=check)
    if action == 'reply':
        text = sys.stdin.buffer.read(16385).decode('utf-8')
        if not text or len(text.encode()) > 16384: raise ValueError('invalid_message')
        priority = getattr(args, 'priority', 'normal') or 'normal'
        if priority not in PRIORITIES: raise ValueError('invalid_priority')
        return inbox.reply(guarded, ledger, record, args.message_id, text, priority, authorize=check)
    raise NativeInboxError('native_inbox_transport_conflict')


LOCAL_CODES = {'session_delivery_unknown', 'session_binding_denied', 'session_not_connected', 'binding_conflict',
               'coordinator_changed', 'invalid_response', 'invalid_message', 'invalid_priority', 'worker_already_running'}


@contextmanager
def _effect(source):
    """Every local failure after a dispatch keeps its known or uncertain remote effect.

    `source` is the effect dict or a callable returning it (None before any dispatch). A service
    error reports its own call's outcome and passes unchanged; nothing here depends on a local write.
    """
    try: yield
    except QuorumError: raise
    except Exception as error:
        effect = source() if callable(source) else source
        if not effect: raise
        if isinstance(error, NativeInboxError):
            if error.remote_effect: raise
            raise error.dispatched(effect) from None
        code = str(error) if isinstance(error, ValueError) and str(error) in LOCAL_CODES else 'operation_failed'
        reason = 'evidence_unpersisted' if isinstance(error, (OSError, sqlite3.Error)) else None
        raise NativeInboxError(code, reason, remote_effect=effect) from None


def accept_worker(args, ledger, context):
    """The worker's own accept-id: validated before profile/login; pins the returned membership once."""
    from quorum_api import ServiceConfig
    from quorum_invitations import public_service
    from quorum_profiles import AnonymousCredentials, ensure_login, resolve_profile
    context = {**guard(context), 'arguments': context['arguments']}
    explicit = ServiceConfig(args.origin, args.publishable_key) if args.origin else None
    if explicit is not None: public_service(explicit)
    service_check(context, explicit)
    # A fresh state records only the already pinned explicit service through the common locked writer.
    if explicit is not None: resolve_profile(ledger.root, explicit)
    api, config, store = _api(ledger, context, explicit)
    public_service(config)
    if isinstance(store, AnonymousCredentials): raise ValueError('anonymous_state_conflict')
    ensure_login(config, store)
    invite_id = context['scope']['invite_id']
    guard(context)
    sent = False
    def dispatched():
        nonlocal sent
        sent = True
    try:
        accepted = guarded_call(api, 'invite_accept_id', {'invite_id': invite_id}, 'accept-id:' + invite_id,
                                before_request=lambda: guard(context), on_dispatch=dispatched)
    except Exception:
        if not sent: raise
        # No response observed: a local refusal now cannot promise an unused invitation.
        try: guard(context)
        except NativeInboxError as error:
            raise error.dispatched({'state': 'uncertain', 'operation': 'invite_accept_id'}) from None
        raise
    effect = {'state': 'accepted', 'operation': 'invite_accept_id'}
    with _effect(effect):
        effect.update(quorum_id=accepted['quorum_id'], membership_id=accepted['membership_id'])
        # Pin first: a membership accepted remotely stays recorded even if a stop lands meanwhile.
        pin_target(context, accepted['quorum_id'], membership_id=accepted['membership_id'])
        guard(context)
    return {'quorum_id': accepted['quorum_id'], 'membership_id': accepted['membership_id'],
            'membership_only': True, 'native_preflight': 'not_applicable', 'transport': TRANSPORT,
            'worker_preflight': 'passed', 'session_ref': context['session_ref']}


def create_input(args):
    """The existing create JSON, read once: only slug and role, consistent with the positional slug and --role.

    Every supplied slug or role (positional, --role or JSON, including an explicit null) must be in the scope
    name domain; nothing outside it can match an approved scope, so it denies before any recovery.
    """
    if not _name(args.quorum) or (args.role is not None and not _name(args.role)): raise _denied('scope_mismatch')
    if not getattr(args, 'json_stdin', False): raise _denied('scope_mismatch')
    raw = sys.stdin.buffer.read(JSON_LIMIT + 1)
    if len(raw) > JSON_LIMIT: raise _denied('scope_mismatch')
    data = _strict_json(raw, 'scope_mismatch')
    if (set(data) - {'slug', 'role'} or not all(_name(value) for value in data.values())
            or data.get('slug', args.quorum) != args.quorum
            or (args.role is not None and data.get('role', args.role) != args.role)):
        raise _denied('scope_mismatch')
    return data


def create_body(args, context):
    """The `quorum_create` payload: only the approved slug, matching `public.quorum_create(p_slug, p_request_id)`.

    The input may also carry the approved role, validated here as intent; the role stays local scope and is
    applied at connect/agent registration, never sent to the create RPC. The request ID comes from the slot.
    """
    scope = context['scope']
    data = getattr(args, 'worker_create_input', None)
    if data is None: data = create_input(args)
    if (data.get('slug', scope['quorum_slug']) != scope['quorum_slug']
            or data.get('role', scope['role']) != scope['role']):
        raise _denied('scope_mismatch')
    return {'slug': scope['quorum_slug']}


def _set_create(context, value):
    """Persist progress, also after a stop; return the prior record and this write's private identity."""
    root = _state_root(context['state_root'])
    with _slot(root, context['slot_id'], context['binding']['provider_session_id']) as ledger:
        previous = ledger.setting(SLOT_KEY)
        attempt = str(uuid.uuid4())
        ledger.setting(SLOT_KEY, {**previous, 'create': value, 'create_attempt_id': attempt})
        return previous, attempt


def _restore_unsent_create(context, previous, attempt):
    """Undo only this unsubmitted intent; another writer's dispatch or target evidence always wins."""
    root = _state_root(context['state_root'])
    with _slot(root, context['slot_id'], context['binding']['provider_session_id']) as ledger:
        record = ledger.setting(SLOT_KEY)
        if record.get('create_attempt_id') != attempt or record.get('target') != previous.get('target'): return
        restored = {**record, 'create': previous.get('create')}
        if 'create_attempt_id' in previous: restored['create_attempt_id'] = previous['create_attempt_id']
        else: restored.pop('create_attempt_id', None)
        ledger.setting(SLOT_KEY, restored)


def _send(context, api, operation, payload, request_id, step):
    """Serialize intent through dispatch: a predecessor's intent cannot still be awaiting cancellation."""
    guard(context)
    root = _state_root(context['state_root'])
    with _lock(root, ('claude-workers', context['slot_id']), 'create.lock'):
        guard(context)
        previous, attempt = _set_create(context, {'state': 'intent', 'step': step})
        try: guard(context)
        except NativeInboxError:
            _restore_unsent_create(context, previous, attempt)
            raise
        sent = False
        def dispatched():
            nonlocal sent
            sent = True
        try:
            return guarded_call(api, operation, payload, request_id,
                                before_request=lambda: guard(context), on_dispatch=dispatched)
        except Exception:
            if not sent:
                _restore_unsent_create(context, previous, attempt)
                raise
            try: guard(context)
            except NativeInboxError:
                raise NativeInboxError('native_worker_create_ambiguous', 'create_unresolved',
                                       remote_effect={'state': 'uncertain', 'operation': operation}) from None
            raise


def _completed(context, step, effect):
    """A request that returned after a stop completed remotely: record that, never roll back or redo it."""
    try: guard(context)
    except NativeInboxError as error:
        refusal = error.dispatched(effect)
        # Best-effort evidence: a failed write cannot replace or hide the dispatched effect.
        try: _set_create(context, {'state': 'completed_after_stop', 'step': step})
        except Exception: pass
        raise refusal from None


def create_worker(args, context, resolve_profile, api_factory):
    """Scoped create through the existing RPCs with the slot's stable request identity.

    `resolve_profile` and `api_factory` are the caller's existing profile/auth seams, injected
    so tests can use fakes; they run only after every local check and the pinned service match.
    """
    root = _state_root(context['state_root'])
    scope = context['scope']
    payload = getattr(args, 'worker_create_body', None) or create_body(args, context)
    context = {**guard(context), 'arguments': context['arguments']}
    record = _view(root, context['slot_id'])
    result = {'quorum_id': None, 'slug': scope['quorum_slug'], 'access': scope['access']}
    if (record.get('create') or {}).get('state') == 'done':
        return {**result, 'quorum_id': record['target']['quorum_id']}
    service_check(context)
    config, store = resolve_profile(root)
    same_resolved(context, config)
    api = api_factory(config, store)
    quorum_id = (record.get('target') or {}).get('quorum_id')
    effect = {'state': 'completed', 'operation': 'quorum_create'}
    if quorum_id is None:
        created = _send(context, api, 'quorum_create', payload, record['create_request_id'], 'create')
        with _effect(effect):
            quorum_id = created.get('quorum_id') if isinstance(created, dict) else None
            if not isinstance(quorum_id, str): raise ValueError('invalid_response')
            effect['quorum_id'] = quorum_id
            pin_target(context, quorum_id)
            _completed(context, 'create', effect)
    else: effect['quorum_id'] = quorum_id
    # The quorum exists from here on (this or an earlier call created it); no later step is effect-free.
    with _effect(effect):
        if scope['access'] == 'open':
            # The existing stable target-specific access identity; registration waits for its known outcome.
            _send(context, api, 'quorum_set_access', {'quorum_id': quorum_id, 'access': 'open'}, 'access:' + quorum_id,
                  'access')
            effect['operation'] = 'quorum_set_access'
            _completed(context, 'access', effect)
        _set_create(context, {'state': 'done', 'quorum_id': quorum_id})
    return {**result, 'quorum_id': quorum_id}
