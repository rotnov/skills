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
ADOPTED = 'claude-worker-adopt:'
RETIRED = 'claude-worker-retired:'
JSON_LIMIT = 16384
PENDING_SECONDS = 120
DEFAULT_TTL, TTL_LIMIT = 900, 3600
AWAIT_DEFAULT, AWAIT_LIMIT = 60, 60
LABEL_LIMIT = 256
OPERATIONS = {'create': ('create', 'connect', 'receive', 'received', 'reply', 'status', 'stop', 'task-check'),
              'accept': ('accept', 'connect', 'receive', 'received', 'reply', 'status', 'stop', 'task-check'),
              # The owner's own existing membership: no create or invitation. Once the parent agent is retired,
              # knowledge and update (owner maintenance) run only through this worker, on the parent's request.
              'adopt': ('connect', 'receive', 'received', 'reply', 'status', 'stop', 'task-check', 'knowledge',
                        'update')}
# A create/accept scope may carry an optional member knowledge subset; it adds the `knowledge` operation for
# exactly these commands (`write` only as `propose`). Verification, administration, audit, catalog, sources and
# capture stay with an owner binding. The server's own space ACL still decides every call.
# It may also opt in to `update` (`"update": true`): the same `update` commands as an adopt scope, run as this
# worker's own agent on the parent's brief; the server still decides owner-only operations (`owner_required`).
KNOWLEDGE = ('search', 'read', 'propose')
KNOWLEDGE_ACTIONS = {'search': 'search', 'read': 'read', 'write': 'propose'}
# standing-check is added to a scope's operations only when it carries standing grants.
KNOWN = tuple(dict.fromkeys([name for names in OPERATIONS.values() for name in names] + ['standing-check']))
SESSION_ACTIONS = ('connect', 'receive', 'received', 'reply', 'status', 'stop', 'task-check', 'standing-check',
                   'park', 'hold', 'awaiting')
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
    """Owner/permission proof uses POSIX descriptors or Windows handles (`quorum_winproof`); elsewhere this
    route fails closed (never chmods or changes an ACL)."""
    if os.name == 'nt':
        from quorum_winproof import available
        return available()
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


def _windows(action):
    """Run a Windows handle proof step (`quorum_winproof`); any refusal is the fixed path_unsafe."""
    from quorum_winproof import Unsafe
    try: return action()
    except (Unsafe, OSError): raise _unavailable('path_unsafe') from None


def _verify_fd(fd, kind):
    """`_verify` of an open descriptor; on Windows its owner and DACL are read from the handle."""
    if os.name == 'nt':
        from quorum_winproof import verify
        _windows(lambda: verify(fd, kind, 'claude'))
    else: _verify(os.fstat(fd), kind)


def _open(name, parent, kind, *, create=False):
    """Open one component relative to its verified parent; a link, swap or loose mode is refused unchanged.

    On Windows a creation receives an owner-only protected DACL atomically, like mkdir 0700.
    """
    if os.name == 'nt':
        from quorum_winproof import open_verified
        return _windows(lambda: open_verified(parent, name, kind, 'claude', create=create))
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


def _mkdir_new(name, parent):
    """Create a new private directory relative to its verified parent; an existing entry refuses."""
    if os.name == 'nt':
        from quorum_winproof import open_at
        os.close(_windows(lambda: open_at(parent, name, directory=True, create=True, exclusive=True)))
        return
    try: os.mkdir(name, 0o700, dir_fd=parent)
    except OSError: raise _unavailable('path_unsafe') from None


def _create_new(name, parent):
    """Exclusively create a private regular file relative to its verified parent; a writable descriptor."""
    if os.name == 'nt':
        from quorum_winproof import open_at
        return _windows(lambda: open_at(parent, name, directory=False, create=True, exclusive=True, write=True))
    try: return os.open(name, os.O_RDWR | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=parent)
    except OSError: raise _unavailable('path_unsafe') from None


def _rename_at(directory, source, target):
    """Rename relative to the held verified directory (FileRenameInformation with its handle on Windows)."""
    if os.name == 'nt':
        from quorum_winproof import rename_at
        _windows(lambda: rename_at(directory, source, target))
    else: os.rename(source, target, src_dir_fd=directory, dst_dir_fd=directory)


def _unlink_at(directory, name):
    """Remove a file relative to the held verified directory; an absent one is ignored."""
    if os.name == 'nt':
        from quorum_winproof import Unsafe, unlink_at
        try: unlink_at(directory, name)
        except FileNotFoundError: pass
        except (Unsafe, OSError): raise _unavailable('path_unsafe') from None
        return
    try: os.unlink(name, dir_fd=directory)
    except FileNotFoundError: pass


def _root_fd(root, *, create=False):
    """Descriptor of the private state root, every component opened without following links."""
    if os.name == 'nt':
        from quorum_winproof import open_root
        opened = [_windows(lambda: open_root(root.parts[0]))]
    else: opened = [os.open('/', os.O_RDONLY | os.O_DIRECTORY)]
    try:
        _verify_fd(opened[0], 'ancestor')
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
    """Closes the held verified directory descriptor (and Windows pins) together with the connection."""
    anchor = None
    pins = ()

    def close(self):
        try: super().close()
        finally:
            if self.anchor is not None:
                os.close(self.anchor)
                self.anchor = None
            for fd in self.pins: os.close(fd)
            self.pins = ()


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
    (device, inode) equals `expected`; any other observation closes it unused. Windows has no descriptor
    listing: the caller holds every verified component without delete sharing, so none can be renamed,
    replaced or deleted, and the pathname must still name the verified (volume, file ID).
    """
    before = None if os.name == 'nt' else _descriptors()
    try: db = sqlite3.connect('file:' + quote(str(path)) + '?mode=' + mode, uri=True, timeout=5,
                              factory=_AnchoredConnection)
    except sqlite3.Error: raise _unavailable('path_unsafe') from None
    try:
        if os.name == 'nt':
            from quorum_winproof import path_identity
            if _windows(lambda: path_identity(path)) != expected: raise _unavailable('path_unsafe')
        elif [identity for fd, identity in _descriptors().items() if before.get(fd) != identity] != [expected]:
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


def actor_directory(session_ref):
    """On-disk name of a logical actor's directory under `sessions`.

    NTFS reserves ':' for alternate data streams, so Windows stores `claude-worker:<uuid>` as
    `claude-worker-<uuid>`; that form cannot collide with any other session reference (a bare UUID).
    """
    return session_ref.replace(':', '-', 1) if os.name == 'nt' else session_ref


def _actor_ledger(root, session_ref, *, create=False):
    """The logical actor's ledger; see `_ledger`."""
    return _ledger(root, ('sessions', actor_directory(session_ref)), ACTOR_KINDS[:2], create=create)


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
            fd = _create_new('ledger.sqlite', directory)
            fds.append(fd)
            _verify_fd(fd, 'file')
        else: fds.append(fd)
        # A legitimate recovery journal is a private, owned, single-link regular file and is kept for
        # SQLite's recovery; a link, loose mode, other owner or other type refuses before mutable use.
        _sidecars(directory)
        db = _connect(path, _identity([fd])[0], 'rw')
        try:
            db.anchor = os.dup(directory)
            # Windows: keep the whole verified chain pinned while SQLite uses pathnames under it.
            if os.name == 'nt': db.pins = [os.dup(fd) for fd in fds]
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
            _verify_fd(guard.fd, 'file')
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
        {'create': {'access'}, 'accept': {'invite_id'}}.get(mode, set()))
    optional = {'coordinator_role', 'dispatch_mode', 'lifetime', 'ttl_seconds', 'operations', 'accept_tasks_from_members',
                'standing_grants_from', 'trust_members'} | (
        {'access'} if mode == 'accept' else set()) | (
        {'knowledge', 'update'} if mode in ('create', 'accept') else set())
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
    elif mode == 'accept':
        if not isinstance(raw['invite_id'], str) or not CANONICAL.fullmatch(raw['invite_id']):
            raise _denied('scope_invalid')
        scope['invite_id'] = raw['invite_id']
        # An open invitation is owner-approved here only; omission keeps the legacy GitHub scope and hash.
        if 'access' in raw:
            if raw['access'] not in ('github', 'open'): raise _denied('scope_invalid')
            scope['access'] = raw['access']
    coordinator = raw.get('coordinator_role')
    if coordinator is not None and not _name(coordinator): raise _denied('scope_invalid')
    members = raw.get('accept_tasks_from_members', False)
    # One binding persists one delegation policy: coordinator-only intake (`coordinator_role`, optionally
    # native-subagent) or member intake (inline). They cannot both be the binding's grant.
    if type(members) is not bool or (members and coordinator): raise _denied('scope_invalid')
    # Standing grants name the coordinator role whose requests a member binding may honour for the owner's
    # bounded standing actions; like `session connect --standing-grants-from`, they need member intake.
    standing = raw.get('standing_grants_from')
    if 'standing_grants_from' in raw and (not members or not _name(standing)): raise _denied('scope_invalid')
    # Member trust (`session connect --trust-members`) is an explicit boolean that also needs member intake.
    trust = raw.get('trust_members', False)
    if type(trust) is not bool or (trust and not members): raise _denied('scope_invalid')
    dispatch_mode = raw.get('dispatch_mode', 'inline')
    if dispatch_mode not in ('inline', 'native-subagent') or (dispatch_mode == 'native-subagent' and not coordinator):
        raise _denied('scope_invalid')
    # Omission preserves the exact legacy scope/hash; updates cannot widen it.
    if 'accept_tasks_from_members' in raw: scope['accept_tasks_from_members'] = members
    if standing is not None: scope['standing_grants_from'] = standing
    if 'trust_members' in raw: scope['trust_members'] = trust
    lifetime = raw.get('lifetime', 'bounded')
    if lifetime not in ('bounded', 'until-owner-stop'): raise _denied('scope_invalid')
    if 'ttl_seconds' in raw:
        ttl = raw['ttl_seconds']
        if lifetime != 'bounded' or type(ttl) is not int or not 1 <= ttl <= TTL_LIMIT: raise _denied('scope_invalid')
    else: ttl = DEFAULT_TTL if lifetime == 'bounded' else None
    # Omission preserves the exact legacy scope/hash. Only the member subset of knowledge commands is pinnable.
    knowledge = raw.get('knowledge')
    if 'knowledge' in raw:
        if (not isinstance(knowledge, list) or not knowledge or len(knowledge) > len(KNOWLEDGE)
                or any(not isinstance(item, str) or item not in KNOWLEDGE for item in knowledge)
                or len(set(knowledge)) != len(knowledge)):
            raise _denied('scope_invalid')
        scope['knowledge'] = [item for item in KNOWLEDGE if item in knowledge]
    # Omission (or false) adds nothing; only an explicit true is stored, so legacy scopes keep their hash.
    if 'update' in raw:
        if type(raw['update']) is not bool: raise _denied('scope_invalid')
        if raw['update']: scope['update'] = True
    allowed = _allowed(scope, standing)
    operations = raw.get('operations', list(allowed))
    if (not isinstance(operations, list) or not operations or len(set(map(str, operations))) != len(operations)
            or any(not isinstance(item, str) or item not in allowed for item in operations)):
        raise _denied('scope_invalid')
    scope.update(coordinator_role=coordinator, dispatch_mode=dispatch_mode, lifetime=lifetime, ttl_seconds=ttl,
                 operations=[item for item in allowed if item in operations])
    return scope


