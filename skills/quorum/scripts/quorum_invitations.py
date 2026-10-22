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


REFRESH = ('Read the installed SKILL.md and references/join.md. Resolve the installed skill directory, '
           'perform references/update.md automatic refresh once for this request, and read '
           'the returned persistent copy instructions before acceptance. Use that copy, '
           'choose a private local state directory outside the repository, and run its frozen uv ')
COMPLETION = ('After acceptance, complete references/join.md in this same chat: read references/sessions.md, '
              'run session connect for the selected quorum, then session start and session status. '
              'This request authorizes the native transport listener until I stop it. '
              'Use my supplied role and shared project reference if present; otherwise use '
              '<engine>-<first-eight-characters-of-this-session-UUID> as the role and the quorum slug '
              'as the project reference. Use only this actual current session identity. '
              'Reuse a matching existing binding; stop on a conflicting one instead of replacing it. '
              'Once status confirms the listener is running, say Hello in this chat with the quorum, '
              'role and project reference, and report the persistent installed skill path. '
              'A local greeting is not a peer delivery receipt; verify a peer message and reply separately. '
              'Do not start a model, another session, headless pilot or OS startup service. '
              'If native transport is unavailable report that blocker; do not ask for secrets or spoof another client. '
              'For connection failures, I authorize a sanitized report to https://github.com/rotnov/skills/issues. '
              'Check existing issues first; keep one report per distinct failure. Include client, OS, versions, '
              'failed step, safe error code and actual listener state; no tokens, OAuth codes, full environment, '
              'full configuration or private project data. With no GitHub write access, prepare issue text '
              'and the issue link instead of requesting secrets.')
INSTALL = ('If quorum is not installed, install it globally using: '
           'npx --yes skills@1.5.20 add rotnov/skills --skill quorum -a claude-code codex --copy -g -y. '
           'Run installation in a persistent directory outside the project, not a temporary scratch folder. '
           'If already installed, use the immutable automatic refresh below. ')


def issue_id_invitation(api, config, slug, operation, payload, request_id):
    import uuid
    if not 1 <= len(request_id) <= 128: raise ValueError('invitation_arguments_invalid')
    service = public_service(config)
    quorum_id = api.call('quorum_lookup', {'slug': slug})['quorum_id']
    api.call('quorum_check_owner', {'quorum_id': quorum_id})
    data = api.call(operation, {'quorum_id': quorum_id, **payload}, request_id)
    # Only select public fields; never forward an unexpected backend credential.
    return str(uuid.UUID(data['invite_id'])), quorum_id, data['expires_at'], service


def create_id_invitation(api, config, slug, target, request_id, *, github_user=None):
    if not re.fullmatch(r'[0-9]{1,20}', target): raise ValueError('invitation_arguments_invalid')
    if github_user is not None and not re.fullmatch(r'[A-Za-z0-9](?:[A-Za-z0-9-]{0,37}[A-Za-z0-9])?', github_user):
        raise ValueError('github_user_invalid')
    invite_id, quorum_id, expires_at, service = issue_id_invitation(
        api, config, slug, 'invite_create_id', {'target_github_id': target}, request_id)
    target_label = (f'@{github_user}, ' if github_user else '') + f'GitHub ID {target}'
    introduction = (f'This invitation is for {target_label}; quorum slug is {json.dumps(slug)}; '
                    f'expires at {json.dumps(expires_at)}. GitHub is required and the account '
                    'must match this invitation. Sign in through GitHub via Supabase Auth; '
                    'no Supabase dashboard registration is needed. ')
    prompt = (introduction + INSTALL + REFRESH + f'helper accept-id {invite_id} with --origin and --publishable-key from this public '
              f'service locator: {json.dumps(service)}. Reuse my machine login; complete my own GitHub '
              'browser login only for initial setup or explicit reauthentication. ' + COMPLETION)
    configured = (introduction + 'Use the installed quorum skill and my configured machine profile. '
                  + REFRESH + f'helper accept-id {invite_id} without service flags. '
                  'Reuse my existing machine login. Missing or ambiguous service profiles are blockers; '
                  'never infer a server from the invitation UUID. ' + COMPLETION)
    return {'invite_id': invite_id, 'quorum': slug, 'quorum_id': quorum_id, 'target_github_id': target,
            'target_github_user': github_user, 'expires_at': expires_at, 'service': service,
            'session_prompt': prompt, 'configured_machine_prompt': configured}


def create_open_invitation(api, config, slug, request_id):
    invite_id, quorum_id, expires_at, service = issue_id_invitation(
        api, config, slug, 'invite_create_open', {}, request_id)
    prompt = (f'This single-use open invitation joins quorum slug {json.dumps(slug)}; it expires at '
              f'{json.dumps(expires_at)}. No GitHub login or browser is needed: accept it with a new '
              'anonymous Quorum identity kept only in a fresh private state directory. Anyone holding this '
              'text before acceptance can use it; do not forward it. ' + INSTALL + REFRESH
              + f'helper accept-id {invite_id} --anonymous with --origin and --publishable-key from this '
              f'public service locator: {json.dumps(service)}. ' + COMPLETION)
    return {'invite_id': invite_id, 'quorum': slug, 'quorum_id': quorum_id, 'access': 'open',
            'expires_at': expires_at, 'service': service, 'session_prompt': prompt}


def accept_id_invitation(args, ledger):
    import uuid
    from quorum_live import api_for
    from quorum_profiles import resolve_profile, ensure_login, AnonymousCredentials
    from quorum_api import ServiceConfig
    invite_id = str(uuid.UUID(args.invite_id))
    if bool(args.origin) != bool(args.publishable_key): raise ValueError('service_arguments_required')
    explicit = ServiceConfig(args.origin, args.publishable_key) if args.origin else None
    if explicit is not None: public_service(explicit)
    config, _ = resolve_profile(ledger.root, explicit, anonymous=args.anonymous)
    public_service(config)
    api, config, store = api_for(ledger)
    # A GitHub invitation needs the machine login, never this state's anonymous one.
    if isinstance(store, AnonymousCredentials) != args.anonymous: raise ValueError('anonymous_state_conflict')
    ensure_login(config, store, anonymous=args.anonymous)
    accepted = api.call('invite_accept_id', {'invite_id': invite_id}, 'accept-id:' + invite_id)
    return {'quorum_id': accepted['quorum_id'], 'membership_id': accepted['membership_id'],
            'membership_only': True}
