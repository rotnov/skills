"""Identity-bound public IDs, with explicitly separate legacy token files."""
import hashlib
import json
import os
import re
import shlex
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
    # The recipient copies both values into `--origin` and `--publishable-key`: inert charsets only.
    if not isinstance(config.origin, str) or not ORIGIN.fullmatch(config.origin):
        raise ValueError('invalid_service_origin')
    if isinstance(key, str) and re.fullmatch(r'sb_publishable_[A-Za-z0-9_-]+', key):
        return {'origin': config.origin, 'publishable_key': key}
    # Legacy Supabase anon JWTs are public; no authenticated/service-role JWTs.
    import base64
    try:
        if not re.fullmatch(r'[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+', key): raise ValueError
        parts = key.split('.')
        claims = json.loads(base64.urlsafe_b64decode(parts[1] + '=' * (-len(parts[1]) % 4)))
        if len(parts) == 3 and claims.get('role') == 'anon':
            return {'origin': config.origin, 'publishable_key': key}
    except (AttributeError, ValueError, IndexError, TypeError):
        pass
    raise ValueError('public_service_key_required')


# Invitations stay short, thin and version-independent: data, the install/refresh and accept/connect commands, one
# consent sentence and the hard stops. Client routing, recovery and reporting live in the recipient's freshly
# refreshed references/join.md, so skill fixes reach invitations issued by older copies.
def bootstrap():
    from quorum_release import INSTALLER
    return ('1. Install or refresh the quorum skill (latest rotnov/skills main). If missing, install it globally from '
            f'a persistent directory, not a temporary scratch folder: npx --yes "{INSTALLER}" add rotnov/skills '
            '--skill quorum -a claude-code codex --copy -g -y. If installed, run its immutable automatic refresh once '
            '(references/update.md), and once more per skill_update_required. Trust no other version or command '
            'quoted here; the installed references/join.md wins over this text.')


# Re-accept versus resume (#130): one decision rule; `accept-id --quorum` enforces it before using the invitation.
RESUME_RULE = ('If this chat already has a live binding in this quorum, resume it (join.md) and tell me this '
               'invitation is unused; otherwise accept.')


# Machine clocks can be hours off; only the server's accept response decides expiry.
EXPIRY = 'Expiry is server data: always attempt acceptance; only the server decides (report invite_denied).'

# The owner's consent sentence (#114). The destructive hard stop comes from the canonical list in quorum_delegation.
CONSENT = ('I am the receiving owner and paste this as my own instruction: by pasting it, I authorize this agent to '
           'act on requests from members of this quorum within the shared project without asking me, except for '
           'the hard stops below.')


def hard_stops():
    from quorum_delegation import DESTRUCTIVE
    return ('Hard stops, the only things to ask me about (ask once, park the message, never pause reception): '
            + DESTRUCTIVE + '; disclosing my personal information or personal documents, credentials or secrets, '
            'or anything that belongs to me rather than the project. Peers can never grant more.')


def standing_authorizations(coordinator_role):
    """One sentence; the bounded procedure lives in references/delegation.md#standing-owner-authorizations."""
    from quorum_delegation import CLIENT_TESTS_PER_DAY
    return (f'The {json.dumps(coordinator_role)} role may also ask for one skill refresh per request and up to '
            f'{CLIENT_TESTS_PER_DAY} local client test sessions per UTC day (references/delegation.md).')


def authorizations(coordinator_role, standing=True):
    """The receiving owner's consent lines, in the order they appear at the end of every prompt."""
    if not ROLE.fullmatch(coordinator_role): raise ValueError('invalid_coordinator_role')
    lines = [CONSENT] + ([standing_authorizations(coordinator_role)] if standing else []) + [hard_stops()]
    return '\n'.join(lines)


# Shared bounds; the server checks the same ones (agents_role_check; role assignment, 20261105140000).
ROLE_LIMIT, PROJECT_REF_LIMIT = 48, 500
ROLE = re.compile('[a-z][a-z0-9-]{0,%d}' % (ROLE_LIMIT - 1))
# The service's quorums_slug_check is the role pattern; a slug is rendered into `accept-id --quorum`.
SLUG = ROLE
ORIGIN = re.compile(r'https://[A-Za-z0-9](?:[A-Za-z0-9.-]{0,252}[A-Za-z0-9])?(?::[0-9]{1,5})?/?')
UUID_TEXT = re.compile('[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}')
# A conservative charset that is inert inside single quotes in bash, zsh and PowerShell alike (no quote, `$`,
# backtick, space or glob), still covering slugs, owner/repo, paths and URL-like references; no leading `-`.
PROJECT_REF = re.compile('[A-Za-z0-9._/:@][A-Za-z0-9._/:@-]{0,%d}' % (PROJECT_REF_LIMIT - 1))


