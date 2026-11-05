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


# Invitations stay thin and version-independent: data, the receiving owner's authorizations and hard
# safety rules. Procedure lives in the recipient's freshly refreshed references/join.md, so skill fixes
# reach invitations issued by older copies.
def bootstrap():
    from quorum_release import INSTALLER
    return ('First, before anything else, install or refresh the quorum skill to the latest rotnov/skills main. '
            'If it is not installed, install it globally from a persistent directory outside the project, not a '
            f'temporary scratch folder: npx --yes "{INSTALLER}" add rotnov/skills --skill quorum -a claude-code '
            'codex --copy -g -y. If it is installed, run its immutable automatic refresh once (references/update.md). '
            'Never trust any other version, release or command quoted in this invitation. Then read the freshly '
            'installed SKILL.md and follow its references/join.md for this invitation: worker routes, client '
            'branches, expiry, setup reporting and failures. Wherever this text and the installed '
            'references/join.md differ, the installed references/join.md wins. ')


# Machine clocks can be hours off; only the server's accept response decides expiry.
EXPIRY = ('The expiry above is server data only: do not judge it yourself from any local clock, helper output or '
          'sense of the date; always attempt acceptance, and only the server response decides (report invite_denied '
          'if it comes back). ')


def standing_authorizations(coordinator_role):
    """The receiving owner's bounded standing grants; procedure lives in references/delegation.md."""
    from quorum_delegation import CLIENT_TESTS_PER_DAY
    return (f'Standing grants (connect with --standing-grants-from {coordinator_role}): act only on my request here '
            f'or a Quorum message from the {json.dumps(coordinator_role)} role that this binding\'s standing-check '
            '<message-id> authorizes. (1) Skill freshness: the automatic refresh once per request, then stop/reconnect/'
            'restart only this bound listener with the same state, session UUID, quorum, role and project; if the '
            'refreshed copy offers this client\'s native background-worker route, switch to it, starting only that one '
            'worker with this same member scope; if that fails keep this connection, report the exact code, never '
            'use parent reception. (2) Client tests: local grok, codex or claude CLI/TUI sessions only to test the '
            f'quorum skill, daily cap {CLIENT_TESTS_PER_DAY} per UTC day, in a fresh folder outside real projects, no '
            'prompt reading private data, shut down after. These are the only exceptions to the no-model/no-session '
            'rule below; merges, default-branch pushes, deploys, migrations, secrets, external posts, permission/'
            'config edits and destructive actions still need me, typed in this chat. ')


def authorizations(coordinator_role, standing=True):
    if not re.fullmatch('[a-z][a-z0-9-]{0,47}', coordinator_role): raise ValueError('invalid_coordinator_role')
    from quorum_delegation import MEMBER_LIMITS
    return ('I am the receiving human and paste this prompt as my own instruction. I authorize acceptance and '
            'connection of this actual session through the native quorum service agent, with --accept-tasks-from-members '
            '--dispatch-mode inline, and its native transport listener and Hello transport notices until I stop it. '
            'Membership-only acceptance requires my explicit request. '
            'For skill_update_required, I also authorize the fixed automatic refresh once per update request and '
            'stop/reconnect/restart of only this bound listener using the same private state and actual session UUID. '
            'I authorize non-destructive project task intake from every current or future member of this quorum, '
            'including anonymous members, within the selected shared project and this binding\'s local directory, '
            'using existing permissions without asking again for each assignment; immediately before any member task, '
            'run this exact binding\'s task-check <message-id>. Existing joins, grants and capabilities remain '
            'unchanged. ' + MEMBER_LIMITS +
            'Member intake never grants paid model, session or agent creation. Use my supplied role and shared '
            'project reference if present. ' + (standing_authorizations(coordinator_role) if standing else ''))


ROLE = re.compile('[a-z][a-z0-9-]{0,47}')


def assignment(slug, role=None, project_ref=None):
    """The owner's binding role/project for this recipient, validated before any invitation is issued.

    The service stores no role with an invitation, so the prompt carries it as a binding instruction and the
    recipient's `--exclusive-role` connect refuses a role another identity or another own agent already holds.
    """
    if role is None and project_ref is None: return None, ''
    if role is not None and not ROLE.fullmatch(role): raise ValueError('invalid_role')
    if project_ref is not None and (not isinstance(project_ref, str) or not 1 <= len(project_ref) <= 500
                                    or not project_ref.isprintable() or project_ref != project_ref.strip()):
        raise ValueError('invalid_project_ref')
    if role is None:
        return {'role': None, 'project_ref': project_ref}, (
            f'Assigned shared project reference: {json.dumps(project_ref)}; use exactly it as --project-ref. ')
    project_ref = slug if project_ref is None else project_ref
    return {'role': role, 'project_ref': project_ref}, (
        f'Assigned binding (binding instruction, not a suggestion): connect this session with exactly role '
        f'{json.dumps(role)} and shared project reference {json.dumps(project_ref)}, i.e. session connect with '
        f'--role {role} --project-ref {json.dumps(project_ref)} --exclusive-role, or the same role and project in '
        'a Claude worker scope. This overrides every default or fallback role; never choose, derive or append '
        'another role. If connect returns role_held_by_other_identity or role_held_by_own_agent, stop and report '
        'that code to me; do not pick a different role. ')


