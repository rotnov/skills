#!/usr/bin/env python3
"""Portable Quorum entry point with explicit opt-in Claude execution."""
import argparse
import json
from pathlib import Path
import sys
import uuid
import hashlib
import subprocess

sys.dont_write_bytecode = True
from quorum_api import QuorumApi, ServiceConfig, QuorumError
from quorum_auth import token_provider
from quorum_state import LocalLedger
from quorum_grok_leader import GrokBindingError
from quorum_session import UnsupportedClient
from quorum_grok_cloud import CloudError
from quorum_native_inbox import NativeInboxError


def register_agent(api, ledger, slug, role, data, *, device_key="device", expected_quorum_id=None):
    required = {'engine', 'project_ref', 'session_ref', 'cwd'}
    if set(data) != required or data['engine'] not in ('claude', 'codex', 'grok') or not Path(data['cwd']).is_absolute():
        raise ValueError('invalid_registration')
    from quorum_release import ReleaseGuard
    # Registration is not permission to stop a different binding's listener.
    ReleaseGuard(api, ledger, stop_on_mismatch=False).check()
    quorum = api.call('quorum_lookup', {'slug': slug})['quorum_id']
    # A pinned worker target is compared before any device or agent side effect.
    if expected_quorum_id is not None and quorum != expected_quorum_id:
        raise NativeInboxError('native_worker_binding_denied', 'target_mismatch')
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
    for name in ('status', 'login', 'logout', 'worker', 'doctor', 'version'):
        commands.add_parser(name)
    for name in ('create', 'invite', 'accept', 'join', 'leave', 'send', 'context', 'work'):
        sub = commands.add_parser(name)
        sub.add_argument('quorum', nargs='?')
        sub.add_argument('--role')
        sub.add_argument('--json-stdin', action='store_true', help='Read nonsecret operation arguments from stdin')
        sub.add_argument('--request-id', help='Reuse the same ID after an uncertain response')
        if name == 'create':
            sub.add_argument('--access', choices=('github', 'open'), default='github',
                             help='open: also allow single-use 15-minute invitations without GitHub')
            sub.add_argument('--worker-capability', type=Path,
                             help='Generated Claude worker capability file; slug, access and request ID come from its scope')
    from quorum_live import add_commands
    add_commands(commands)
    args = parser.parse_args(argv)
    ledger = None
    try:
        if args.command == 'version':
            from quorum_release import installed_release
            print(json.dumps({'ok':True, 'code':'ok', 'data':installed_release()}))
            return 0
        if getattr(args, 'worker_capability', None) is not None or args.command == 'session':
            import quorum_claude_worker as claude_worker
            # A Claude worker route validates its capability and arguments before the root ledger exists.
            if claude_worker.selected(args):
                value = claude_worker.manage(args)
                if value is not None:
                    print(json.dumps({'ok': True, 'code': 'ok', 'data': value}))
                    return 0
                args.worker_context = claude_worker.preflight(args)
                # Capability metadata and stop use the verified slot only, never the root ledger.
                if args.command == 'session' and args.action in ('status', 'stop'):
                    value = claude_worker.session_dispatch(args, None, register_agent, args.worker_context)
                    print(json.dumps({'ok': True, 'code': 'ok', 'data': value}))
                    return 0
        if getattr(args, 'worker_context', None) is not None:
            from quorum_claude_worker import root_ledger
            ledger = root_ledger(args.state)
        else: ledger = LocalLedger(args.state)
        config_path = ledger.root / 'config.json'
        from quorum_live import dispatch, LIVE_COMMANDS
        if args.command in LIVE_COMMANDS:
            value = dispatch(args, ledger, register_agent)
            if args.command == 'mcp': return 0
            print(json.dumps({'ok': True, 'code': 'ok', 'data': value}))
            return 0
        if args.command == 'configure':
            from quorum_profiles import resolve_profile
            resolve_profile(ledger.root, ServiceConfig(args.origin, args.publishable_key))
            value = {'configured': True}
        elif args.command == 'status':
            from quorum_live import supervisor_running
            running = supervisor_running(ledger.root)
            value = {'worker': 'running' if running else 'offline',
                     'live_agents': 'explicit_claude_pilot' if running else 'disabled',
                     'configured': config_path.exists()}
        elif args.command == 'worker':
            raise QuorumError('live_agents_disabled')
        elif args.command == 'create' and getattr(args, 'worker_context', None) is not None:
            from quorum_claude_worker import create_worker
            from quorum_profiles import resolve_profile
            value = create_worker(args, args.worker_context, resolve_profile,
                                  lambda config, store: QuorumApi(config, token_provider(config, store)))
        elif args.command == 'doctor':
            from quorum_acp import validate_manifest
            manifest = json.loads((Path(__file__).resolve().parents[1] / 'assets/engines.lock.json').read_text())
            try:
                validate_manifest(manifest)
            except ValueError:
                raise QuorumError('engine_acceptance_pending') from None
            raise QuorumError('live_agents_disabled')
        else:
            from quorum_profiles import resolve_profile, ensure_login, auth_lock
            # Only explicit login may refresh an older state's own token family.
            config, store = resolve_profile(ledger.root, refresh_legacy=args.command == 'login')
            if args.command == 'login': value = ensure_login(config, store, explicit=True)
            elif args.command == 'logout':
                with auth_lock(store): store.delete(config.origin)
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
                    if args.command == 'create' and args.access == 'open':
                        value.update(api.call('quorum_set_access', {'quorum_id': value['quorum_id'], 'access': 'open'},
                                              'access:' + value['quorum_id']))
        print(json.dumps({'ok': True, 'code': 'ok', 'data': value}))
        return 0
    except QuorumError as error:
        codes = {'skill_update_required': 4, 'release_manifest_invalid': 4, 'auth_required': 3, 'service_not_configured': 4, 'live_agents_disabled': 4,
                 'engine_acceptance_pending': 4, 'service_profile_ambiguous': 4, 'machine_profile_unavailable': 4,
                 'auth_profile_conflict': 3, 'service_config_conflict': 4, 'service_unavailable': 5, 'service_error': 5,
                 'response_too_large': 5, 'invalid_response': 5, 'listener_start_failed': 5, 'listener_stop_failed': 5,
                 'anonymous_login_unavailable': 4, 'open_invite_required': 3}
        code = str(error) if str(error) in codes else 'operation_failed'
        from quorum_release import ReleaseMismatch
        data = {'release':error.diagnostic} if isinstance(error, ReleaseMismatch) else {}
        print(json.dumps({'ok': False, 'code': code, 'data': data}))
        return codes.get(code, 3)
    except GrokBindingError as error:
        print(json.dumps({'ok': False, 'code': 'grok_transport_unavailable', 'data': error.diagnostic()}))
        return 3
    except UnsupportedClient as error:
        print(json.dumps({'ok': False, 'code': 'unsupported_client', 'data': error.diagnostic()}))
        return 3
    except CloudError as error:
        # Fixed cloud codes and reasons only; never a webhook target or peer text.
        print(json.dumps({'ok': False, 'code': error.code, 'data': error.diagnostic()}))
        return 3
    except NativeInboxError as error:
        # Fixed worker codes and reasons only; never a rollout path, transcript or peer text.
        print(json.dumps({'ok': False, 'code': error.code, 'data': error.diagnostic()}))
        return 3
    except (ValueError, OSError, KeyError, TypeError, subprocess.SubprocessError) as error:
        # Allow only fixed local codes; arbitrary exception text may contain secrets.
        safe = {'machine_profile_unavailable', 'service_profile_ambiguous', 'auth_profile_conflict',
                'service_config_conflict', 'github_identity_required', 'auth_lock_timeout',
                'browser_unavailable', 'login_timeout', 'anonymous_state_conflict'}
        if isinstance(error, ValueError):
            safe.update({'grok_transport_unavailable', 'session_binding_denied', 'coordinator_changed',
                         'claude_pipe_unavailable', 'listener_stop_pending', 'session_delivery_unknown',
                         'private_acl_unavailable', 'unsafe_link', 'browser_unavailable', 'login_timeout',
                         'worker_already_running'})
        code = str(error) if str(error) in safe else 'operation_failed'
        data = error.diagnostic() if code == 'auth_profile_conflict' and hasattr(error, 'diagnostic') else {}
        print(json.dumps({'ok': False, 'code': code, 'data': data}))
        return 3
    finally:
        # main owns this connection; dispatchers finish before its lifetime ends.
        if ledger is not None:
            ledger.db.close()


if __name__ == '__main__':
    raise SystemExit(main())
