"""Explicit owner-side pilot orchestration. No automatic install or startup."""
from pathlib import Path
from quorum_state import SessionBinding
from quorum_os import FileLock, open_private, protect


def bind_delivery(ledger, delivery):
    record = ledger.setting('pilot-agent')
    if record is None or delivery.get('recipient_agent_id') != record['agent_id']:
        raise ValueError('pilot_binding_denied')
    validate_pilot_profile(ledger, record)
    data = record['registration']
    binding = SessionBinding(record['quorum_id'], data['project_ref'], record['device_id'],
                             record['agent_id'], delivery['conversation_id'], data['engine'],
                             data['cwd'], data['session_ref'])
    try:
        saved = ledger.binding(binding.conversation_id)
    except ValueError:
        ledger.bind_session(binding)
    else:
        if saved != binding: raise ValueError('binding_conflict')
    return binding


def read_invitation(path):
    import json
    import os
    from quorum_auth import FileCredentials
    from quorum_api import ServiceConfig
    if os.name == 'nt':
        FileCredentials._check(Path(path).lstat())
        protect(path)
    fd = open_private(path, os.O_RDONLY | getattr(os, 'O_NONBLOCK', 0))
    with os.fdopen(fd, 'rb') as source:
        FileCredentials._check(os.fstat(source.fileno()))
        raw = source.read(16385)
    if len(raw) > 16384: raise ValueError('invitation_too_large')
    data = json.loads(raw)
    ServiceConfig(data['origin'], data['publishable_key'])
    if not isinstance(data.get('token'), str) or len(data['token']) != 64:
        raise ValueError('invalid_invitation')
    return data


LIVE_COMMANDS = {'connect', 'pilot-init', 'pilot-worker', 'ping', 'thread', 'mcp', 'invite-file', 'listen'}


def add_commands(commands):
    from quorum_listener import add_commands as add_listener_commands
    add_listener_commands(commands)
    invite = commands.add_parser('invite-file', help='Create an owner-only invitation file without printing its token')
    invite.add_argument('quorum')
    target = invite.add_mutually_exclusive_group(required=True)
    target.add_argument('--github-user')
    target.add_argument('--github-id')
    invite.add_argument('--request-id', required=True)
    connect = commands.add_parser('connect', help='Read a private invitation, log in and register a test agent')
    connect.add_argument('--invite-file', type=Path, required=True)
    for name in ('pilot-init', 'connect'):
        command = connect if name == 'connect' else commands.add_parser(name)
        if name != 'connect': command.add_argument('quorum')
        command.add_argument('--role', required=True)
        command.add_argument('--session-ref', required=True)
    worker = commands.add_parser('pilot-worker', help='Explicit bounded foreground Claude pilot')
    worker.add_argument('--toolchain', type=Path, required=True)
    worker.add_argument('--approve-subscription', action='store_true', required=True)
    worker.add_argument('--max-messages', type=int, choices=(1,), required=True)
    worker.add_argument('--timeout', type=int, choices=range(10, 61), default=60)
    worker.add_argument('--wait-seconds', type=int, choices=range(1, 1801), default=600)
    ping = commands.add_parser('ping')
    ping.add_argument('--to-role', required=True)
    ping.add_argument('--text', required=True)
    ping.add_argument('--request-id', required=True)
    ping.add_argument('--conversation-id')
    thread = commands.add_parser('thread')
    thread.add_argument('conversation_id')
    mcp = commands.add_parser('mcp')
    mcp.add_argument('--agent-id', required=True)


def api_for(ledger):
    import json
    from quorum_api import QuorumApi, ServiceConfig
    from quorum_auth import CredentialStore, token_provider
    path = ledger.root / 'config.json'
    if path.is_symlink(): raise ValueError('unsafe_config_file')
    config = ServiceConfig(**json.loads(path.read_text()))
    store = CredentialStore(root=ledger.root / 'credentials')
    return QuorumApi(config, token_provider(config, store)), config, store