def _allowed(scope, standing):
    """A scope's admissible operations in canonical order; standing-check, knowledge and update only when opted in."""
    return (OPERATIONS[scope['mode']] + (('standing-check',) if standing is not None else ())
            + (('knowledge',) if 'knowledge' in scope else ())
            + (('update',) if scope.get('update') is True and 'update' not in OPERATIONS[scope['mode']] else ()))


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
    result = {'slot_id': record['slot_id'], 'state': state, 'terminal_reason': reason,
              'lifetime': record['lifetime'], 'pending_expires_at': record['pending_expires_at'],
              'permission_expires_at': record['permission_expires_at'], 'binding': _public(record),
              'target': dict(record.get('target') or {}), 'create': _create_state(record, state),
              'session': _session_status(root, record) if session else None}
    # Rebind links only: slot IDs, never a handle, path or token.
    if record.get('rebound_to'): result['rebound_to'] = record['rebound_to']
    if isinstance(record.get('rebind'), dict):
        result['rebind'] = {key: record['rebind'][key] for key in ('previous_slot_id', 'count')}
    return result


def _held(directory, name):
    """Whether another holder has the verified lock file; probed with a shared lock, nothing created.

    Windows `FileLock` locks byte 0 exclusively, so the probe takes and at once releases that byte.
    """
    fd = _open(name, directory, 'file')
    if fd is None: return False
    if os.name == 'nt':
        import msvcrt
        try:
            try: msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
            except OSError: return True
            msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
            return False
        finally: os.close(fd)
    import fcntl
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
    names = ('sessions', actor_directory(binding['session_ref']))
    with _components(root, names, ACTOR_KINDS[:2]) as fds:
        if fds is None: return None
        rows = _read_db(fds[-1], root.joinpath(*names, 'ledger.sqlite'), lambda db: db.execute(
            "select key,data from settings where key in ('listener', 'hello', 'transport-notices', "
            "'show-transport-notices', ?) or key like 'delivery:%' order by rowid",
            (CONTROL,)).fetchall(),
                        ledger='actor')
        if rows is None: return None
        running = _held(fds[-1], 'listener.lock')
    settings = {key: json.loads(data) for key, data in rows}
    listener = settings.get('listener') or {}
    from quorum_native_inbox import awaiting_summary
    # The same awaiting-owner fields as the Codex status; rows keep their ledger order.
    awaiting = awaiting_summary([(key, value) for key, value in settings.items() if key.startswith('delivery:')])
    state = listener.get('state', 'stopped')
    # A recorded live run whose lock is free ended without reporting; reported, not rewritten here.
    if not running and state in LIVE_STATES: state = 'exited'
    return {'listener': 'running' if running else 'offline', 'state': state, 'run_id': listener.get('run_id'),
            'reception': 'enabled' if (settings.get(CONTROL) or {}).get('enabled') is True else 'disabled',
            'pending_receipts': sum(value.get('state') == 'offered' for key, value in settings.items()
                                    if key.startswith('delivery:')),
            # The last connection's Hello outcome; a summary string only, never a message body.
            'hello': (settings.get('hello') or {}).get('summary') if isinstance(settings.get('hello'), dict) else None,
            # Hello/roster transport notices this worker receipted quietly (#126); counts only.
            'transport_notices': {'shown': settings.get('show-transport-notices') is True,
                                  'auto_receipted': int((settings.get('transport-notices') or {}).get('auto_receipted', 0))
                                  if isinstance(settings.get('transport-notices'), dict) else 0},
            **awaiting}


def _bootstrap(root, slot_id):
    return [sys.executable, str(Path(__file__).with_name('quorum.py')), '--state', str(root),
            'session', 'worker', 'await-binding', slot_id, '--wait-seconds', str(AWAIT_DEFAULT)]


# --- Owner-parent lifecycle ------------------------------------------------------------------

