"""Owner-side invitation creation; plaintext token goes only to a private file."""
import hashlib
import json
import os
import re
from quorum_os import open_private, protect


def github_id(username, get=None):
    if not isinstance(username, str) or not re.fullmatch(r'[A-Za-z0-9](?:[A-Za-z0-9-]{0,37}[A-Za-z0-9])?', username):
        raise ValueError('github_user_invalid')
    if get is None:
        from urllib.request import Request, build_opener
        from quorum_api import NoRedirect
        def get(url):
            request = Request(url, headers={'Accept': 'application/vnd.github+json', 'User-Agent': 'quorum-pilot'})
            with build_opener(NoRedirect).open(request, timeout=20) as response:
                raw = response.read(262145)
            if len(raw) > 262144: raise ValueError('github_response_too_large')
            return json.loads(raw)
    result = get('https://api.github.com/users/' + username)
    if (not isinstance(result.get('login'), str) or result['login'].lower() != username.lower()
            or type(result.get('id')) is not int or result['id'] <= 0):
        raise ValueError('github_identity_invalid')
    return str(result['id'])


def create_invitation(api, config, ledger, slug, target, request_id):
    from quorum_live import read_invitation
    if not re.fullmatch(r'[0-9]{1,20}', target) or not 1 <= len(request_id) <= 128:
        raise ValueError('invitation_arguments_invalid')
    quorum_id = api.call('quorum_lookup', {'slug': slug})['quorum_id']
    api.call('quorum_check_owner', {'quorum_id': quorum_id})
    directory = ledger.root / 'invitations'
    directory.mkdir(mode=0o700, exist_ok=True)
    protect(directory, directory=True)
    key = hashlib.sha256(json.dumps([quorum_id, target, request_id]).encode()).hexdigest()[:24]
    path = directory / ('invitation-' + key + '.json')
    if path.exists():
        data = read_invitation(path)
        if any(data[k] != value for k, value in (
                ('quorum_id', quorum_id), ('target_github_id', target), ('origin', config.origin),
                ('publishable_key', config.publishable_key), ('quorum', slug))):
            raise ValueError('invitation_file_conflict')
    else:
        fd = open_private(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL)
        try:
            data = api.call('invite_create', {'quorum_id': quorum_id, 'target_github_id': target}, request_id)
            if not isinstance(data.get('token'), str) or not re.fullmatch('[a-f0-9]{64}', data['token']):
                raise ValueError('invitation_token_unavailable')
            data.update(origin=config.origin, publishable_key=config.publishable_key, quorum=slug,
                        quorum_id=quorum_id, target_github_id=target)
            with os.fdopen(fd, 'w') as output:
                fd = None
                json.dump(data, output)
                output.flush()
                os.fsync(output.fileno())
            if os.name == 'nt': protect(path)
        except Exception:
            if fd is not None: os.close(fd)
            path.unlink(missing_ok=True)
            raise
    return {'invitation_file': str(path), 'quorum_id': quorum_id,
            'target_github_id': target, 'invite_id': data['invite_id'], 'expires_at': data['expires_at']}
