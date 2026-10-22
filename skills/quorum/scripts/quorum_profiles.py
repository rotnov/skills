"""Private per-OS-user service registry; never copy rotating OAuth credentials."""
from contextlib import contextmanager, nullcontext
import hashlib
import json
import os
from pathlib import Path
import time
from urllib.parse import urlsplit

from quorum_api import ServiceConfig, QuorumError
from quorum_auth import CredentialStore, FileCredentials
from quorum_os import FileLock, reject_link, protect, open_private


def private_directory(path, *, create=False):
    path = Path(path)
    if not path.is_absolute() or '..' in path.parts:
        raise ValueError('unsafe_profile_directory')
    # Reject indirect paths before creating anything (including ancestor links).
    for component in [*reversed(path.parents), path]:
        reject_link(component)
    if not path.exists() and create:
        path.mkdir(mode=0o700, parents=True, exist_ok=True)
        protect(path, directory=True)
    if not path.is_dir(): raise ValueError('machine_profile_unavailable')
    info = path.stat()
    if os.name != 'nt' and (info.st_uid != os.getuid() or info.st_mode & 0o077):
        raise ValueError('unsafe_profile_directory')
    if os.name == 'nt': protect(path, directory=True)
    return path


@contextmanager
def credential_lock(root):
    """Wait for another new helper, then let the caller re-read persisted state."""
    private_directory(root)
    lock = FileLock(Path(root) / 'auth.lock')
    deadline = time.monotonic() + 180
    while True:
        try:
            lock.__enter__()
            break
        except ValueError as error:
            if str(error) != 'worker_already_running': raise
            if time.monotonic() >= deadline: raise ValueError('auth_lock_timeout') from None
            time.sleep(.05)
    try:
        # Refuse existing shared/hardlinked locks as well as links.
        FileCredentials._check(os.fstat(lock.fd))
        if os.name == 'nt': protect(lock.path)
        yield
    finally:
        lock.__exit__()


def auth_lock(store):
    # In-memory backends keep their prior API; disk stores serialize per path.
    if isinstance(store.backend, FileCredentials):
        return credential_lock(store.backend.root)
    return nullcontext()


def github_identity(session):
    user = session.get('user')
    if not isinstance(user, dict) or not isinstance(user.get('identities'), list):
        raise ValueError('github_identity_required')
    ids = set()
    for identity in user['identities']:
        if not isinstance(identity, dict): raise ValueError('github_identity_required')
        if identity.get('provider') == 'github':
            data = identity.get('identity_data', {})
            if not isinstance(data, dict): raise ValueError('github_identity_required')
            value = data.get('provider_id') or identity.get('id')
            if isinstance(value, (str, int)) and str(value).isdigit() and int(value) > 0:
                ids.add(str(value))
    if len(ids) != 1: raise ValueError('github_identity_required')
    return ids.pop()


class MachineProfiles:
    def __init__(self):
        configured = os.environ.get('QUORUM_PROFILE_ROOT')
        self.root = private_directory(Path(configured) if configured else
                                      Path.home() / '.local/share/quorum/machine-profiles', create=True)
        self.files = FileCredentials(self.root)

    def read(self):
        raw = self.files.read('registry')
        data = {} if raw is None else json.loads(raw)
        if not isinstance(data, dict): raise ValueError('invalid_profile_registry')
        for origin, record in data.items():
            if not isinstance(record, dict) or set(record) != {'publishable_key', 'credential_root', 'github_id'}:
                raise ValueError('invalid_profile_registry')
            ServiceConfig(origin, record['publishable_key'])
            if record['github_id'] is not None and (not isinstance(record['github_id'], str) or not record['github_id'].isdigit()):
                raise ValueError('invalid_profile_registry')
        return data

    def write(self, records):
        self.files.write('registry', json.dumps(records).encode())

    def check_identity(self, origin, session):
        identity = github_identity(session)
        with credential_lock(self.root):
            records = self.read()
            selected = records[origin]
            if selected['github_id'] not in (None, identity):
                raise ValueError('auth_profile_conflict')
            if selected['github_id'] is None:
                selected['github_id'] = identity
                self.write(records)