def prepare(state_root, scope):
    """Pending slot only: no profile, auth, API, membership or receive right."""
    sid = actual_session()
    root = _state_root(state_root)
    raw, scope = scope, normalize_scope(scope)
    if scope['cwd'] != str(Path.cwd().resolve()): raise _denied('cwd_mismatch')
    target = {}
    if scope['mode'] == 'adopt':
        # The target comes from this chat's own existing binding in this state, never from the input.
        parent, policy = parent_binding(root, sid, scope, policy=True)
        if policy and not DELEGATION_KEYS & set(raw):
            # The owner's intake policy recorded at this chat's connect carries over when the scope names none,
            # so the worker's own connect stores the matching grant for its own agent (nothing is copied).
            scope = normalize_scope({**raw, **policy})
        scope['adopt'] = parent
        target = {'quorum_id': parent['quorum_id']}
    prepared = now()
    permission = None if scope['lifetime'] == 'until-owner-stop' else prepared + scope['ttl_seconds']
    pending = prepared + PENDING_SECONDS if permission is None else min(prepared + PENDING_SECONDS, permission)
    slot_id = str(uuid.uuid4())
    # The ordinary private state root may be created on this parent route, then verified like every worker path.
    os.close(_root_fd(root, create=True))
    _ledger(root, create=True).db.close()
    with _directory(root, 'claude-workers', create=True): pass
    adopt = scope['mode'] == 'adopt'
    # One live adopting worker per parent agent: check, write and claim under the index lock.
    with (_lock(root, ('claude-workers',), 'index.lock') if adopt else nullcontext()):
        if adopt: _adoption_free(root, scope['adopt']['agent_id'])
        # Claim first: a crash before the slot is written leaves a claim to nothing, which is released.
        if adopt: _index_setting(root, ADOPTED + scope['adopt']['agent_id'], {'slot_id': slot_id})
        _write_slot(root, sid, scope, slot_id, target, prepared, pending, permission)
    result = {'slot_id': slot_id, 'state': 'pending', 'lifetime': scope['lifetime'], 'pending_expires_at': pending,
              'permission_expires_at': permission, 'bootstrap_command': _bootstrap(root, slot_id),
              'connect_flags': connect_flags(scope)}
    if adopt: result['adopt'] = dict(scope['adopt'])
    return result


# Scope keys that state a delegation policy; an adopt scope naming none inherits the parent's.
DELEGATION_KEYS = {'coordinator_role', 'accept_tasks_from_members', 'standing_grants_from', 'trust_members',
                   'dispatch_mode'}


def connect_flags(scope):
    """The intake flags the worker's `session connect` must pass for this scope (they must equal it)."""
    flags = []
    if scope.get('coordinator_role'): flags += ['--accept-tasks-from', scope['coordinator_role']]
    if scope.get('accept_tasks_from_members'): flags.append('--accept-tasks-from-members')
    if scope.get('standing_grants_from'): flags += ['--standing-grants-from', scope['standing_grants_from']]
    if scope.get('trust_members'): flags.append('--trust-members')
    return flags + ['--dispatch-mode', scope['dispatch_mode']]


def _parent_policy(record):
    """The delegation policy of this chat's own parent binding as scope keys; {} without one."""
    from quorum_delegation import member_trust, standing_grants
    policy = record.get('delegation')
    if not isinstance(policy, dict): return {}
    if policy.get('scope') == 'members':
        result = {'accept_tasks_from_members': True}
        standing = standing_grants(record)
        if standing is not None: result['standing_grants_from'] = standing['from_role']
        if member_trust(record): result['trust_members'] = True
        return result
    role = policy.get('coordinator_role')
    if isinstance(role, str) and _name(role):
        return {'coordinator_role': role, 'dispatch_mode': policy.get('dispatch_mode', 'inline')}
    return {}


def _write_slot(root, sid, scope, slot_id, target, prepared, pending, permission, extra=None):
    with _directory(root, 'claude-workers') as workers:
        if workers is None: raise _unavailable('path_unsafe')
        _mkdir_new(slot_id, workers)
        os.close(_open(slot_id, workers, 'private'))
    record = {'slot_id': slot_id, 'state': 'pending', 'terminal_reason': None, 'provider_session_id': sid,
              'state_root': str(root), 'scope': scope, 'scope_hash': _digest(scope), 'lifetime': scope['lifetime'],
              'prepared_at': prepared, 'pending_expires_at': pending, 'permission_expires_at': permission,
              'generation': 1, 'binding': None, 'token_hash': None, 'target': target}
    if scope['mode'] == 'create': record.update(create_request_id=str(uuid.uuid4()), create=None)
    if extra: record.update(extra)
    ledger = _ledger(root, ('claude-workers', slot_id), SLOT_KINDS, create=True)
    try: ledger.setting(SLOT_KEY, record)
    finally: ledger.db.close()


def parent_binding(root, sid, scope, *, policy=False):
    """This chat's own stopped parent-route binding for exactly the scoped quorum, role and project.

    Read-only from the verified `sessions/<actual SID>` ledger; the state's public service must be the pinned
    one. A running parent listener or enabled parent reception refuses: two live agents on one role make
    role sends ambiguous. Returns the parent's quorum and agent IDs, which pin the worker's target, and with
    `policy` also its delegation policy as scope keys.
    """
    if not os.path.isdir(root): raise _denied('parent_binding_missing')
    names = ('sessions', sid)
    with _components(root, names, ACTOR_KINDS[:2]) as fds:
        if fds is None: raise _denied('parent_binding_missing')
        rows = _read_db(fds[-1], root.joinpath(*names, 'ledger.sqlite'), lambda db: db.execute(
            "select key,data from settings where key in ('session-agent', 'session-reception')"
        ).fetchall(), ledger='parent')
        running = rows is not None and _held(fds[-1], 'listener.lock')
    settings = {key: json.loads(data) for key, data in rows or ()}
    record = settings.get('session-agent')
    if (not isinstance(record, dict) or record.get('session_ref') != sid or record.get('engine') != PROVIDER
            or record.get('transport', 'native') == TRANSPORT
            or not all(isinstance(record.get(key), str) and record[key] for key in ('quorum_id', 'agent_id'))):
        raise _denied('parent_binding_missing')
    if record.get('quorum') != scope['quorum_slug']: raise _denied('target_mismatch')
    if (record.get('role') != scope['role'] or record.get('project_ref') != scope['project_ref']
            or record.get('cwd') != scope['cwd']):
        raise _denied('scope_mismatch')
    service_check({'scope': scope, 'state_root': str(root)})
    # The held listener lock, not a recorded state, proves a run; enabled reception would restart one.
    if running or (settings.get('session-reception') or {}).get('enabled') is True:
        raise _denied('parent_binding_running')
    found = {'quorum_id': record['quorum_id'], 'agent_id': record['agent_id']}
    return (found, _parent_policy(record)) if policy else found


def _index_setting(root, key, value=None):
    index = _ledger(root)
    if index is None: raise _unavailable('path_unsafe')
    try: return index.setting(key, value)
    finally: index.db.close()


def _adoption_free(root, agent_id):
    """Caller holds the index lock. An earlier adopting slot blocks until it is terminal (stopped or expired)
    and, if it ever connected, until its own agent is confirmed retired: never two live agents on one role."""
    claimed = _index_setting(root, ADOPTED + agent_id)
    if not isinstance(claimed, dict): return
    try: earlier = _view(root, _slot_uuid(claimed.get('slot_id')))
    except NativeInboxError as error:
        if error.reason == 'recovery_needed': raise
        return  # A slot whose ledger was never written holds nothing.
    if _effective(earlier)[0] != 'terminal': raise _denied('actor_claimed')
    worker_agent = (earlier.get('target') or {}).get('agent_id')
    if _unresolved(earlier):
        # A registration was sent without a recorded answer: its agent may be live under this role.
        raise NativeInboxError('native_worker_binding_denied', 'previous_worker_registration_unresolved',
                               previous={'slot_id': earlier['slot_id'], 'agent_id': None})
    if worker_agent and _index_setting(root, RETIRED + worker_agent) is None:
        raise NativeInboxError('native_worker_binding_denied', 'previous_worker_agent_active',
                               previous={'slot_id': earlier['slot_id'], 'agent_id': worker_agent})


def record_retired(ledger, agent_id):
    """The owner's confirmed `leave` of an agent; read only to release an adoption claim."""
    if isinstance(agent_id, str) and CANONICAL.fullmatch(agent_id):
        ledger.setting(RETIRED + agent_id, {'retired_at': now()})


@contextmanager
def _owner_api(root, record):
    """The owner's root ledger and API on this state's login, after the slot's pinned service is confirmed."""
    from quorum_live import api_for
    context = {'scope': record['scope'], 'state_root': str(root)}
    service_check(context)
    ledger = root_ledger(root)
    try:
        api, config, _ = api_for(ledger)
        same_resolved(context, config)
        yield ledger, api
    finally: ledger.db.close()


