"""Identity-bound public IDs, with explicitly separate legacy token files."""
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



def public_service(config):
    """Allow only public API bootstrap keys in copyable invitation output."""
    key = config.publishable_key
    if isinstance(key, str) and re.fullmatch(r'sb_publishable_[A-Za-z0-9_-]+', key):
        return {'origin': config.origin, 'publishable_key': key}
    # Legacy Supabase anon JWTs are public; no authenticated/service-role JWTs.
    import base64
    try:
        parts = key.split('.')
        claims = json.loads(base64.urlsafe_b64decode(parts[1] + '=' * (-len(parts[1]) % 4)))
        if len(parts) == 3 and claims.get('role') == 'anon':
            return {'origin': config.origin, 'publishable_key': key}
    except (AttributeError, ValueError, IndexError, TypeError):
        pass
    raise ValueError('public_service_key_required')


def create_id_invitation(api, config, slug, target, request_id):
    import uuid
    if not re.fullmatch(r'[0-9]{1,20}', target) or not 1 <= len(request_id) <= 128:
        raise ValueError('invitation_arguments_invalid')
    service = public_service(config)
    quorum_id = api.call('quorum_lookup', {'slug': slug})['quorum_id']
    api.call('quorum_check_owner', {'quorum_id': quorum_id})
    data = api.call('invite_create_id', {'quorum_id': quorum_id, 'target_github_id': target}, request_id)
    invite_id = str(uuid.UUID(data['invite_id']))
    # Only select public fields; never forward an unexpected backend credential.
    prompt = ('Install the public quorum skill using: '
              'npx --yes skills@1.5.20 add rotnov/skills --skill quorum -a claude-code codex --copy -y. '
              'Read its SKILL.md and references/invite.md. Resolve the installed skill directory, '
              'choose a private local state directory outside the repository, and run its frozen uv '
              f'helper accept-id {invite_id} with --origin and --publishable-key from this public '
              f'service locator: {json.dumps(service)}. Complete my own GitHub browser login if needed. '
              'Accept membership only, then report the quorum ID. Do not start a model, daemon or '
              'headless pilot. For connecting this existing chat, inspect the separate native-session '
              'route and use only its actual session identity. If native transport is unavailable '
              '(including macOS Claude), report that blocker; do not ask for secrets or spoof another client.')
    return {'invite_id': invite_id, 'quorum_id': quorum_id, 'target_github_id': target,
            'expires_at': data['expires_at'], 'service': service,
            'session_prompt': prompt}


def accept_id_invitation(args, ledger):
    import uuid
    from quorum_auth import login_pkce
    from quorum_live import api_for
    invite_id = str(uuid.UUID(args.invite_id))
    from quorum_api import ServiceConfig, QuorumError
    path = ledger.root / 'config.json'
    if bool(args.origin) != bool(args.publishable_key): raise ValueError('service_arguments_required')
    if args.origin:
        config = ServiceConfig(args.origin, args.publishable_key)
        public = public_service(config)
        if path.exists() or path.is_symlink():
            if path.is_symlink() or json.loads(path.read_text()) != public:
                raise ValueError('service_config_conflict')
        else:
            fd = open_private(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL)
            with os.fdopen(fd, 'w') as output: json.dump(public, output)
    elif not path.is_file() or path.is_symlink():
        raise QuorumError('service_not_configured')
    public_service(ServiceConfig(**json.loads(path.read_text())))
    api, config, store = api_for(ledger)
    if store.load(config.origin) is None: login_pkce(config, store)
    accepted = api.call('invite_accept_id', {'invite_id': invite_id}, 'accept-id:' + invite_id)
    return {'quorum_id': accepted['quorum_id'], 'membership_id': accepted['membership_id'],
            'membership_only': True}
