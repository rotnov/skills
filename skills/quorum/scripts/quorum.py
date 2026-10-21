#!/usr/bin/env python3
"""Portable Quorum entry point with explicit opt-in Claude execution."""
import argparse
import json
import os
from pathlib import Path
import sys
import uuid
import hashlib
import subprocess

sys.dont_write_bytecode = True
from quorum_api import QuorumApi, ServiceConfig, QuorumError
from quorum_auth import CredentialStore, login_pkce, token_provider
from quorum_state import LocalLedger
from quorum_os import open_private


def register_agent(api, ledger, slug, role, data, *, device_key="device"):
    required = {'engine', 'project_ref', 'session_ref', 'cwd'}
    if set(data) != required or data['engine'] not in ('claude', 'codex') or not Path(data['cwd']).is_absolute():
        raise ValueError('invalid_registration')
    quorum = api.call('quorum_lookup', {'slug': slug})['quorum_id']
    binding_key = [quorum, role, data] + ([] if device_key == 'device' else [device_key])
    identity = hashlib.sha256(json.dumps(binding_key, sort_keys=True).encode()).hexdigest()
    saved = ledger.setting('agent:' + identity)
    if saved:
        api.call('agent_status', {'agent_id': saved['agent_id']})
        ledger.setting('registration:' + saved['agent_id'], {**saved, 'registration': data})
        return saved
    device = ledger.setting(device_key)
    if not device:
        device = api.call('device_register', {'label': 'quorum-local'},
                          'device:' + hashlib.sha256((str(ledger.root) + ('' if device_key == 'device' else ':' + device_key)).encode()).hexdigest())
        ledger.setting(device_key, device)
    result = api.call('agent_join', {'quorum_id': quorum, 'device_id': device['device_id'],
                                     'role': role, **{key: data[key] for key in required - {'cwd'}}},
                      'join:' + identity)
    value = {**result, 'quorum_id': quorum, 'device_id': device['device_id'], 'worker': 'offline'}
    ledger.setting('agent:' + identity, value)
    ledger.setting('registration:' + value['agent_id'], {**value, 'registration': data})
    return value


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--state', type=Path, default=Path.home() / '.local/share/quorum')
    commands = parser.add_subparsers(dest='command', required=True)
    config = commands.add_parser('configure')
    config.add_argument('--origin', required=True)
    config.add_argument('--publishable-key', required=True)
    for name in ('status', 'login', 'logout', 'worker', 'doctor'):
        commands.add_parser(name)
    for name in ('create', 'invite', 'accept', 'join', 'leave', 'send', 'context', 'work'):
        sub = commands.add_parser(name)
        sub.add_argument('quorum', nargs='?')
        sub.add_argument('--role')
        sub.add_argument('--json-stdin', action='store_true', help='Read nonsecret operation arguments from stdin')
        sub.add_argument('--request-id', help='Reuse the same ID after an uncertain response')
    from quorum_live import add_commands
    add_commands(commands)
    args = parser.parse_args(argv)
    try:
        ledger = LocalLedger(args.state)
        config_path = ledger.root / 'config.json'
        from quorum_live import dispatch, LIVE_COMMANDS
        if args.command in LIVE_COMMANDS:
            value = dispatch(args, ledger, register_agent)
            if args.command == 'mcp': return 0
            print(json.dumps({'ok': True, 'code': 'ok', 'data': value}))
            return 0
        if args.command == 'configure':
            config = ServiceConfig(args.origin, args.publishable_key)
            if config_path.is_symlink(): raise ValueError('unsafe_config_file')
            fd = open_private(config_path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC)
            with os.fdopen(fd, 'w') as handle:
                json.dump({'origin': config.origin, 'publishable_key': config.publishable_key}, handle)
            value = {'configured': True}
        elif args.command == 'status':
            from quorum_live import supervisor_running
            running = supervisor_running(ledger.root)
            value = {'worker': 'running' if running else 'offline',
                     'live_agents': 'explicit_claude_pilot' if running else 'disabled',
                     'configured': config_path.exists()}
        elif args.command == 'worker':
            raise QuorumError('live_agents_disabled')
        elif args.command == 'doctor':
            from quorum_acp import validate_manifest
            manifest = json.loads((Path(__file__).resolve().parents[1] / 'assets/engines.lock.json').read_text())
            try:
                validate_manifest(manifest)
            except ValueError:
                raise QuorumError('engine_acceptance_pending') from None
            raise QuorumError('live_agents_disabled')
        else:
            if not config_path.is_file() or config_path.is_symlink():
                raise QuorumError('service_not_configured')
            config = ServiceConfig(**json.loads(config_path.read_text()))
            store = CredentialStore(root=ledger.root / 'credentials')
            if args.command == 'login': value = login_pkce(config, store)
            elif args.command == 'logout':
                store.delete(config.origin)
                ledger.db.execute('delete from capabilities')
                ledger.db.commit()
                value = {'logged_out': True}
            else:
                if not args.json_stdin:
                    raise ValueError('json_stdin_required')
                raw = sys.stdin.buffer.read(131073)
                if len(raw) > 131072: raise ValueError('request_too_large')
                data = json.loads(raw)
                if not isinstance(data, dict): raise ValueError('invalid_arguments')
                operations = {'create': 'quorum_create', 'invite': 'invite_create', 'accept': 'invite_accept',
                              'join': 'agent_join', 'leave': 'agent_leave', 'send': 'message_send',
                              'context': 'context_read', 'work': 'work_register'}
                api = QuorumApi(config, token_provider(config, store))
                if args.command == 'join':
                    value = register_agent(api, ledger, args.quorum, args.role, data)
                else:
                    if args.command == 'create': data.setdefault('slug', args.quorum)
                    if args.role: data.setdefault('role', args.role)
                    request_id = args.request_id or str(uuid.uuid4())
                    value = api.call(operations[args.command], data,
                                     None if args.command == 'context' else request_id)
        print(json.dumps({'ok': True, 'code': 'ok', 'data': value}))
        return 0
    except QuorumError as error:
        codes = {'auth_required': 3, 'service_not_configured': 4, 'live_agents_disabled': 4,
                 'engine_acceptance_pending': 4, 'service_unavailable': 5, 'service_error': 5,
                 'response_too_large': 5, 'invalid_response': 5, 'listener_start_failed': 5}
        code = str(error) if str(error) in codes else 'operation_failed'
        print(json.dumps({'ok': False, 'code': code, 'data': {}}))
        return codes.get(code, 3)
    except (ValueError, OSError, KeyError, TypeError, subprocess.SubprocessError):
        # Never serialize exception text: it may contain remote bodies or secrets.
        print(json.dumps({'ok': False, 'code': 'operation_failed', 'data': {}}))
        return 3


if __name__ == '__main__':
    raise SystemExit(main())