def _adopted(scope):
    """The parent agent an adopt scope hands over from; the service lets exactly it share the role meanwhile."""
    return (scope.get('adopt') or {}).get('agent_id') if scope.get('mode') == 'adopt' else None


def _registration(record):
    """The adopting worker's exact pinned registration: payload, device key and stable join request ID."""
    scope, session_ref = record['scope'], record['binding']['session_ref']
    data = {'engine': PROVIDER, 'session_ref': session_ref, 'project_ref': scope['project_ref'], 'cwd': scope['cwd']}
    device_key = 'session-device:' + session_ref
    # Same identity as `register_agent`, so a replay repeats the original request exactly.
    identity = hashlib.sha256(json.dumps([record['target']['quorum_id'], scope['role'], data, device_key],
                                         sort_keys=True).encode()).hexdigest()
    return data, device_key, 'join:' + identity


def _unresolved(record):
    """A registration was (or may have been) sent, but its agent ID is not recorded."""
    return bool(record.get('registration')) and not (record.get('target') or {}).get('agent_id')


def _reconcile(root, record, ledger, api, register_agent):
    """Recover a sent registration's agent ID by replaying it with the same request ID and pinned payload.

    The service checks membership, then answers a replay from its idempotency record; a request that never
    arrived registers the agent now, which the caller then retires. The ID is pinned once on the slot.
    """
    agent_id = (record.get('target') or {}).get('agent_id')
    if agent_id or not record.get('registration'): return agent_id
    if register_agent is None: raise _denied('operation_denied')
    data, device_key, _ = _registration(record)
    result = register_agent(api, ledger, record['scope']['quorum_slug'], record['scope']['role'], data,
                            device_key=device_key, expected_quorum_id=record['target']['quorum_id'],
                            keep_agent_id=_adopted(record['scope']))
    agent_id = result.get('agent_id') if isinstance(result, dict) else None
    if not isinstance(agent_id, str) or not CANONICAL.fullmatch(agent_id): raise QuorumError('invalid_response')
    with _slot(root, record['slot_id'], record['provider_session_id']) as slot:
        current = slot.setting(SLOT_KEY)
        target = dict(current.get('target') or {})
        if target.get('agent_id') not in (None, agent_id): raise _denied('target_mismatch')
        slot.setting(SLOT_KEY, {**current, 'target': {**target, 'agent_id': agent_id}})
    record['target'] = {**record['target'], 'agent_id': agent_id}
    return agent_id


def _retire(root, record, register_agent=None):
    """Owner `stop --leave`: retire this adopt slot's own worker agent with the owner's login and pinned service."""
    agent_id = (record.get('target') or {}).get('agent_id')
    if not agent_id and not record.get('registration'): return 'none'
    if agent_id and _index_setting(root, RETIRED + agent_id) is not None: return 'retired'
    with _owner_api(root, record) as (ledger, api):
        agent_id = _reconcile(root, record, ledger, api, register_agent)
        if _index_setting(root, RETIRED + agent_id) is not None: return 'retired'
        try: api.call('agent_leave', {'agent_id': agent_id}, 'leave:' + agent_id)
        except QuorumError:
            # A lost response leaves a committed leave that a retry cannot repeat: confirm it by a read instead.
            if not verified_retired(api, record, agent_id): raise
        record_retired(ledger, agent_id)
    return 'retired'


def verified_retired(api, record, agent_id):
    """Authenticated proof that this slot's own worker agent is no longer live under its role; False otherwise.

    Reads only the slot's pinned quorum, role and project (never a slug). Proof is either `resolve_role` naming
    exactly one live agent that is not this one, or, when the role has none or several, the server's explicit
    `agent_denied` for this login's own registered agent (revoked agent or device). An error, a malformed
    answer or this agent still resolving keeps the claim.
    """
    scope, quorum_id = record['scope'], (record.get('target') or {}).get('quorum_id')
    if not isinstance(quorum_id, str) or not quorum_id: return False
    try:
        found = api.call('resolve_role', {'quorum_id': quorum_id, 'role': scope['role'],
                                          'project_ref': scope['project_ref']})
    except QuorumError as error:
        if str(error) != 'recipient_ambiguous_or_missing': return False
        found = None
    if found is not None:
        other = found.get('agent_id') if isinstance(found, dict) and set(found) == {'agent_id'} else None
        return isinstance(other, str) and bool(CANONICAL.fullmatch(other)) and other != agent_id
    try: api.call('agent_status', {'agent_id': agent_id})
    except QuorumError as error: return str(error) == 'auth_required' and error.denial == 'agent_denied'
    return False


def verify_retired(state_root, slot_id, register_agent=None):
    """Owner recovery after a lost leave or registration answer: record the retirement only on authenticated proof."""
    sid = actual_session()
    root = _state_root(state_root)
    record = _view(root, _slot_uuid(slot_id))
    if record.get('provider_session_id') != sid: raise _denied('session_mismatch')
    if record['scope']['mode'] != 'adopt': raise _denied('operation_denied')
    agent_id = (record.get('target') or {}).get('agent_id')
    if not agent_id and not record.get('registration'): return {'slot_id': record['slot_id'], 'worker_agent': 'none'}
    if not agent_id or _index_setting(root, RETIRED + agent_id) is None:
        with _owner_api(root, record) as (ledger, api):
            agent_id = _reconcile(root, record, ledger, api, register_agent)
            if _index_setting(root, RETIRED + agent_id) is None:
                if not verified_retired(api, record, agent_id):
                    raise NativeInboxError('native_worker_binding_denied', 'previous_worker_agent_active',
                                           previous={'slot_id': record['slot_id'], 'agent_id': agent_id})
                record_retired(ledger, agent_id)
    return {'slot_id': record['slot_id'], 'worker_agent': 'retired'}


def _write_capability(root, slot_id, token):
    """Descriptor-relative private temporary, then atomic replacement; only while the slot is pending."""
    data = json.dumps({'version': 1, 'slot_id': slot_id, 'state_root': str(root), 'token': token}).encode()
    with _directory(root, 'claude-workers', slot_id) as directory:
        if directory is None: raise _denied('slot_invalid')
        name = '.capability-' + secrets.token_hex(8) + '.tmp'
        if os.name == 'nt': fd = _create_new(name, directory)  # Owner-only DACL set at creation.
        else: fd = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=directory)
        try:
            try:
                if os.name != 'nt': os.fchmod(fd, 0o600)
                view = memoryview(data)
                while view: view = view[os.write(fd, view):]
                os.fsync(fd)
            finally: os.close(fd)
            # The rename atomically replaces an earlier unactivated attempt's file.
            _rename_at(directory, name, 'capability.json')
        except BaseException:
            _unlink_at(directory, name)
            raise


def attest(state_root, slot_id, native_handle, spawn_tool_use_id=None):
    """Same actual SID, one-time locked activation from the owner-parent's exact native receipt."""
    sid = actual_session()
    root = _state_root(state_root)
    slot_id = _slot_uuid(slot_id)
    handle = _label(native_handle)
    tool = None if spawn_tool_use_id is None else _label(spawn_tool_use_id)
    check_root(root)
    # A rebind slot also writes its predecessor: recover both before the index lock is taken.
    _recover(root, slot_id)
    marker = _view(root, slot_id).get('rebind')
    if isinstance(marker, dict): _recover(root, _slot_uuid(marker.get('previous_slot_id')))
    with _slot(root, slot_id, sid, index=True) as ledger:
        record = _settle(ledger, ledger.setting(SLOT_KEY))
        if record['state'] == 'terminal': raise _terminal(record['terminal_reason'])
        if record['state'] == 'active':
            binding = record['binding']
            if binding['native_worker_handle'] != handle or binding.get('spawn_tool_use_id') != tool:
                raise _denied('binding_changed')
            return _public(record)
        if record.get('rebind') is not None: return _attest_rebind(root, sid, ledger, record, handle, tool)
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


