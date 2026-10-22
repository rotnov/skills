"""Private per-OS-user service registry; never copy rotating OAuth credentials."""
from contextlib import contextmanager, nullcontext
import hashlib
import json
import os
from pathlib import Path
import tempfile
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
def credential_lock(root, name='auth.lock'):
    """Wait for another new helper, then let the caller re-read persisted state."""
    private_directory(root)
    lock = FileLock(Path(root) / name)
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


def auth_user_id(session):
    user = session.get('user') if isinstance(session, dict) else None
    value = user.get('id') if isinstance(user, dict) else None
    return value if isinstance(value, str) and value else None


class SharedCredentials(CredentialStore):
    def __init__(self, profiles, config, root, principal=None):
        # A bound legacy state pins its verified Auth user; a rotated login never serves it.
        self.profiles, self.config, self.principal = profiles, config, principal
        super().__init__(root=private_directory(root))

    def _pinned(self, session):
        if self.principal is not None and auth_user_id(session) != self.principal:
            raise ValueError('auth_profile_conflict')

    def _check(self, profile):
        if profile != self.config.origin: raise ValueError('auth_profile_conflict')
        private_directory(self.backend.root)

    def load(self, profile):
        self._check(profile)
        session = super().load(profile)
        if session is not None:
            self._pinned(session)
            self.profiles.check_identity(profile, session)
        return session

    def save(self, profile, session):
        self._check(profile)
        self._validate(session)
        self._pinned(session)
        self.profiles.check_identity(profile, session)
        super().save(profile, session)

    def delete(self, profile):
        self._check(profile)
        super().delete(profile)

    def established(self):
        with credential_lock(self.profiles.root):
            return self.profiles.read()[self.config.origin]['github_id'] is not None


class AnonymousCredentials(CredentialStore):
    """One state's open-quorum identity; never registered as the machine login."""


