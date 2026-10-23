"""Deliver Quorum into the current Cursor Cloud agent. Never start another agent.

Identity is this process's CURSOR_CONVERSATION_ID. The metadata socket is read
only to confirm that id; the prompt and API key never go there. A follow-up is
POST /v1/agents/<id>/runs on the fixed Cloud Agents host, which continues this
same agent. A busy run or a rate-limited key is deferred and retried. The key
stays in the environment.
"""
import json
import os
from pathlib import Path
import re
import socket
import stat
import subprocess
import sys
import urllib.error
import urllib.request
import uuid


_BC = re.compile(r'^bc-[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$')
_API = 'https://api.cursor.com/v1/agents/'
# Retry-After is honored as delta-seconds up to an hour. A 429 without a usable
# value (absent, or an HTTP-date) waits this long rather than hammering the key.
_RATE_LIMIT_WAIT = 30
_MAX_WAIT = 3600


class DeliveryDeferred(Exception):
    """Cursor refused before creating a run (busy or rate-limited); nothing was submitted."""
    def __init__(self, retry_after=0):
        super().__init__()
        self.retry_after = retry_after


def _seconds(value, default):
    text = value.strip() if isinstance(value, str) else ''
    return min(int(text), _MAX_WAIT) if re.fullmatch('[0-9]{1,7}', text) else default


def _retry_after(headers, default):
    get = getattr(headers, 'get', None)
    return _seconds(get('Retry-After') if get else None, default)


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def canonical_id(value):
    text = str(value).strip().lower()
    if not _BC.fullmatch(text):
        raise ValueError('cursor_session_invalid')
    return text


def _metadata_socket(path):
    if not isinstance(path, str) or not 1 <= len(os.fsencode(path)) <= 107:
        raise ValueError('cursor_identity_unavailable')
    try:
        endpoint = Path(path)
        if (not endpoint.is_absolute() or path.startswith('//') or '..' in endpoint.parts
                or any(ord(c) < 32 or ord(c) == 127 for c in path)):
            raise ValueError
        info = endpoint.lstat()
        parent = endpoint.parent.lstat()
        if (stat.S_ISLNK(info.st_mode) or not stat.S_ISSOCK(info.st_mode)
                or not stat.S_ISDIR(parent.st_mode) or stat.S_ISLNK(parent.st_mode)
                or parent.st_mode & 0o022 or parent.st_uid not in (0, os.geteuid())):
            raise ValueError
    except (OSError, ValueError, TypeError):
        raise ValueError('cursor_identity_unavailable') from None
    return path


def read_agent_id(path):
    """Bounded read of this VM's agent id. Sends no credential and no prompt."""
    endpoint = _metadata_socket(path)
    request = (b'GET /v1/meta-data/agent/id HTTP/1.1\r\nHost: localhost\r\n'
               b'Connection: close\r\n\r\n')
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as connection:
            connection.settimeout(2)
            connection.connect(endpoint)
            connection.sendall(request)
            data = b''
            while len(data) < 2048:
                chunk = connection.recv(512)
                if not chunk:
                    break
                data += chunk
    except (OSError, ValueError):
        raise ValueError('cursor_identity_unavailable') from None
    head, separator, body = data.partition(b'\r\n\r\n')
    if (not separator or not head.startswith(b'HTTP/1.1 200 ')
            or len(body) > 80 or b'\n' in body):
        raise ValueError('cursor_identity_unavailable')
    try:
        return canonical_id(body.decode('ascii').strip())
    except (UnicodeError, ValueError):
        raise ValueError('cursor_identity_unavailable') from None


def _api_key():
    key = os.environ.get('CURSOR_API_KEY', '')
    if not isinstance(key, str) or not 16 <= len(key) <= 4096 or any(ch.isspace() for ch in key):
        raise ValueError('cursor_ingress_unavailable')
    return key


def binding(session_id):
    if os.environ.get('CURSOR_AGENT') != '1':
        raise ValueError('cursor_identity_unavailable')
    try:
        sid = canonical_id(session_id)
        exported = canonical_id(os.environ.get('CURSOR_CONVERSATION_ID', ''))
    except ValueError:
        raise ValueError('cursor_identity_unavailable') from None
    if exported != sid:
        raise ValueError('session_binding_denied')
    if read_agent_id(os.environ.get('CURSOR_AGENT_SOCKET', '')) != sid:
        raise ValueError('cursor_identity_unavailable')
    _api_key()
    return {'session_ref': sid}


def _urlopen(request, timeout):
    return urllib.request.build_opener(_NoRedirect).open(request, timeout=timeout)


def post_followup(session_id, content, key, *, urlopen=_urlopen):
    sid = canonical_id(session_id)
    if not isinstance(content, str) or not content or len(content.encode()) > 48000:
        raise ValueError('invalid_message')
    if not isinstance(key, str) or any(ch.isspace() for ch in key):
        raise ValueError('cursor_ingress_unavailable')
    payload = json.dumps({'prompt': {'text': content}}, ensure_ascii=False).encode()
    request = urllib.request.Request(
        _API + sid + '/runs', data=payload, method='POST',
        headers={'Authorization': 'Bearer ' + key, 'Content-Type': 'application/json',
                 'Accept': 'application/json'})
    headers = None
    try:
        # A refused redirect returns no response, so the key stays on this host.
        response = urlopen(request, timeout=10)
        if response is None:
            raise ValueError('session_delivery_unknown')
        with response:
            status = getattr(response, 'status', None)
            headers = getattr(response, 'headers', None)
            response.read(256)
    except urllib.error.HTTPError as error:
        status, headers = error.code, error.headers
        try: error.read(256)
        except Exception: pass
    except (OSError, urllib.error.URLError, TimeoutError):
        raise ValueError('session_delivery_unknown') from None
    # Both refusals come before a run exists, so the message never reached the agent.
    if status == 409:
        raise DeliveryDeferred(_retry_after(headers, 0))
    if status == 429:
        raise DeliveryDeferred(_retry_after(headers, _RATE_LIMIT_WAIT))
    if status not in (200, 201, 202):
        raise ValueError('session_delivery_unknown')
    return 'submitted'


def submit(record, message_id, content):
    str(uuid.UUID(message_id))
    if not isinstance(content, str) or not content or len(content.encode()) > 48000:
        raise ValueError('invalid_message')
    payload = json.dumps({'session_ref': record['session_ref'], 'content': content},
                         ensure_ascii=False)
    try:
        result = subprocess.run([sys.executable, str(Path(__file__).resolve())], input=payload,
                                text=True, encoding='utf-8', capture_output=True, timeout=20,
                                creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)
    except (OSError, subprocess.TimeoutExpired):
        raise ValueError('session_delivery_unknown') from None
    if result.returncode == 2:
        raise DeliveryDeferred(_seconds(result.stdout, 0))
    if result.returncode:
        raise ValueError('session_delivery_unknown')


def main():
    try:
        raw = sys.stdin.buffer.read(131073)
        if len(raw) > 131072:
            raise ValueError('invalid_message')
        data = json.loads(raw)
        confirmed = binding(data['session_ref'])
        if confirmed['session_ref'] != canonical_id(data['session_ref']):
            raise ValueError('session_binding_denied')
        post_followup(confirmed['session_ref'], data['content'], _api_key())
        return 0
    except DeliveryDeferred as deferred:
        # Only the bounded wait crosses back to the listener.
        print(int(deferred.retry_after), flush=True)
        return 2
    except Exception:
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