def _new_binding(record, sid, handle, tool, actor, session_ref):
    """The slot's binding for this attested native handle, sealed by its authority hash."""
    binding = {'provider': PROVIDER, 'binding_version': 1, 'provider_session_id': sid,
               'native_worker_handle': handle, 'logical_actor_id': actor, 'session_ref': session_ref,
               'slot_id': record['slot_id'], 'generation': record['generation'],
               'attestation': 'owner-parent-native-receipt'}
    if tool is not None: binding['spawn_tool_use_id'] = tool
    binding['authority_hash'] = _digest({**binding, 'scope_hash': record['scope_hash'],
                                         'lifetime': record['lifetime'], 'state_root': record['state_root'],
                                         'permission_expires_at': record['permission_expires_at']})
    return binding


# --- Rebind after a parent chat restart ------------------------------------------------------

def _actor_binding(root, binding):
    """The `worker_binding` a connected actor ledger stores for this slot binding (as `_worker_binding`)."""
    return {key: binding[key] for key in ('provider', 'session_ref', 'slot_id', 'logical_actor_id',
                                          'provider_session_id', 'native_worker_handle', 'authority_hash')} | {
        'capability_file': str(_slot_path(root, binding['slot_id']) / 'capability.json')}


# An unactivated successor that ended this way never acted; its predecessor's hand-over may be repeated.
LAPSED = ('pending_expired', 'await_timeout')


def _unactivated(successor):
    """Check a superseded slot's successor that never activated: pending refuses, an owner stop stays a stop."""
    state, reason = _effective(successor)
    if state != 'terminal': raise _denied('rebind_pending')
    if reason not in LAPSED: raise _terminal(reason)


def _rebind_source(root, record):
    """The connected slot a rebind continues from: this active slot, or, after an interrupted rebind whose
    successor never activated and has lapsed, the superseded slot itself. Activated successors are followed."""
    for _ in range(64):
        state, reason = _effective(record)
        if state == 'active': return record
        if state == 'pending': raise _denied('slot_pending')
        if reason != 'rebound': raise _terminal(reason)
        successor = _view(root, _slot_uuid(record.get('rebound_to')))
        if successor.get('binding'):
            record = successor
            continue
        _unactivated(successor)
        return record
    raise _denied('slot_invalid')


def _limbo(root, record):
    """An unactivated rebind successor whose predecessor was already superseded: the hand-over is unfinished,
    so this slot speaks for the actor (no handle can act until it activates)."""
    marker = record.get('rebind')
    if record.get('binding') or not isinstance(marker, dict): return False
    try: previous = _view(root, _slot_uuid(marker.get('previous_slot_id')))
    except NativeInboxError: return False
    return previous.get('terminal_reason') == 'rebound' and previous.get('rebound_to') == record['slot_id']


def _connected(root, record):
    """Read-only: the actor ledger's agent record for this slot, which must still have reception enabled."""
    from quorum_native_inbox import CONTROL
    binding, target = record['binding'], record.get('target') or {}
    if not target.get('agent_id') or _unresolved(record): raise _denied('target_pending')
    names = ('sessions', actor_directory(binding['session_ref']))
    with _components(root, names, ACTOR_KINDS[:2]) as fds:
        rows = None if fds is None else _read_db(
            fds[-1], root.joinpath(*names, 'ledger.sqlite'),
            lambda db: db.execute("select key,data from settings where key in ('session-agent', ?)",
                                  (CONTROL,)).fetchall(), ledger='actor')
    settings = {key: json.loads(data) for key, data in rows or ()}
    agent = settings.get('session-agent')
    if (not isinstance(agent, dict) or agent.get('session_ref') != binding['session_ref']
            or agent.get('agent_id') != target['agent_id'] or agent.get('quorum_id') != target.get('quorum_id')):
        raise _denied('target_pending')
    # A stopped worker stays stopped: rebind never re-enables reception.
    if (settings.get(CONTROL) or {}).get('enabled') is not True: raise _denied('reception_stopped')
    return agent


def _rebind_scope(scope, knowledge, update):
    """The predecessor's exact scope; the owner may only add the member opt-ins to a create/accept scope."""
    if not knowledge and not update: return dict(scope)
    if (scope['mode'] not in ('create', 'accept') or any(item not in KNOWLEDGE for item in knowledge)
            or type(update) is not bool):
        raise _denied('scope_invalid')
    result = dict(scope)
    if knowledge:
        result['knowledge'] = [item for item in KNOWLEDGE if item in knowledge or item in scope.get('knowledge', ())]
    if update: result['update'] = True
    added = [name for name in ('knowledge', 'update') if name in result and name not in scope]
    allowed = _allowed(result, result.get('standing_grants_from'))
    result['operations'] = [item for item in allowed if item in scope['operations'] or item in added]
    return result


def rebind(state_root, slot_id, *, knowledge=(), update=False):
    """Owner-parent: a pending successor slot that hands this connected worker binding to a new native handle.

    For the same actual `CLAUDE_CODE_SESSION_ID` after the parent chat restarted and its native worker is gone.
    The agent, its session reference and actor ledger, role, delegation, scope and deliveries stay; nothing is
    registered or accepted, and the permission deadline is the predecessor's. Attesting the successor
    supersedes the predecessor (`native_worker_slot_terminal` reason `rebound` for the old handle). A stopped,
    expired or service-revoked binding, another session or a still-pending successor is refused.
    """
    from quorum_session import agent_revoked
    sid = actual_session()
    root = _state_root(state_root)
    slot_id = _slot_uuid(slot_id)
    knowledge = list(knowledge or ())
    check_root(root)
    _recover(root, slot_id)
    named = _view(root, slot_id)
    if named.get('provider_session_id') != sid: raise _denied('session_mismatch')
    source = _rebind_source(root, named)
    if source.get('provider_session_id') != sid: raise _denied('session_mismatch')
    if source['scope']['cwd'] != str(Path.cwd().resolve()): raise _denied('cwd_mismatch')
    scope = _rebind_scope(source['scope'], knowledge, update)
    agent = _connected(root, source)
    # The agent must still be live at the service: rebind never revives a revoked agent or membership.
    with _owner_api(root, source) as (_, api):
        try: api.call('agent_status', {'agent_id': agent['agent_id']})
        except QuorumError as error:
            if agent_revoked(error): raise _denied('agent_revoked') from None
            raise
    prepared = now()
    permission = source['permission_expires_at']
    if permission is not None and prepared >= permission: raise _terminal('permission_expired')
    pending = prepared + PENDING_SECONDS if permission is None else min(prepared + PENDING_SECONDS, permission)
    successor = str(uuid.uuid4())
    binding = source['binding']
    marker = {'previous_slot_id': source['slot_id'], 'previous_scope_hash': source['scope_hash'],
              'session_ref': binding['session_ref'], 'logical_actor_id': binding['logical_actor_id'],
              'agent_id': agent['agent_id'], 'count': (source.get('rebind') or {}).get('count', 0) + 1}
    carried = {key: source[key] for key in ('registration', 'create', 'create_request_id') if key in source}
    key = REBINDING + binding['session_ref']
    with _lock(root, ('claude-workers',), 'index.lock'):
        # Attest also runs under this lock: the source cannot change hands between this check and the write.
        if _rebind_source(root, _view(root, slot_id))['slot_id'] != source['slot_id']: raise _denied('rebind_pending')
        reserved = _reserved(root, _index_setting(root, key), source['slot_id'])
        if reserved is not None:
            # A repeated rebind (for example after a lost response) returns the same pending successor.
            if reserved['scope_hash'] != _digest(scope): raise _denied('rebind_pending')
            return _rebind_result(root, reserved)
        # Reserve first: a crash before the slot is written leaves a reservation of nothing, which is replaced.
        _index_setting(root, key, {'slot_id': successor, 'previous_slot_id': source['slot_id']})
        _write_slot(root, sid, scope, successor, dict(source.get('target') or {}), prepared, pending, permission,
                    extra={**carried, 'rebind': marker})
    return _rebind_result(root, _view(root, successor))


# One reserved pending successor per actor: `claude-worker-rebinding:<session_ref>` -> its slot and source.
REBINDING = 'claude-worker-rebinding:'