class SharedCredentials(CredentialStore):
    def __init__(self, profiles, config, root):
        self.profiles, self.config = profiles, config
        super().__init__(root=private_directory(root))

    def _check(self, profile):
        if profile != self.config.origin: raise ValueError('auth_profile_conflict')
        private_directory(self.backend.root)

    def load(self, profile):
        self._check(profile)
        session = super().load(profile)
        if session is not None: self.profiles.check_identity(profile, session)
        return session

    def save(self, profile, session):
        self._check(profile)
        self._validate(session)
        self.profiles.check_identity(profile, session)
        super().save(profile, session)

    def delete(self, profile):
        self._check(profile)
        super().delete(profile)

    def established(self):
        with credential_lock(self.profiles.root):
            return self.profiles.read()[self.config.origin]['github_id'] is not None


def read_config(path):
    if os.name == 'nt' and path.exists(): protect(path)
    fd = open_private(path, os.O_RDONLY | getattr(os, 'O_NONBLOCK', 0))
    with os.fdopen(fd, 'rb') as source:
        FileCredentials._check(os.fstat(source.fileno()))
        raw = source.read(16385)
    if len(raw) > 16384: raise ValueError('unsafe_config_file')
    return ServiceConfig(**json.loads(raw))


def origin_identity(origin):
    """Compare HTTPS services without changing their stored credential keys."""
    parsed = urlsplit(origin)
    port = parsed.port
    return parsed.scheme, parsed.hostname, 443 if port is None else port


def resolve_profile(state, explicit=None):
    """Bind only the supplied state/legacy directory; never scan other installs."""
    state = private_directory(state, create=True)
    path = state / 'config.json'
    reject_link(path)
    saved = read_config(path) if path.exists() else None
    if saved is not None and explicit is not None and saved != explicit:
        raise ValueError('service_config_conflict')
    profiles = MachineProfiles()
    with credential_lock(profiles.root):
        records = profiles.read()
        config = saved or explicit
        if config is None:
            if not records: raise QuorumError('service_not_configured')
            if len(records) != 1: raise ValueError('service_profile_ambiguous')
            origin, record = next(iter(records.items()))
            config = ServiceConfig(origin, record['publishable_key'])
        # Preserve an adopted origin's exact credential hash. An alternate
        # spelling of the same service must not create another token family.
        if any(origin != config.origin and origin_identity(origin) == origin_identity(config.origin)
               for origin in records):
            raise ValueError('service_config_conflict')
        record = records.get(config.origin)
        if record is not None and record['publishable_key'] != config.publishable_key:
            raise ValueError('service_config_conflict')
        legacy_root = state / 'credentials'
        if legacy_root == profiles.root:
            raise ValueError('unsafe_profile_directory')
        legacy_session = None
        if legacy_root.exists() or legacy_root.is_symlink():
            private_directory(legacy_root)
            legacy_session = CredentialStore(root=legacy_root).load(config.origin)
        if record is None:
            # Adopt only this explicitly selected state's existing login in place.
            credential_root = legacy_root if legacy_session else profiles.root / hashlib.sha256(config.origin.encode()).hexdigest()
            private_directory(credential_root, create=True)
            record = {'publishable_key': config.publishable_key, 'credential_root': str(credential_root),
                      'github_id': github_identity(legacy_session) if legacy_session else None}
            records[config.origin] = record
            profiles.write(records)
        else:
            credential_root = private_directory(record['credential_root'])
            if legacy_session and credential_root != legacy_root:
                # Even the same human can have a distinct refresh-token family.
                raise ValueError('auth_profile_conflict')
        if saved is None:
            fd = open_private(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL)
            with os.fdopen(fd, 'w') as output:
                json.dump({'origin': config.origin, 'publishable_key': config.publishable_key}, output)
                output.flush()
                os.fsync(output.fileno())
            if os.name == 'nt': protect(path)
    return config, SharedCredentials(profiles, config, credential_root)


def ensure_login(config, store, *, explicit=False):
    from quorum_auth import login_pkce, token_provider
    with auth_lock(store):
        session = store.load(config.origin)
        if session is None:
            if isinstance(store, SharedCredentials) and store.established() and not explicit:
                raise QuorumError('auth_required')
            return login_pkce(config, store)
        # Already hold the profile lock: re-use the locked token routine.
        token_provider(config, store, locked=True)()
        return {'authenticated': True, 'reused': True}
