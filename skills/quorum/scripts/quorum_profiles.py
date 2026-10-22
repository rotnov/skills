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


ACTIONS = {
    'different_principal': "This state's login belongs to another account or project and was left untouched. "
                           'Use a state bound to the machine login, or a separate QUORUM_PROFILE_ROOT for that account.',
    'legacy_login_unverified': "The Auth server could not verify this state's older login without refreshing it. "
                               "Stop this state's old listener, then run login once with this --state.",
    'machine_login_missing': 'The machine login is signed out. Run login from a state already using it, then retry.',
    'binding_mismatch': "This state's auth-binding.json no longer matches the machine profile. "
                        'Inspect it; neither login is replaced automatically.',
}


class AuthProfileConflict(ValueError):
    """auth_profile_conflict with a token-free, actionable diagnostic."""
    def __init__(self, reason, state_credentials, machine_credentials):
        super().__init__('auth_profile_conflict')
        self.details = {'reason': reason, 'action': ACTIONS[reason],
                        'state_credentials': str(state_credentials), 'machine_credentials': str(machine_credentials)}

    def diagnostic(self):
        return dict(self.details)


class UnverifiedLegacyLogin(AuthProfileConflict):
    """Raised under the registry lock; resolve_profile verifies after releasing it."""
    def __init__(self, profiles, config, record, state):
        super().__init__('legacy_login_unverified', state / 'credentials', record['credential_root'])
        self.profiles, self.config, self.record, self.state = profiles, config, record, state


def read_private_json(path, limit=16384):
    if os.name == 'nt' and path.exists(): protect(path)
    fd = open_private(path, os.O_RDONLY | getattr(os, 'O_NONBLOCK', 0))
    with os.fdopen(fd, 'rb') as source:
        FileCredentials._check(os.fstat(source.fileno()))
        raw = source.read(limit + 1)
    if len(raw) > limit: raise ValueError('unsafe_config_file')
    return json.loads(raw)


def read_config(path):
    return ServiceConfig(**read_private_json(path))


def binding_for(config, record):
    return {'origin': config.origin, 'credential_root': record['credential_root'], 'github_id': record['github_id']}


def check_legacy_binding(profiles, config, record, state, legacy_session):
    """Let a legacy state use the machine login only after a recorded server-verified match."""
    conflict = lambda reason: AuthProfileConflict(reason, state / 'credentials', record['credential_root'])
    if record['github_id'] is None: raise conflict('machine_login_missing')
    try:
        claimed = github_identity(legacy_session)
    except ValueError:
        claimed = None
    # Stored claims are unverified: they may block a link, never establish one.
    if claimed not in (None, record['github_id']): raise conflict('different_principal')
    path = state / 'auth-binding.json'
    reject_link(path)
    if not path.exists(): raise UnverifiedLegacyLogin(profiles, config, record, state)
    if read_private_json(path) != binding_for(config, record): raise conflict('binding_mismatch')


def verify_legacy_principal(pending, refresh_legacy):
    """Prove both token families belong to one server-verified principal, then record that."""
    from quorum_auth import token_provider, verified_user
    profiles, config, record, state = pending.profiles, pending.config, pending.record, pending.state
    conflict = lambda reason: AuthProfileConflict(reason, state / 'credentials', record['credential_root'])
    machine = SharedCredentials(profiles, config, record['credential_root'])
    if machine.load(config.origin) is None: raise conflict('machine_login_missing')
    # Refresh only through the canonical lock, which every new helper shares.
    machine_user = verified_user(config, token_provider(config, machine)())
    legacy = CredentialStore(root=private_directory(state / 'credentials'))
    session = legacy.load(config.origin)
    if session is None: return
    if session.get('expires_at', 0) > time.time() + 30:
        token = session['access_token']
    elif refresh_legacy:
        # Explicit login only: an old unlocked listener may still rotate this family.
        token = token_provider(config, legacy)()
    else:
        raise conflict('legacy_login_unverified')
    try:
        legacy_user = verified_user(config, token)
    except QuorumError as error:
        if str(error) == 'auth_required': raise conflict('legacy_login_unverified') from None
        raise
    try:
        same = (legacy_user['id'] == machine_user['id']
                and github_identity({'user': legacy_user}) == github_identity({'user': machine_user}) == record['github_id'])
    except ValueError:
        same = False
    if not same: raise conflict('different_principal')
    with credential_lock(profiles.root):
        if profiles.read().get(config.origin) != record: raise conflict('binding_mismatch')
        path = state / 'auth-binding.json'
        reject_link(path)
        try:
            fd = open_private(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL)
        except FileExistsError:
            return  # A concurrent helper recorded it; the next pass validates it.
        with os.fdopen(fd, 'w') as output:
            json.dump(binding_for(config, record), output)
            output.flush()
            os.fsync(output.fileno())
        if os.name == 'nt': protect(path)


def origin_identity(origin):
    """Compare HTTPS services without changing their stored credential keys."""
    parsed = urlsplit(origin)
    port = parsed.port
    return parsed.scheme, parsed.hostname, 443 if port is None else port


def resolve_profile(state, explicit=None, *, refresh_legacy=False):
    """Bind only the supplied state/legacy directory; never scan other installs."""
    try:
        return _resolve_profile(state, explicit)
    except UnverifiedLegacyLogin as pending:
        # Network proof runs outside the registry lock (refresh nests it).
        verify_legacy_principal(pending, refresh_legacy)
        return _resolve_profile(state, explicit)


def _resolve_profile(state, explicit):
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
                # Even the same human can have a distinct refresh-token family:
                # never merge, copy or delete it; only bind a verified principal.
                check_legacy_binding(profiles, config, record, state, legacy_session)
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