def _reserved(root, reservation, source_id):
    """The still-valid pending successor this reservation names for `source_id`, else None (absent, never
    written, lapsed, activated or superseded reservations are replaceable)."""
    if not isinstance(reservation, dict): return None
    try: record = _view(root, _slot_uuid(reservation.get('slot_id')))
    except NativeInboxError as error:
        if error.reason == 'recovery_needed': raise
        return None
    if _effective(record)[0] != 'pending' or record.get('binding'): return None
    if (record.get('rebind') or {}).get('previous_slot_id') != source_id: raise _denied('rebind_pending')
    return record


def _rebind_result(root, record):
    """The rebind answer for a pending successor; identical for a repeated request. IDs only, never a token."""
    marker = record['rebind']
    return {'slot_id': record['slot_id'], 'state': 'pending', 'lifetime': record['lifetime'],
            'pending_expires_at': record['pending_expires_at'],
            'permission_expires_at': record['permission_expires_at'],
            'bootstrap_command': _bootstrap(root, record['slot_id']),
            'rebind': {key: marker[key] for key in ('previous_slot_id', 'session_ref', 'agent_id', 'count')},
            'connect_flags': connect_flags(record['scope'])}


@contextmanager
def _held_slot(root, slot_id):
    """A second slot's lock and writable ledger while the caller already holds the index lock."""
    with _lock(root, ('claude-workers', slot_id), 'slot.lock'):
        ledger = _ledger(root, ('claude-workers', slot_id), SLOT_KINDS)
        if ledger is None: raise _denied('slot_invalid')
        try: yield ledger
        finally: ledger.db.close()


def _alias_free(root, sid, alias, session_ref):
    """The new handle's own derived actor may be unclaimed or claimed by this same actor's slots only."""
    index = _ledger(root)
    if index is None: raise _unavailable('path_unsafe')
    try: claimed = index.setting(INDEX + alias)
    finally: index.db.close()
    if claimed is None: return
    try: owner = _view(root, _slot_uuid(claimed.get('slot_id') if isinstance(claimed, dict) else None))
    except NativeInboxError: owner = None
    if owner is None or claimed.get('provider_session_id') != sid or owner.get('provider_session_id') != sid:
        raise _denied('actor_claimed')
    # An activated slot proves its actor by its binding; a never-activated rebind successor (this same
    # hand-over interrupted after its index step, or an earlier lapsed one) by its owner-written rebind marker.
    marker = owner.get('rebind') if not owner.get('binding') else None
    actor = ((owner.get('binding') or {}).get('session_ref') if marker is None
             else marker.get('session_ref') if isinstance(marker, dict) else None)
    if actor != session_ref: raise _denied('actor_claimed')


def _attest_rebind(root, sid, ledger, record, handle, tool):
    """Activate a pending rebind slot; the caller holds the index lock and this slot's lock.

    Crash-safe order, each step repeated unchanged by an identical retry: (1) the successor's capability file
    while it is still pending (authorization refuses a pending slot); (2) the predecessor turns terminal
    `rebound`, which excludes the old handle; (3) the actor ledger names the new binding, under its control
    lock and refused while a receive runs; (4) the root index names the successor; (5) it activates. Before (5)
    no handle can act; a retried attest, or a new rebind after this successor lapsed, completes the hand-over.
    """
    import quorum_native_inbox as inbox
    from quorum_listener import locked
    from quorum_live import supervisor_running
    marker = record['rebind']
    source_id = _slot_uuid(marker.get('previous_slot_id'))
    session_ref = marker['session_ref']
    alias = PREFIX + str(uuid.uuid5(uuid.UUID(sid), handle))
    # The predecessor's slot lock is held until the successor activates: an owner stop of the old slot lands
    # either before (the hand-over is refused) or after (the old slot is superseded and stops nothing live).
    with _held_slot(root, source_id) as source_ledger:
        source = _settle(source_ledger, source_ledger.setting(SLOT_KEY))
        if (not isinstance(source, dict) or source.get('slot_id') != source_id
                or source.get('provider_session_id') != sid or source.get('scope_hash') != marker['previous_scope_hash']
                or (source.get('binding') or {}).get('session_ref') != session_ref
                or (source.get('target') or {}) != (record.get('target') or {})):
            raise _denied('binding_changed')
        unfinished = None
        if source['state'] != 'active':
            if source.get('terminal_reason') != 'rebound': raise _terminal(source.get('terminal_reason'))
            unfinished = _slot_uuid(source.get('rebound_to'))
            if unfinished != record['slot_id']:
                # Only a hand-over to a successor that never activated and lapsed may be repeated.
                other = _view(root, unfinished)
                if other.get('binding'): raise _terminal('rebound')
                _unactivated(other)
        if alias != session_ref: _alias_free(root, sid, alias, session_ref)
        binding = _new_binding(record, sid, handle, tool, marker['logical_actor_id'], session_ref)
        actor = _actor_ledger(root, session_ref)
        if actor is None: raise _denied('target_pending')
        try:
            # The control lock serializes with receive start, stop and the worker's connect write; the immediate
            # transaction makes the actor record a compare-and-set against every other writer of it.
            with inbox.control_lock(actor), actor.db:
                actor.db.execute('begin immediate')
                row = actor.db.execute("select data from settings where key='session-agent'").fetchone()
                current = None if row is None else json.loads(row[0])
                bound = (current or {}).get('worker_binding') or {}
                allowed = {source_id, record['slot_id'], unfinished} - {None}
                if (not isinstance(current, dict) or current.get('session_ref') != session_ref
                        or current.get('agent_id') != marker['agent_id'] or bound.get('slot_id') not in allowed
                        or (bound.get('slot_id') == source_id and bound != _actor_binding(root, source['binding']))):
                    raise _denied('binding_changed')
                # A stopped worker stays stopped: only an enabled binding is handed over.
                if not inbox.enabled(actor): raise _denied('reception_stopped')
                # The previous worker may still be inside a receive; never take its offer or epoch mid-call.
                if locked(actor.root / 'listener.lock') or supervisor_running(actor.root):
                    raise ValueError('worker_already_running')
                token = secrets.token_urlsafe(32)
                _write_capability(root, record['slot_id'], token)
                if source['state'] == 'active' or unfinished != record['slot_id']:
                    source_ledger.setting(SLOT_KEY, {**source, 'state': 'terminal', 'terminal_reason': 'rebound',
                                                     'terminal_at': source.get('terminal_at') or now(),
                                                     'rebound_to': record['slot_id']})
                updated = {**current, 'worker_binding': _actor_binding(root, binding), 'claude_worker_rebind': {
                    'slot_id': record['slot_id'], 'previous_slot_id': source_id, 'rebound_at': int(now()),
                    'count': marker['count']}}
                actor.db.execute("update settings set data=? where key='session-agent'", (json.dumps(updated),))
                # The new worker's first receive says Hello with a fresh presence epoch.
                actor.db.execute("delete from settings where key='presence-epoch'")
        finally: actor.db.close()
        index = _ledger(root)
        if index is None: raise _unavailable('path_unsafe')
        try:
            claim = {'slot_id': record['slot_id'], 'provider_session_id': sid}
            index.setting(INDEX + session_ref, claim)
            if alias != session_ref: index.setting(INDEX + alias, claim)
            adopted = _adopted(record['scope'])
            if adopted and (index.setting(ADOPTED + adopted) or {}).get('slot_id') in (source_id, unfinished):
                index.setting(ADOPTED + adopted, {'slot_id': record['slot_id']})
        finally: index.db.close()
        record = {**record, 'state': 'active', 'binding': binding, 'activated_at': now(),
                  'token_hash': hashlib.sha256(token.encode()).hexdigest()}
        ledger.setting(SLOT_KEY, record)
    return {**_public(record), 'rebind': {'previous_slot_id': source_id, 'count': marker['count']}}


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