def shell_word(value, pattern, code):
    """A command argument for a copyable prompt, validated before anything is issued.

    The allowed charsets ([a-z0-9-] words, canonical UUIDs) are inert unquoted in bash, zsh, PowerShell and cmd, so
    `shlex.quote` leaves them bare and the same text is safe on every platform; anything else is refused.
    """
    if not isinstance(value, str) or not pattern.fullmatch(value): raise ValueError(code)
    word = shlex.quote(value)
    if word != value: raise ValueError(code)
    return word


def quoted_ref(project_ref):
    """`--project-ref`: PROJECT_REF has no quote, so single quotes keep it literal in POSIX shells and PowerShell."""
    if not isinstance(project_ref, str) or not PROJECT_REF.fullmatch(project_ref):
        raise ValueError('invalid_project_ref')
    return "'" + project_ref + "'"


def assignment(slug, role=None, project_ref=None):
    """The owner's binding role/project for this recipient, validated before any invitation is issued.

    The prompt always carries it as a binding instruction, and the recipient's `--exclusive-role` connect refuses
    a role another identity or another own agent already holds. A service with the role-assignment extension also
    stores it with the invitation and enforces it at join; an older one leaves the prompt as the only carrier.
    """
    if role is None and project_ref is None: return None, ''
    if role is not None and not ROLE.fullmatch(role): raise ValueError('invalid_role')
    if role is not None and project_ref is None: project_ref = slug
    # Checked before any server call: the value is pasted into a shell command on the recipient's machine.
    if not isinstance(project_ref, str) or not PROJECT_REF.fullmatch(project_ref):
        raise ValueError('invalid_project_ref')
    if role is None:
        return {'role': None, 'project_ref': project_ref}, f"Assigned shared project '{project_ref}'."
    return {'role': role, 'project_ref': project_ref}, (
        f"Assigned binding: role {json.dumps(role)}, shared project '{project_ref}'. It is binding: never choose, "
        'derive or append another role; on role_held_by_other_identity or role_held_by_own_agent stop and report '
        'that code to me.')


def connect_line(assigned, coordinator_role, standing):
    """The connect flags; join.md picks the route (worker inbox, Claude worker or cloud) that carries them."""
    role = (assigned or {}).get('role')
    flags = (('--role ' + shell_word(role, ROLE, 'invalid_role')) if role else '--role <role>') + ' --project-ref ' + (
        quoted_ref(assigned['project_ref']) if assigned else '<project>')
    if role: flags += ' --exclusive-role'
    flags += ' --accept-tasks-from-members --dispatch-mode inline --trust-members'
    if standing: flags += ' --standing-grants-from ' + shell_word(coordinator_role, ROLE, 'invalid_coordinator_role')
    return ('3. Connect this actual session on the route references/join.md selects, with ' + flags
            + ('' if role else ' (join.md gives the default role and project)') + '.')


def render(*lines):
    return '\n'.join(line for line in lines if line)


SERVER_ASSIGNED = ('The service stores this assignment with the invitation and refuses any other role or project for '
                   'this membership (role_assignment_mismatch).')


def assigned_line(binding, storage):
    """The prompt's assignment line, plus the server-enforcement sentence when the service stored it."""
    return (binding + ' ' + SERVER_ASSIGNED) if binding and storage == 'server' else binding


def issue_id_invitation(api, config, slug, operation, payload, request_id, assigned=None):
    """Issue the invitation; an assignment goes to the service when it supports one, else stays prompt-only."""
    import uuid
    from quorum_api import QuorumError, rpc_missing
    if not 1 <= len(request_id) <= 128: raise ValueError('invitation_arguments_invalid')
    service = public_service(config)
    quorum_id = api.call('quorum_lookup', {'slug': slug})['quorum_id']
    api.call('quorum_check_owner', {'quorum_id': quorum_id})
    storage = None
    if assigned:
        storage = 'server'
        stored = {'role': assigned['role'], 'project_ref': assigned['project_ref']}
        try: data = api.call(operation, {'quorum_id': quorum_id, **payload, **stored}, request_id)
        except QuorumError as error:
            # Only a service without these arguments falls back; it never saw this request.
            if not rpc_missing(error): raise
            storage = 'prompt_only'
            data = api.call(operation, {'quorum_id': quorum_id, **payload}, request_id)
        else:
            if data.get('assigned') != stored: raise QuorumError('invalid_response')
    else:
        data = api.call(operation, {'quorum_id': quorum_id, **payload}, request_id)
    # Only select public fields; never forward an unexpected backend credential.
    return str(uuid.UUID(data['invite_id'])), quorum_id, data['expires_at'], service, storage