def dispatch(args, ledger, register_agent):
    import hashlib
    import json
    import os
    import re
    import time
    import uuid
    from quorum_auth import login_pkce
    if args.command == 'listen':
        from quorum_listener import dispatch as listener_dispatch
        return listener_dispatch(args, ledger)
    if args.command == 'connect':
        invite = read_invitation(args.invite_file)
        config_path = ledger.root / 'config.json'
        public = {key: invite[key] for key in ('origin', 'publishable_key')}
        if config_path.exists():
            if config_path.is_symlink() or json.loads(config_path.read_text()) != public:
                raise ValueError('service_config_conflict')
        else:
            fd = open_private(config_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL)
            with os.fdopen(fd, 'w') as output: json.dump(public, output)
        api, config, store = api_for(ledger)
        if store.load(config.origin) is None: login_pkce(config, store)
        accepted = api.call('invite_accept', {'token': invite['token']},
                            'accept:' + hashlib.sha256(invite['token'].encode()).hexdigest())
        if accepted['quorum_id'] != invite['quorum_id']: raise ValueError('quorum_mismatch')
        args.quorum = invite['quorum']
    else:
        api, config, store = api_for(ledger)
    if args.command == 'invite-file':
        from quorum_invitations import create_invitation, github_id
        target = args.github_id or github_id(args.github_user)
        return create_invitation(api, config, ledger, args.quorum, target, args.request_id)
    if args.command in ('pilot-init', 'connect'):
        if not re.fullmatch('[a-z][a-z0-9-]{0,47}', args.role): raise ValueError('invalid_role')
        if not args.session_ref or len(args.session_ref) > 128: raise ValueError('invalid_session_ref')
        cwd = ledger.root / 'scratch' / args.role
        cwd.mkdir(mode=0o700, parents=True, exist_ok=True)
        if cwd.is_symlink() or not cwd.resolve().is_relative_to(ledger.root.resolve()):
            raise ValueError('invalid_scratch_directory')
        data = {'engine': 'claude', 'project_ref': 'quorum-pilot/messaging',
                'session_ref': args.session_ref, 'cwd': str(cwd)}
        device_key = 'pilot-device:' + hashlib.sha256(json.dumps(
            [args.quorum, args.role, args.session_ref]).encode()).hexdigest()
        result = register_agent(api, ledger, args.quorum, args.role, data, device_key=device_key)
        ledger.setting('pilot-agent', {**result, 'registration': data,
                                      'pilot_device_key': device_key, 'pilot_role': args.role})
        return result
    if args.command == 'mcp':
        from quorum_mcp import McpTools, serve
        record = ledger.setting('registration:' + args.agent_id)
        if record is None: raise ValueError('agent_not_configured')
        api.call('agent_status', {'agent_id': args.agent_id})
        capability = ledger.issue_capability(args.agent_id)
        def rpc(operation, payload):
            request_id = payload.pop('request_id', None)
            if operation == 'message_send' and request_id is None:
                raise ValueError('request_id_required')
            return api.call(operation, payload, request_id)
        tools = McpTools(ledger.authorize, rpc, scope=lambda _: {
            'quorum_id': record['quorum_id'], 'project_ref': record['registration']['project_ref']})
        try: serve(tools, capability)
        finally: ledger.revoke(args.agent_id)
        return None
    record = ledger.setting('pilot-agent')
    if record is None: raise ValueError('pilot_agent_required')
    agent_id, quorum_id = record['agent_id'], record['quorum_id']
    if args.command == 'ping':
        recipient = api.call('resolve_role', {'quorum_id': quorum_id, 'role': args.to_role,
                                             'project_ref': record['registration']['project_ref']})
        conversation = args.conversation_id or str(uuid.uuid5(uuid.NAMESPACE_URL, agent_id + ':' + args.request_id))
        message = api.call('message_send', {'quorum_id': quorum_id, 'sender_agent_id': agent_id,
                           'recipient_agent_id': recipient['agent_id'], 'conversation_id': conversation,
                           'body': args.text, 'reply_to': None}, args.request_id)
        return {'conversation_id': conversation, **message}
    if args.command == 'thread':
        return api.call('conversation_read', {'quorum_id': quorum_id, 'agent_id': agent_id,
                        'conversation_id': args.conversation_id, 'after_sequence': 0})
    if args.command == 'pilot-worker':
        worker = prepare_worker(ledger, api, args.toolchain, args.timeout, args.approve_subscription)
        # Hold the device lock across idle polling as well as model execution.
        # Worker.run_once acquires this same lock, so use a distinct supervisor lock.
        supervisor = LocalSupervisorLock(ledger.root)
        with supervisor:
            worker.reconcile()
            deadline = time.monotonic() + args.wait_seconds
            while time.monotonic() < deadline:
                result = worker.run_once()
                if result != 'idle': return {'worker': result, 'messages_budget': 1}
                time.sleep(min(10, max(0, deadline - time.monotonic())))
        return {'worker': 'idle_timeout', 'model_turns': 0}
    raise ValueError('unknown_operation')


def prepare_worker(ledger, api, toolchain, timeout, approved):
    import shutil
    from quorum_acp import ClaudePilotRunner, ExecutionPolicy
    from quorum_worker import Worker
    record = ledger.setting('pilot-agent')
    if record is None: raise ValueError('pilot_agent_required')
    policy = ExecutionPolicy(1, timeout, 'subscription', True, approved)
    policy.validate()
    validate_pilot_profile(ledger, record)
    api.call('agent_status', {'agent_id': record['agent_id']})
    cwd = Path(record['registration']['cwd'])
    scope = {key: record[key] for key in ('quorum_id', 'device_id', 'agent_id')}
    scope.update(project_ref=record['registration']['project_ref'], cwd=str(cwd))
    runner = ClaudePilotRunner(toolchain, shutil.which('node'), shutil.which('claude'), binding_scope=scope)
    runner.preflight(str(cwd))
    return Worker(api, ledger, runner, record['device_id'], policy, bind_delivery)


class LocalSupervisorLock(FileLock):
    def __init__(self, root): super().__init__(root / 'supervisor.lock')


def supervisor_running(root):
    path = root / 'supervisor.lock'
    if not path.exists(): return False
    try:
        with FileLock(path): return False
    except ValueError as error:
        if str(error) == 'worker_already_running': return True
        raise


def validate_pilot_profile(ledger, record):
    import json
    import re
    from quorum_os import reject_link
    data = record['registration']
    device = ledger.setting(record.get('pilot_device_key', 'missing-pilot-device'))
    role = record.get('pilot_role', '')
    if (not device or device['device_id'] != record['device_id']
            or not re.fullmatch('[a-z][a-z0-9-]{0,47}', role)
            or data['engine'] != 'claude' or data['project_ref'] != 'quorum-pilot/messaging'):
        raise ValueError('dedicated_pilot_profile_required')
    expected = ledger.root / 'scratch' / role
    reject_link(expected.parent)
    reject_link(expected)
    if (Path(data['cwd']).resolve() != expected.resolve()
            or not expected.resolve().is_relative_to(ledger.root.resolve())):
        raise ValueError('scratch_directory_required')
    for row in ledger.db.execute("select data from settings where key like 'registration:%'"):
        other = json.loads(row[0])
        if other['device_id'] == record['device_id'] and other['agent_id'] != record['agent_id']:
            raise ValueError('dedicated_pilot_profile_required')