def stop_slot(state_root, slot_id, *, sid=None, leave=False, register_agent=None):
    """Terminal revocation of this slot, then a stop request for its exact active run only.

    `leave` (owner, adopt slots only) then retires the worker's own agent; expiry never does.
    """
    import quorum_native_inbox as inbox
    sid = sid or actual_session()
    root = _state_root(state_root)
    slot_id = _slot_uuid(slot_id)
    with _slot(root, slot_id, sid) as ledger:
        record = _settle(ledger, ledger.setting(SLOT_KEY))
        if leave and record['scope']['mode'] != 'adopt': raise _denied('operation_denied')
        if record['state'] != 'terminal':
            record = {**record, 'state': 'terminal', 'terminal_reason': 'stopped', 'terminal_at': now()}
            ledger.setting(SLOT_KEY, record)
    if _superseded(root, record):
        # The agent and its actor ledger now belong to the activated successor slot: stop that one instead.
        if leave: raise _terminal('rebound')
        return {**_summary(root, record, session=False), 'rebound_to': record['rebound_to']}
    binding = record.get('binding')
    # An unfinished hand-over's successor speaks for the actor: its stop also stops the agent's reception.
    if not binding and _limbo(root, record): binding = {'session_ref': record['rebind']['session_ref']}
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
    if leave: return {**_summary(root, record), 'worker_agent': _retire(root, record, register_agent)}
    return _summary(root, record)


def _superseded(root, record):
    """A slot handed over by a rebind whose successor activated; until then the hand-over is unfinished."""
    if record.get('terminal_reason') != 'rebound': return False
    try: successor = _view(root, _slot_uuid(record.get('rebound_to')))
    except NativeInboxError: return False
    return bool(successor.get('binding'))


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


def _anonymous(scope):
    """Only an accept scope the owner prepared for an open invitation admits an anonymous identity."""
    return scope['mode'] == 'accept' and scope.get('access') == 'open'


def _check_syntax(operation, arguments):
    """Argument rules that need no authority record: forbidden modes, flags and request overrides."""
    get = arguments.get
    if operation == 'create' and get('request_id') is not None: raise _denied('request_override')
    if operation == 'accept' and not (get('transport') == TRANSPORT and get('parent_thread_id') is None
                                      and not get('membership_only') and get('client') is None
                                      and bool(get('origin')) == bool(get('publishable_key'))
                                      and (not get('anonymous') or bool(get('origin')))):
        raise _denied('scope_mismatch')
    if operation == 'connect' and not (get('transport') == TRANSPORT and get('parent_thread_id') is None):
        raise _denied('scope_mismatch')
    # Retiring another agent (`leave`) is in no worker scope; the owner retires it, then connects exclusively.
    if operation == 'connect' and get('supersede'): raise _denied('supersede_not_in_scope')


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
        require(get('invite_id') == scope['invite_id'] and bool(get('anonymous')) == _anonymous(scope))
        if get('origin'): require(_same_service(scope, get('origin'), get('publishable_key')))
    elif operation == 'connect':
        require(get('quorum') == scope['quorum_slug'] and get('role') == scope['role']
                and get('project_ref') == scope['project_ref'] and get('accept_tasks_from') == scope['coordinator_role']
                and bool(get('accept_tasks_from_members')) == scope.get('accept_tasks_from_members', False)
                and get('standing_grants_from') == scope.get('standing_grants_from')
                and bool(get('trust_members')) == scope.get('trust_members', False)
                and get('dispatch_mode', 'inline') == scope['dispatch_mode'])
        # No registration until the approved membership or creation (including open access) is known.
        if not target.get('quorum_id') or (scope['mode'] == 'create'
                                           and (record.get('create') or {}).get('state') != 'done'):
            raise _denied('target_pending')
    elif operation == 'standing-check':
        from quorum_delegation import STANDING_ACTIONS
        require(scope.get('standing_grants_from') is not None and get('standing_action') in STANDING_ACTIONS
                and isinstance(get('message_id'), str) and CANONICAL.fullmatch(get('message_id')))
        if not target.get('agent_id'): raise _denied('target_pending')
    elif operation == 'task-check':
        require(scope.get('accept_tasks_from_members') is True
                and isinstance(get('message_id'), str) and CANONICAL.fullmatch(get('message_id')))
        if not target.get('agent_id'): raise _denied('target_pending')
    elif operation in ('receive', 'received', 'reply', 'knowledge', 'update') and not target.get('agent_id'):
        raise _denied('target_pending')
    if operation == 'update': require(get('quorum') == scope['quorum_slug'])
    if operation == 'knowledge' and 'knowledge' in scope:
        # A member subset: only its pinned commands, never an owner action or a capture.
        if (KNOWLEDGE_ACTIONS.get(get('action')) not in scope['knowledge'] or get('owner_action')
                or get('material_result')):
            raise _denied('operation_denied')


def knowledge_request(context, action, data):
    """A member knowledge subset rechecked on the parsed request, before any ledger, profile or service call.

    `write` is only `propose` and `read` is never an audit; adopt scopes keep their owner maintenance.
    """
    if 'knowledge' not in context['scope']: return
    get = data.get if isinstance(data, dict) else (lambda _: None)
    if (KNOWLEDGE_ACTIONS.get(action) not in context['scope']['knowledge']
            or (action == 'write' and get('operation') != 'propose') or (action == 'read' and get('audit'))):
        raise _denied('operation_denied')


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
    if operation not in KNOWN: raise _denied('operation_denied')
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


def mark_registration(context):
    """Locked intent for the adopting worker's pinned registration, with its stable join request ID."""
    root = _state_root(context['state_root'])
    with _slot(root, context['slot_id'], context['binding']['provider_session_id']) as ledger:
        record = ledger.setting(SLOT_KEY)
        if not record.get('registration'):
            ledger.setting(SLOT_KEY, {**record, 'registration': {'state': 'sent', 'request_id': _registration(record)[2]}})


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


def manage(args, register_agent=None):
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
        if action == 'stop':
            return stop_slot(args.state, args.slot, leave=getattr(args, 'leave', False), register_agent=register_agent)
        if action == 'verify-retired': return verify_retired(args.state, args.slot, register_agent)
        if action == 'rebind':
            return rebind(args.state, args.slot, knowledge=getattr(args, 'knowledge', None) or (),
                          update=bool(getattr(args, 'update_maintenance', False)))
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
        if operation in ('park', 'hold', 'awaiting'):
            # Awaiting-owner actions reuse the scope's existing receipt or reply permission.
            from quorum_native_inbox import scope_operation
            operation = scope_operation(args)
        names = {'connect': ('quorum', 'role', 'project_ref', 'transport', 'parent_thread_id', 'accept_tasks_from',
                             'dispatch_mode', 'accept_tasks_from_members', 'standing_grants_from', 'trust_members',
                             'exclusive_role', 'supersede'),
                 'receive': ('wait_seconds',), 'received': ('message_id',), 'reply': ('message_id', 'priority'),
                 'task-check': ('message_id',), 'standing-check': ('message_id', 'standing_action')}
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
    elif args.command in ('knowledge', 'update'):
        operation = args.command
        arguments = ({'quorum': args.quorum} if operation == 'update' else
                     {'action': args.action, 'owner_action': bool(args.owner_action),
                      'material_result': bool(args.material_result)})
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
    if operation not in KNOWN: raise _denied('operation_denied')
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
    policy = record.get('delegation')
    if isinstance(policy, dict) and ((policy.get('scope') == 'members')
                                    != context['scope'].get('accept_tasks_from_members', False)):
        raise _denied('scope_mismatch')
    from quorum_delegation import member_trust, standing_grants
    if isinstance(policy, dict) and policy.get('scope') == 'members' and (
            (standing_grants(record) or {}).get('from_role') != context['scope'].get('standing_grants_from')
            or member_trust(record) != context['scope'].get('trust_members', False)):
        raise _denied('scope_mismatch')


def _api(auth_ledger, context, explicit=None):
    """Pinned public service first (R2), then the existing profile/auth seam, then a guarded API."""
    from quorum_live import api_for
    service_check(context, explicit)
    api, config, store = api_for(auth_ledger)
    same_resolved(context, config)
    return api, config, store


def owner_binding(context, auth_ledger):
    """The connected worker's own ledger, record, provenance check and pinned API for its scoped knowledge and
    update (adopt, or an opted-in create/accept scope) commands; the caller closes the ledger. Never the parent's session or ledger.

    The check re-reads the persisted binding: a changed slot or worker binding refuses the call and its result."""
    context = {**guard(context), 'arguments': context['arguments']}
    ledger = _actor_ledger(_state_root(context['state_root']), context['session_ref'])
    if ledger is None: raise ValueError('session_not_connected')
    try:
        record = ledger.setting('session-agent')
        if record is None: raise ValueError('session_not_connected')
        _record_matches(record, context)
        def check():
            guard(context)
            _record_matches(ledger.setting('session-agent') or {}, context)
        api, _, _ = _api(auth_ledger, context)
    except BaseException:
        ledger.db.close()
        raise
    return ledger, record, check, api