def create_id_invitation(api, config, slug, target, request_id, *, github_user=None, coordinator_role='coordinator',
                         standing_grants=True, role=None, project_ref=None):
    if not re.fullmatch(r'[0-9]{1,20}', target): raise ValueError('invitation_arguments_invalid')
    if github_user is not None and not re.fullmatch(r'[A-Za-z0-9](?:[A-Za-z0-9-]{0,37}[A-Za-z0-9])?', github_user):
        raise ValueError('github_user_invalid')
    quorum = shell_word(slug, SLUG, 'invalid_quorum')
    assigned, binding = assignment(slug, role, project_ref)
    # Every local check runs before the service issues anything.
    consent = authorizations(coordinator_role, standing_grants)
    connect = connect_line(assigned, coordinator_role, standing_grants)
    invite_id, quorum_id, expires_at, service, storage = issue_id_invitation(
        api, config, slug, 'invite_create_id', {'target_github_id': target}, request_id, assigned)
    invite = shell_word(invite_id, UUID_TEXT, 'invalid_response')
    binding = assigned_line(binding, storage)
    target_label = (f'@{github_user}, ' if github_user else '') + f'GitHub ID {target}'
    header = (f'Quorum invitation for {target_label}: quorum slug {json.dumps(slug)}, invite ID {invite_id}, '
              f'expires at {json.dumps(expires_at)}. ' + EXPIRY)
    github = ('GitHub is required and the account must match; sign in through GitHub via Supabase Auth, with no '
              'Supabase dashboard registration.')
    prompt = render(header, github, binding, bootstrap(),
                    f'2. Accept: accept-id {invite} --quorum {quorum} --origin and --publishable-key from this '
                    f'public service locator: {json.dumps(service)}. ' + RESUME_RULE, connect, consent)
    configured = render(header, github, binding, bootstrap(),
                        f'2. Accept: accept-id {invite} --quorum {quorum} without service flags, on my configured '
                        'machine profile and login; a missing or ambiguous profile is a blocker. ' + RESUME_RULE,
                        connect, consent)
    return {'invite_id': invite_id, 'quorum': slug, 'quorum_id': quorum_id, 'target_github_id': target,
            'target_github_user': github_user, 'expires_at': expires_at, 'service': service,
            'standing_grants': standing_grants, 'member_trust': True,
            **({'assigned': assigned, 'assignment_storage': storage} if assigned else {}),
            'session_prompt': prompt, 'configured_machine_prompt': configured}


def create_open_invitation(api, config, slug, request_id, *, coordinator_role='coordinator', standing_grants=True,
                           role=None, project_ref=None):
    quorum = shell_word(slug, SLUG, 'invalid_quorum')
    assigned, binding = assignment(slug, role, project_ref)
    # Every local check runs before the service issues anything.
    consent = authorizations(coordinator_role, standing_grants)
    connect = connect_line(assigned, coordinator_role, standing_grants)
    invite_id, quorum_id, expires_at, service, storage = issue_id_invitation(
        api, config, slug, 'invite_create_open', {}, request_id, assigned)
    invite = shell_word(invite_id, UUID_TEXT, 'invalid_response')
    binding = assigned_line(binding, storage)
    header = (f'Single-use open Quorum invitation: quorum slug {json.dumps(slug)}, invite ID {invite_id}, expires at '
              f'{json.dumps(expires_at)}. ' + EXPIRY)
    anonymous = ('No GitHub or browser: accept with a new anonymous identity in a fresh private state. Anyone '
                 'holding this text can use it until accepted; do not forward it.')
    prompt = render(header, anonymous, binding, bootstrap(),
                    f'2. Accept: accept-id {invite} --anonymous --quorum {quorum} --origin and --publishable-key '
                    f'from this public service locator: {json.dumps(service)}. ' + RESUME_RULE,
                    connect, consent)
    return {'invite_id': invite_id, 'quorum': slug, 'quorum_id': quorum_id, 'access': 'open',
            'expires_at': expires_at, 'service': service, 'standing_grants': standing_grants, 'member_trust': True,
            **({'assigned': assigned, 'assignment_storage': storage} if assigned else {}), 'session_prompt': prompt}


