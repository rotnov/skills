"""PKCE and pilot-only unencrypted credential storage. Login is always an explicit action."""
import base64
import hashlib
import hmac
import json
import os
from pathlib import Path
import secrets
import stat
import tempfile
import time
from quorum_os import protect, open_private, reject_link
from urllib.parse import urlencode, urlsplit, parse_qs


class PkceFlow:
    def __init__(self):
        self.verifier = secrets.token_urlsafe(48)
        self.state = secrets.token_urlsafe(32)
        self.challenge = base64.urlsafe_b64encode(hashlib.sha256(self.verifier.encode()).digest()).decode().rstrip('=')
        self.redirect = 'http://127.0.0.1:8768/callback?quorum_state=' + self.state

    def authorization_url(self, origin):
        from quorum_api import ServiceConfig
        ServiceConfig(origin, 'validate-origin')
        return origin.rstrip('/') + '/auth/v1/authorize?' + urlencode({
            'provider': 'github', 'redirect_to': self.redirect,
            'code_challenge': self.challenge, 'code_challenge_method': 's256'})

    def callback_code(self, path):
        url = urlsplit(path)
        data = parse_qs(url.query, strict_parsing=True)
        if (url.path != '/callback' or url.scheme or url.netloc or url.fragment
                or len(data.get('code', [])) != 1 or len(data.get('quorum_state', [])) != 1
                or not hmac.compare_digest(data['quorum_state'][0], self.state)):
            raise ValueError('invalid_oauth_callback')
        if len(data['code'][0]) > 4096:
            raise ValueError('invalid_oauth_callback')
        return data['code'][0]


class CredentialStore:
    def __init__(self, backend=None, *, root=None):
        self.backend = backend if backend is not None else FileCredentials(root)

    def load(self, profile):
        try:
            raw = self.backend.read(profile)
            if raw is None:
                return None
            session = json.loads(raw)
            self._validate(session)
            return session
        except Exception:
            raise ValueError('credential_store_unavailable') from None

    def save(self, profile, session):
        try:
            self._validate(session)
            self.backend.write(profile, json.dumps(session).encode())
        except Exception:
            raise ValueError('credential_store_unavailable') from None

    @staticmethod
    def _validate(session):
        if (not isinstance(session, dict)
                or any(not isinstance(session.get(key), str) or not session[key]
                       for key in ('access_token', 'refresh_token'))):
            raise ValueError('invalid_session')

    def delete(self, profile):
        try:
            self.backend.delete(profile)
        except Exception:
            raise ValueError('credential_store_unavailable') from None


class FileCredentials:
    """Owner-approved pilot storage: plaintext, private directory, atomic writes.

    Set root inside a writable local Docker volume; never inside /run/secrets.
    No Keychain, Docker API, encryption, or dependency installation is attempted.
    """
    limit = 131072

    def __init__(self, root):
        if root is None:
            raise ValueError('credential_directory_required')
        self.root = Path(root)
        if not self.root.is_absolute() or self.root.is_symlink():
            raise ValueError('unsafe_credential_directory')
        self.root.mkdir(mode=0o700, parents=True, exist_ok=True)
        reject_link(self.root)
        if not self.root.is_dir() or (os.name != 'nt' and self.root.stat().st_uid != os.getuid()):
            raise ValueError('unsafe_credential_directory')
        protect(self.root, directory=True)

    def _path(self, profile):
        if not isinstance(profile, str) or not profile:
            raise ValueError('invalid_credential_profile')
        return self.root / (hashlib.sha256(profile.encode()).hexdigest() + '.json')

    @staticmethod
    def _check(info):
        if (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1
                or (os.name != 'nt' and (info.st_uid != os.getuid() or info.st_mode & 0o077))):
            raise ValueError('unsafe_credential_file')

    def read(self, profile):
        path = self._path(profile)
        try:
            if os.name == 'nt' and path.exists():
                self._check(path.lstat())
                protect(path)
            fd = open_private(path, os.O_RDONLY | getattr(os, 'O_NONBLOCK', 0))
        except FileNotFoundError:
            return None
        with os.fdopen(fd, 'rb') as handle:
            self._check(os.fstat(handle.fileno()))
            data = handle.read(self.limit + 1)
        if len(data) > self.limit:
            raise ValueError('credential_file_too_large')
        return data

    def write(self, profile, data):
        path = self._path(profile)
        if len(data) > self.limit:
            raise ValueError('credential_file_too_large')
        try:
            self._check(path.lstat())
        except FileNotFoundError:
            pass
        fd, temporary = tempfile.mkstemp(prefix='.session-', dir=self.root)
        try:
            with os.fdopen(fd, 'wb') as handle:
                if os.name != 'nt': os.fchmod(handle.fileno(), 0o600)
                handle.write(data)
                handle.flush()
                os.fsync(handle.fileno())
            if os.name == 'nt': protect(temporary)
            os.replace(temporary, path)
        finally:
            try:
                os.unlink(temporary)
            except FileNotFoundError:
                pass

    def delete(self, profile):
        path = self._path(profile)
        try:
            self._check(path.lstat())
            path.unlink()
        except FileNotFoundError:
            pass


def token_provider(config, store, clock=time.time):
    from quorum_api import request_json, QuorumError
    def token():
        session = store.load(config.origin)
        if session is None: raise QuorumError('auth_required')
        if session.get('expires_at', 0) <= clock() + 30:
            session = request_json(config.origin.rstrip('/') + '/auth/v1/token?grant_type=refresh_token',
                                   {'apikey': config.publishable_key, 'Content-Type': 'application/json'},
                                   json.dumps({'refresh_token': session['refresh_token']}).encode())
            session['expires_at'] = clock() + session['expires_in']
            store.save(config.origin, session)
        return session['access_token']
    return token


def login_pkce(config, store):
    from http.server import BaseHTTPRequestHandler, HTTPServer
    import webbrowser
    from quorum_api import request_json
    flow, code = PkceFlow(), []
    class Callback(BaseHTTPRequestHandler):
        def log_message(self, *_): pass
        def do_GET(self):
            try:
                code.append(flow.callback_code(self.path))
                self.send_response(200)
                self.end_headers()
                self.wfile.write(b'Quorum login received. You may close this window.')
            except ValueError:
                self.send_response(400)
                self.end_headers()
    with HTTPServer(('127.0.0.1', 8768), Callback) as server:
        server.timeout = 1
        if not webbrowser.open(flow.authorization_url(config.origin)):
            raise ValueError('browser_unavailable')
        deadline = time.monotonic() + 120
        while not code and time.monotonic() < deadline: server.handle_request()
    if not code: raise ValueError('login_timeout')
    session = request_json(config.origin.rstrip('/') + '/auth/v1/token?grant_type=pkce',
                           {'apikey': config.publishable_key, 'Content-Type': 'application/json'},
                           json.dumps({'auth_code': code[0], 'code_verifier': flow.verifier}).encode())
    session['expires_at'] = time.time() + session['expires_in']
    store.save(config.origin, session)
    return {'authenticated': True}