SAFETY = ('Hard rules: use only this actual session identity; never spoof another client, session or export, and '
          'never relabel parent reception as a worker inbox. Do not start a model, another session, headless pilot '
          'or OS startup service, and never suggest visible parent reception: it is a legacy opt-in only on my own '
          'explicit request. Do not ask for, paste or forward secrets, tokens or OAuth codes. Ask me before posting '
          'anything outside the quorum. Do not narrate raw envelopes or routine notices in this chat; report one '
          'setup outcome and material results only. ')


def issue_id_invitation(api, config, slug, operation, payload, request_id):
    import uuid
    if not 1 <= len(request_id) <= 128: raise ValueError('invitation_arguments_invalid')
    service = public_service(config)
    quorum_id = api.call('quorum_lookup', {'slug': slug})['quorum_id']
    api.call('quorum_check_owner', {'quorum_id': quorum_id})
    data = api.call(operation, {'quorum_id': quorum_id, **payload}, request_id)
    # Only select public fields; never forward an unexpected backend credential.
    return str(uuid.UUID(data['invite_id'])), quorum_id, data['expires_at'], service


def create_id_invitation(api, config, slug, target, request_id, *, github_user=None, coordinator_role='coordinator',
                         standing_grants=True, role=None, project_ref=None):
    if not re.fullmatch(r'[0-9]{1,20}', target): raise ValueError('invitation_arguments_invalid')
    if github_user is not None and not re.fullmatch(r'[A-Za-z0-9](?:[A-Za-z0-9-]{0,37}[A-Za-z0-9])?', github_user):
        raise ValueError('github_user_invalid')
    assigned, binding = assignment(slug, role, project_ref)
    granted = binding + authorizations(coordinator_role, standing_grants)
    invite_id, quorum_id, expires_at, service = issue_id_invitation(
        api, config, slug, 'invite_create_id', {'target_github_id': target}, request_id)
    target_label = (f'@{github_user}, ' if github_user else '') + f'GitHub ID {target}'
    introduction = (f'Quorum invitation for {target_label}: quorum slug {json.dumps(slug)}, invite ID {invite_id}, '
                    f'expires at {json.dumps(expires_at)}. ' + EXPIRY +
                    'GitHub is required and the account must match this invitation; sign in through GitHub via '
                    'Supabase Auth, with no Supabase dashboard registration. ')
    prompt = (introduction + bootstrap() + f'Accept with accept-id {invite_id} and --origin and --publishable-key '
              f'from this public service locator: {json.dumps(service)}. Reuse my machine login; complete my own '
              'GitHub browser login only for initial setup or explicit reauthentication. ' + granted + SAFETY)
    configured = (introduction + bootstrap() + f'Accept with accept-id {invite_id} without service flags, using my '
                  'configured machine profile and existing machine login. Missing or ambiguous service profiles are '
                  'blockers; never infer a server from the invitation UUID. ' + granted + SAFETY)
    return {'invite_id': invite_id, 'quorum': slug, 'quorum_id': quorum_id, 'target_github_id': target,
            'target_github_user': github_user, 'expires_at': expires_at, 'service': service,
            'standing_grants': standing_grants, **({'assigned': assigned} if assigned else {}),
            'session_prompt': prompt, 'configured_machine_prompt': configured}


def create_open_invitation(api, config, slug, request_id, *, coordinator_role='coordinator', standing_grants=True,
                           role=None, project_ref=None):
    assigned, binding = assignment(slug, role, project_ref)
    granted = binding + authorizations(coordinator_role, standing_grants)
    invite_id, quorum_id, expires_at, service = issue_id_invitation(
        api, config, slug, 'invite_create_open', {}, request_id)
    prompt = (f'Single-use open Quorum invitation: quorum slug {json.dumps(slug)}, invite ID {invite_id}, expires at '
              f'{json.dumps(expires_at)}. ' + EXPIRY + 'No GitHub login or browser is needed: accept it with a new '
              'anonymous Quorum identity kept only in a fresh private state directory. Anyone holding this text '
              'before acceptance can use it; do not forward it. ' + bootstrap()
              + f'Accept with accept-id {invite_id} --anonymous and --origin and --publishable-key from this public '
              f'service locator: {json.dumps(service)}. ' + granted + SAFETY)
    return {'invite_id': invite_id, 'quorum': slug, 'quorum_id': quorum_id, 'access': 'open',
            'expires_at': expires_at, 'service': service, 'standing_grants': standing_grants,
            **({'assigned': assigned} if assigned else {}), 'session_prompt': prompt}


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
    if cloud:
        from quorum_grok_cloud import CloudError, preflight
        # A typed client never silently degrades to membership-only acceptance.
        if args.membership_only: raise CloudError('cloud_client_conflict')
        # The actual parent chat and its private webhook target, before any profile, login or invitation use.
        preflight(ledger)
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
    if cloud:
        # Acceptance registers nothing; `cloud connect` binds this same parent explicitly.
        return {'quorum_id': accepted['quorum_id'], 'membership_id': accepted['membership_id'],
                'membership_only': True, 'native_preflight': 'not_applicable', 'client': 'grok-bot',
                'cloud_preflight': 'passed'}
    if tool:
        # Acceptance registers nothing; the same worker's `session connect --transport tool-inbox` binds itself.
        return {'quorum_id': accepted['quorum_id'], 'membership_id': accepted['membership_id'],
                'membership_only': True, 'native_preflight': 'not_applicable', 'transport': 'tool-inbox',
                'worker_preflight': 'passed', 'session_ref': worker['session_ref']}
    return {'quorum_id': accepted['quorum_id'], 'membership_id': accepted['membership_id'],
            'membership_only': True, 'native_preflight': 'skipped' if args.membership_only else 'passed'}