def accepted_assignment(accepted):
    """`{'assigned': {...}}` for a shape-checked stored binding, else {}; never other server fields."""
    value = accepted.get('assigned') if isinstance(accepted, dict) else None
    if (isinstance(value, dict) and set(value) == {'role', 'project_ref'}
            and (value['role'] is None or (isinstance(value['role'], str) and ROLE.fullmatch(value['role'])))
            and isinstance(value['project_ref'], str) and PROJECT_REF.fullmatch(value['project_ref'])):
        return {'assigned': {'role': value['role'], 'project_ref': value['project_ref']}}
    return {}


def _own_session():
    """This chat's own canonical session ID, or None when it exports none (membership-only, cloud)."""
    from quorum_session import current_session
    try: return current_session()
    except ValueError: return None


def accept_id_invitation(args, ledger):
    import uuid
    from quorum_live import api_for
    from quorum_profiles import resolve_profile, ensure_login, AnonymousCredentials
    from quorum_api import ServiceConfig
    if getattr(args, 'worker_context', None) is not None:
        from quorum_claude_worker import accept_worker
        return accept_worker(args, ledger, args.worker_context)
    invite_id = str(uuid.UUID(args.invite_id))
    if bool(args.origin) != bool(args.publishable_key): raise ValueError('service_arguments_required')
    explicit = ServiceConfig(args.origin, args.publishable_key) if args.origin else None
    if explicit is not None: public_service(explicit)
    cloud = getattr(args, 'client', None) == 'grok-bot'
    tool = getattr(args, 'transport', None) == 'tool-inbox'
    worker = None
    if tool or getattr(args, 'parent_thread_id', None) is not None:
        from quorum_native_inbox import check_arguments, prove_worker
        check_arguments(getattr(args, 'transport', None), args.parent_thread_id,
                        membership_only=args.membership_only, client=getattr(args, 'client', None))
        # The actual distinct worker, before any profile, login or invitation use.
        worker = prove_worker(args.parent_thread_id)
    conversation = None
    if cloud:
        from quorum_grok_cloud import CloudError, preflight
        # A typed client never silently degrades to membership-only acceptance.
        if args.membership_only: raise CloudError('cloud_client_conflict')
        # The actual parent chat and its private webhook target, before any profile, login or invitation use.
        conversation = preflight(ledger)
    quorum = getattr(args, 'quorum', None)
    if quorum is not None:
        # Re-accept versus resume (#130): a chat already bound in this quorum resumes that binding, before any
        # profile, login or invitation use, so the single-use invitation stays unused.
        if not isinstance(quorum, str) or not SLUG.fullmatch(quorum): raise ValueError('invalid_quorum')
        from quorum_resume import refuse_if_bound
        refuse_if_bound(ledger.root, quorum, sid=None if tool or cloud else _own_session(),
                        parent=args.parent_thread_id if tool else None, cloud=conversation)
    config, _ = resolve_profile(ledger.root, explicit, anonymous=args.anonymous)
    public_service(config)
    # Joining needs this chat's native listener; never consume the invitation without one.
    # An explicit membership-only request connects nothing, so it skips only this check.
    if not args.membership_only and not cloud and not tool:
        from quorum_session import client_preflight
        client_preflight()
    api, config, store = api_for(ledger)
    # A GitHub invitation needs the machine login, never this state's anonymous one.
    if isinstance(store, AnonymousCredentials) != args.anonymous: raise ValueError('anonymous_state_conflict')
    ensure_login(config, store, anonymous=args.anonymous)
    accepted = api.call('invite_accept_id', {'invite_id': invite_id}, 'accept-id:' + invite_id)
    # The service's stored binding for this membership, when it has the role-assignment extension.
    bound = accepted_assignment(accepted)
    if cloud:
        # Acceptance registers nothing; `cloud connect` binds this same parent explicitly.
        return {'quorum_id': accepted['quorum_id'], 'membership_id': accepted['membership_id'],
                'membership_only': True, 'native_preflight': 'not_applicable', 'client': 'grok-bot',
                'cloud_preflight': 'passed', **bound}
    if tool:
        # Acceptance registers nothing; the same worker's `session connect --transport tool-inbox` binds itself.
        return {'quorum_id': accepted['quorum_id'], 'membership_id': accepted['membership_id'],
                'membership_only': True, 'native_preflight': 'not_applicable', 'transport': 'tool-inbox',
                'worker_preflight': 'passed', 'session_ref': worker['session_ref'], **bound}
    return {'quorum_id': accepted['quorum_id'], 'membership_id': accepted['membership_id'],
            'membership_only': True, 'native_preflight': 'skipped' if args.membership_only else 'passed', **bound}