def capability_status(context):
    root = _state_root(context['state_root'])
    return {**_summary(root, _view(root, context['slot_id'])), 'session_ref': context['session_ref'],
            'transport': TRANSPORT}


def session_dispatch(args, auth_ledger, register_agent, context):
    """Capability-scoped session actions for the logical actor; never the parent pipe or device, and the parent
    ledger only read (an adopt scope's first connect rechecks that the parent's reception is stopped)."""
    action = args.action
    if action == 'status': return capability_status(context)
    if action == 'stop':
        return stop_slot(context['state_root'], context['slot_id'], sid=context['binding']['provider_session_id'])
    context = {**guard(context), 'arguments': context['arguments']}
    session_ref = context['session_ref']
    # Verified before any profile or API work; only connect may create the actor's private ledger.
    ledger = _actor_ledger(_state_root(context['state_root']), session_ref, create=action == 'connect')
    if ledger is None: raise ValueError('session_not_connected')
    def check():
        # The binding guard: the slot capability and the persisted actor binding must both still name this
        # worker. A rebind that completes after this command's proof (while it waits on the service or for the
        # receive locks) refuses every later operation and disclosure of the previous handle.
        guard(context)
        current = ledger.setting('session-agent')
        if current is not None: _record_matches(current, context)
    try:
        record = ledger.setting('session-agent')
        if record is not None: _record_matches(record, context)
        scope = context['scope']
        if action == 'connect' and scope['mode'] == 'adopt' and record is None:
            # First connect, before any service call: the parent's reception for this quorum is still
            # stopped and still names the same pinned agent.
            parent = parent_binding(_state_root(context['state_root']), context['binding']['provider_session_id'],
                                    scope)
            if parent != scope['adopt']: raise _denied('target_mismatch')
        api, _, _ = _api(auth_ledger, context)
        guarded = Guarded(api, check)
        with _effect(guarded.effect):
            return _session_action(args, auth_ledger, register_agent, context, ledger, record, guarded, check)
    finally: ledger.db.close()


def _session_action(args, auth_ledger, register_agent, context, ledger, record, guarded, check):
    """One capability-scoped action over the shared delivery code; `guarded` tracks what was dispatched."""
    import quorum_native_inbox as inbox
    from quorum_delegation import grant, member_grant, standing_check, task_check
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
        if getattr(args, 'exclusive_role', False):
            # The same read-only check as `session connect --exclusive-role`, on the pinned quorum; an adopt
            # scope's parent agent is the predecessor its own lifecycle retires, not a duplicate.
            from quorum_session import claim_role
            claim_role(guarded, auth_ledger, session_ref, context['target']['quorum_id'], scope['role'],
                       scope['project_ref'], record,
                       expected=(scope['adopt']['agent_id'],) if scope['mode'] == 'adopt' else ())
        if scope['mode'] == 'adopt' and not context['target'].get('agent_id'):
            # Persisted before any device or agent request: a lost answer can never release the claim.
            mark_registration(context)
        from quorum_session import role_refusal
        try:
            # An adopt scope's parent agent may coexist with this worker until its owner retires it.
            result = register_agent(guarded, auth_ledger, scope['quorum_slug'], scope['role'], data,
                                    device_key='session-device:' + session_ref,
                                    expected_quorum_id=context['target']['quorum_id'], keep_agent_id=_adopted(scope))
        except QuorumError as error:
            refusal = role_refusal(error, scope['role'])
            if refusal is not None: raise refusal from None
            raise
        with _effect({'state': 'completed', 'operation': 'agent_join', 'quorum_id': result['quorum_id'],
                      'agent_id': result['agent_id']}):
            pin_target(context, result['quorum_id'], result['agent_id'])
            guarded.check()
        fresh = {**result, **data, 'registration': data, 'role': scope['role'], 'quorum': scope['quorum_slug'],
                 'transport': TRANSPORT, 'worker_binding': _worker_binding(context)}
        # The same persisted member grant `session connect --accept-tasks-from-members --standing-grants-from` writes.
        if scope.get('accept_tasks_from_members'):
            fresh['delegation'] = member_grant(fresh, scope.get('standing_grants_from'), scope.get('trust_members', False))
        # Only this scope's coordinator, resolved now: no grant is copied from a parent or another binding.
        if scope['coordinator_role']:
            peer = guarded.call('resolve_role', {'quorum_id': fresh['quorum_id'], 'role': scope['coordinator_role'],
                                                 'project_ref': fresh['project_ref']})
            previous = (record or {}).get('delegation')
            if previous and previous['coordinator_id'] != peer['agent_id']: raise ValueError('coordinator_changed')
            fresh['delegation'] = grant(fresh, peer['agent_id'], scope['coordinator_role'],
                                        dispatch_mode=scope['dispatch_mode'])
        with inbox.control_lock(ledger):
            # Under the lock a rebind takes: a rebind that landed after this connect's last check refuses here,
            # so the previous handle can never write its binding back over the successor's.
            check()
            ledger.setting('session-agent', fresh)
        reception = inbox.initialize_control(ledger, new_binding=record is None)
        return {**result, 'session_ref': session_ref, 'transport': TRANSPORT, 'reception': reception,
                'listener': 'offline', 'worker_binding': fresh['worker_binding'],
                'delegation': fresh.get('delegation')}
    if not record: raise ValueError('session_not_connected')
    # The shared delivery code reuses this tracker, so every dispatched operation stays known.
    if action == 'receive':
        import quorum_notices
        # The owner's debugging opt-out is stated per receive; omitting it receipts transport notices quietly.
        quorum_notices.set_visible(ledger, getattr(args, 'show_transport_notices', False) is True)
        return inbox.receive(guarded, auth_ledger, ledger, record, args.wait_seconds, authorize=check)
    if action == 'standing-check':
        current = lambda: ledger.setting('session-agent')
        if args.standing_action != 'skill-refresh':
            ReleaseGuard(guarded, ledger).check()
            return standing_check(guarded, record, args.message_id, args.standing_action, current, ledger,
                                  authorize=check)
        # As on the parent route: a refresh request is how a stale binding recovers, so a release mismatch is
        # reported, never a reason to refuse; every authority check is unchanged.
        from quorum_release import ReleaseMismatch
        try: release = {'installed': ReleaseGuard(guarded, ledger, stop_on_mismatch=False).check(),
                        'update_required': False}
        except ReleaseMismatch as mismatch: release = mismatch.diagnostic
        return {**standing_check(guarded, record, args.message_id, args.standing_action, current, ledger,
                                 authorize=check), 'release': release}
    ReleaseGuard(guarded, ledger).check()
    if action == 'task-check':
        return task_check(guarded, record, args.message_id, lambda: ledger.setting('session-agent'), authorize=check)
    if action == 'received': return inbox.confirm(guarded, ledger, record, args.message_id, authorize=check)
    if action in inbox.ACTIONS: return inbox.dispatch_action(guarded, ledger, record, args, authorize=check)
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
    from quorum_session import RoleHeld
    try: yield
    # A refused exclusive connect dispatched only reads and registered nothing; its own code and guidance pass.
    except (QuorumError, RoleHeld): raise
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
    anonymous = _anonymous(context['scope'])
    # An open scope keeps its anonymous login only in this state, as the ordinary `--anonymous` accept does;
    # a GitHub or legacy state is never converted (`anonymous_state_conflict`).
    if explicit is not None or anonymous: resolve_profile(ledger.root, explicit, anonymous=anonymous)
    api, config, store = _api(ledger, context, explicit)
    public_service(config)
    if isinstance(store, AnonymousCredentials) != anonymous: raise ValueError('anonymous_state_conflict')
    ensure_login(config, store, anonymous=anonymous)
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