ACTIONS = {
    'different_principal': "This state's login belongs to another account or project and was left untouched. "
                           'Use a state bound to the machine login, or a separate QUORUM_PROFILE_ROOT for that account.',
    'legacy_login_unverified': "The Auth server could not verify this state's older login without refreshing it. "
                               "Stop this state's old listener, then run login once with this --state.",
    'machine_login_missing': 'The machine login is signed out. Run login from a state already using it, then retry.',
    'binding_mismatch': "This state's auth-binding.json no longer matches the machine profile. "
                        'Inspect it; neither login is replaced automatically.',
    'machine_principal_changed': 'The machine login changed to another Auth user during verification. '
                                 'Retry; an older binding is never reused for a different user.',
    'bound_principal_unverifiable': "This state is bound to an earlier Auth user whose login is gone, so it cannot be "
                                    're-verified, and the machine login now claims another user. It was left untouched. '
                                    'Use a state bound to the machine login, or a separate QUORUM_PROFILE_ROOT.',
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
    def __init__(self, profiles, config, record, state, stale=None):
        super().__init__('legacy_login_unverified', state / 'credentials', record['credential_root'])
        self.profiles, self.config, self.record, self.state = profiles, config, record, state
        self.stale = stale  # The exact outdated binding this verification may replace.


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


def binding_for(config, record, principal):
    return {'origin': config.origin, 'credential_root': record['credential_root'],
            'github_id': record['github_id'], 'auth_user_id': principal}


def read_binding(state):
    """Return the binding or None; call under the registry lock every publisher holds."""
    # Any temporary here is from an interrupted publisher. Removing it restores
    # the published file's single link; unlink never follows a link.
    for leftover in state.glob('.auth-binding-*.tmp'):
        try:
            os.unlink(leftover)
        except (FileNotFoundError, IsADirectoryError, PermissionError):
            pass
    path = state / 'auth-binding.json'
    reject_link(path)
    return read_private_json(path) if path.exists() else None


def publish_binding(path, binding, stale=None):
    """Publish a complete private binding; replace only the exact stale one just re-verified."""
    current = read_binding(path.parent)
    if current == binding or current != stale: return  # A concurrent helper wrote it; the next pass validates it.
    fd, temporary = tempfile.mkstemp(prefix='.auth-binding-', suffix='.tmp', dir=path.parent)
    try:
        with os.fdopen(fd, 'w') as output:
            if os.name != 'nt': os.fchmod(output.fileno(), 0o600)
            json.dump(binding, output)
            output.flush()
            os.fsync(output.fileno())
        if os.name == 'nt': protect(temporary)
        if stale is None:
            try:
                os.link(temporary, path)  # Atomic, and never replaces an existing binding.
            except FileExistsError:
                pass
        else:
            os.replace(temporary, path)
    finally:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass


def check_legacy_binding(profiles, config, record, state, legacy_session):
    """Let a legacy state use the machine login only after a recorded server-verified match.

    Returns the verified Auth user the machine login must still be. A binding
    stays pinned after its legacy login is gone, which can no longer re-verify it.
    """
    conflict = lambda reason: AuthProfileConflict(reason, state / 'credentials', record['credential_root'])
    if legacy_session is not None:
        if record['github_id'] is None: raise conflict('machine_login_missing')
        try:
            claimed = github_identity(legacy_session)
        except ValueError:
            claimed = None
        # Stored claims are unverified: they may block a link, never establish one.
        if claimed not in (None, record['github_id']): raise conflict('different_principal')
    binding = read_binding(state)
    if binding is None and legacy_session is None: return None
    if binding is None: raise UnverifiedLegacyLogin(profiles, config, record, state)
    expected = binding_for(config, record, None)
    if (not isinstance(binding, dict) or not set(binding) <= set(expected)
            or any(binding.get(key) != expected[key] for key in ('origin', 'credential_root', 'github_id'))):
        raise conflict('binding_mismatch')
    principal = binding.get('auth_user_id')
    # Read without the canonical lock (already held); this is a claim, so it can only reject.
    machine = CredentialStore(root=private_directory(record['credential_root'])).load(config.origin)
    if (not isinstance(principal, str) or not principal
            or auth_user_id(legacy_session) not in (None, principal)
            or (machine is not None and auth_user_id(machine) != principal)):
        # An unpinned or replaced principal is re-verified at the Auth server, never routed.
        if legacy_session is None: raise conflict('bound_principal_unverifiable')
        raise UnverifiedLegacyLogin(profiles, config, record, state, binding)
    return principal


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
        # Pin the server-verified user only while the machine login still claims it.
        current = CredentialStore(root=private_directory(record['credential_root'])).load(config.origin)
        if auth_user_id(current) != machine_user['id']: raise conflict('machine_principal_changed')
        publish_binding(state / 'auth-binding.json', binding_for(config, record, machine_user['id']), pending.stale)


def origin_identity(origin):
    """Compare HTTPS services without changing their stored credential keys."""
    parsed = urlsplit(origin)
    port = parsed.port
    return parsed.scheme, parsed.hostname, 443 if port is None else port


def read_state_config(path):
    """Return this state's config or None; call under its config.lock like every writer."""
    # Any temporary here is from an interrupted writer; removing it restores one link.
    for leftover in path.parent.glob('.config-*.tmp'):
        try:
            os.unlink(leftover)
        except (FileNotFoundError, IsADirectoryError, PermissionError):
            pass
    reject_link(path)
    return read_config(path) if path.exists() else None


def write_config(path, config):
    """Publish a complete private config once; never replace an existing one."""
    fd, temporary = tempfile.mkstemp(prefix='.config-', suffix='.tmp', dir=path.parent)
    try:
        with os.fdopen(fd, 'w') as output:
            if os.name != 'nt': os.fchmod(output.fileno(), 0o600)
            json.dump({'origin': config.origin, 'publishable_key': config.publishable_key}, output)
            output.flush()
            os.fsync(output.fileno())
        if os.name == 'nt': protect(temporary)
        try:
            os.link(temporary, path)  # Atomic: no partial file, and never replaces one.
        except FileExistsError:
            if read_config(path) != config: raise ValueError('service_config_conflict') from None
    finally:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass


def resolve_anonymous(state, path, saved, explicit, create):
    """An open-invitation state keeps its anonymous login inside that state only."""
    root = state / 'anonymous-credentials'
    if not (root.exists() or root.is_symlink()):
        # Never convert a GitHub or legacy state, even for the same service.
        if not create or saved is not None or (state / 'credentials').exists():
            raise ValueError('anonymous_state_conflict')
    config = saved or explicit
    if config is None: raise QuorumError('service_not_configured')
    root = private_directory(root, create=True)
    if saved is None: write_config(path, config)
    return config, AnonymousCredentials(root=root)


def resolve_profile(state, explicit=None, *, anonymous=False, refresh_legacy=False):
    """Bind only the supplied state/legacy directory; never scan other installs."""
    try:
        return _resolve_profile(state, explicit, anonymous)
    except UnverifiedLegacyLogin as pending:
        # Network proof runs outside the registry lock (refresh nests it).
        # Anonymous states return before any legacy binding, so never get here.
        verify_legacy_principal(pending, refresh_legacy)
        return _resolve_profile(state, explicit, anonymous)


def _resolve_profile(state, explicit, anonymous):
    state = private_directory(state, create=True)
    # Concurrent first runs of one state serialize; each sees none or a complete config.
    with credential_lock(state, 'config.lock'):
        return _resolve_state(state, explicit, anonymous)


def _resolve_state(state, explicit, anonymous):
    path = state / 'config.json'
    saved = read_state_config(path)
    if saved is not None and explicit is not None and saved != explicit:
        raise ValueError('service_config_conflict')
    if anonymous or (state / 'anonymous-credentials').exists() or (state / 'anonymous-credentials').is_symlink():
        return resolve_anonymous(state, path, saved, explicit, anonymous)
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
        legacy_session = principal = None
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
            if credential_root != legacy_root:
                # Even the same human can have a distinct refresh-token family:
                # never merge, copy or delete it; only bind a verified principal.
                # A recorded binding pins even after the legacy login is deleted.
                principal = check_legacy_binding(profiles, config, record, state, legacy_session)
        if saved is None: write_config(path, config)
    return config, SharedCredentials(profiles, config, credential_root, principal)


def ensure_login(config, store, *, explicit=False, anonymous=False):
    from quorum_auth import login_anonymous, login_pkce, token_provider
    with auth_lock(store):
        session = store.load(config.origin)
        if session is None:
            if isinstance(store, AnonymousCredentials):
                # A replacement anonymous user would not hold the lost membership.
                if not anonymous: raise QuorumError('open_invite_required')
                return login_anonymous(config, store)
            if isinstance(store, SharedCredentials) and store.established() and not explicit:
                raise QuorumError('auth_required')
            return login_pkce(config, store)
        # Already hold the profile lock: re-use the locked token routine.
        token_provider(config, store, locked=True)()
        return {'authenticated': True, 'reused': True}
