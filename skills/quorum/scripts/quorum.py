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
from quorum_api import QuorumApi, ServiceConfig, QuorumError, SERVER_ERRORS, rpc_missing, safe_code
from quorum_auth import token_provider
from quorum_state import LocalLedger, default_state_root
from quorum_grok_leader import GrokBindingError, GrokBuildChanged, GrokRefused
from quorum_session import (AmbiguousSession, BindingError, MessageUnavailable, ReconcileEvidenceRequired, RecipientUnresolved, RoleHeld,
                            UnsupportedClient)
from quorum_grok_cloud import CloudError
from quorum_native_inbox import NativeInboxError
from quorum_resume import BindingExists
from quorum_debug import DebugError
from quorum_knowledge import KnowledgeError


def register_agent(api, ledger, slug, role, data, *, device_key="device", expected_quorum_id=None, supersede=False,
                   keep_agent_id=None):
    """Register (or reuse) this binding's agent.

    `supersede` asks the service to retire this identity's own other holders of the role in the same transaction;
    an older service without that argument raises its missing-RPC error so the caller can choose a fallback.
    `keep_agent_id` names one own holder that may coexist (an adopting worker's parent); an older service has no
    role exclusivity, so the plain join is retried there with the same request ID.
    """
    required = {'engine', 'project_ref', 'session_ref', 'cwd'}
    if set(data) != required or data['engine'] not in ('claude', 'codex', 'grok') or not Path(data['cwd']).is_absolute():
        raise ValueError('invalid_registration')
    from quorum_release import ReleaseGuard
    # Registration is not permission to stop a different binding's listener.
    ReleaseGuard(api, ledger, stop_on_mismatch=False).check()
    quorum = api.call('quorum_lookup', {'slug': slug})['quorum_id']
    # A pinned worker target is compared before any device or agent side effect.
    if expected_quorum_id is not None and quorum != expected_quorum_id:
        from quorum_claude_worker import ClaudeWorkerError
        raise ClaudeWorkerError('native_worker_binding_denied', 'target_mismatch')
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
    payload = {'quorum_id': quorum, 'device_id': device['device_id'], 'role': role,
               **{key: data[key] for key in required - {'cwd'}}}
    extras = {**({'supersede': True} if supersede else {}), **({'keep_agent_id': keep_agent_id} if keep_agent_id else {})}
    try: result = api.call('agent_join', {**payload, **extras}, 'join:' + identity)
    except QuorumError as error:
        if not (extras and not supersede and rpc_missing(error)): raise
        result = api.call('agent_join', payload, 'join:' + identity)
    # Agents this join retired are reported once; they are not part of the stored binding.
    superseded = result.pop('superseded', None) if isinstance(result, dict) else None
    value = {**result, 'quorum_id': quorum, 'device_id': device['device_id'], 'worker': 'offline'}
    ledger.setting('agent:' + identity, value)
    ledger.setting('registration:' + value['agent_id'], {**value, 'registration': data})
    return {**value, 'superseded': superseded} if superseded else value


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--state', type=Path, default=default_state_root())
    commands = parser.add_subparsers(dest='command', required=True)
    config = commands.add_parser('configure')
    config.add_argument('--origin', required=True)
    config.add_argument('--publishable-key', required=True)
    for name in ('status', 'login', 'logout', 'worker', 'version'):
        commands.add_parser(name)
    for name in ('create', 'invite', 'accept', 'join', 'leave', 'send', 'context', 'work'):
        sub = commands.add_parser(name)
        sub.add_argument('quorum', nargs='?')
        sub.add_argument('--role')
        sub.add_argument('--json-stdin', action='store_true', help='Read nonsecret operation arguments from stdin')
        sub.add_argument('--request-id', help='Reuse the same ID after an uncertain response')
        if name == 'create':
            sub.add_argument('--access', choices=('github', 'open'), default='github',
                             help='open: also allow single-use invitations without GitHub (server-set expiry)')
            sub.add_argument('--worker-capability', type=Path,
                             help='Generated Claude worker capability file; slug, access and request ID come from its scope')
    capabilities = commands.add_parser('capabilities', help='Offline route capability report; starts nothing')
    from quorum_capabilities import ROUTES, REQUIREMENTS
    capabilities.add_argument('--route', choices=ROUTES, help='Default: the current client\'s quiet worker route')
    capabilities.add_argument('--require', choices=tuple(REQUIREMENTS),
                              help='Fail with background_execution_unsupported unless the route supports it')
    doctor = commands.add_parser('doctor', help='Check this participant\'s actual route; the ACP gate is informational')
    from quorum_doctor import DOCTOR_ROUTES
    doctor.add_argument('--route', choices=DOCTOR_ROUTES,
                        help='Default: the current client\'s quiet worker route; acp-worker checks the gated default worker')
    from quorum_live import add_commands
    add_commands(commands)
    from quorum_updates import add_commands as add_update_commands
    add_update_commands(commands)
    from quorum_knowledge import add_commands as add_knowledge_commands
    add_knowledge_commands(commands)
    args = parser.parse_args(argv)
    ledger = None
    try:
        # A quiet setup request must never fall through to visible parent reception.
        # This is only a route constraint; worker provenance/capabilities are still checked below.
        if getattr(args, 'require_worker_inbox', False) and getattr(args, 'transport', None) != 'tool-inbox':
            raise NativeInboxError('native_worker_required', 'parent_route_visible')
        if args.command == 'capabilities':
            # Offline and static: no state directory, login, network or process.
            from quorum_capabilities import CapabilityUnsupported, check
            try: value = check(args.route, args.require)
            except CapabilityUnsupported as error:
                print(json.dumps({'ok': False, 'code': error.code, 'data': error.data}))
                return 4
            print(json.dumps({'ok': True, 'code': 'ok', 'data': value}))
            return 0
        if args.command == 'version':
            from quorum_release import installed_release
            print(json.dumps({'ok':True, 'code':'ok', 'data':installed_release()}))
            return 0
        if args.command == 'doctor':
            # The participant's actual route decides health; the default ACP worker gate stays reported.
            # Before the ledger exists: doctor reads state read-only and offline, and never creates it.
            from quorum_doctor import diagnose
            ok, code, exit_code, value = diagnose(args.state.expanduser().absolute(), args.route)
            print(json.dumps({'ok': ok, 'code': code, 'data': value}))
            return exit_code
        if getattr(args, 'worker_capability', None) is not None or args.command == 'session':
            import quorum_claude_worker as claude_worker
            # A Claude worker route validates its capability and arguments before the root ledger exists.
            if claude_worker.selected(args):
                value = claude_worker.manage(args, register_agent)
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
        if args.command == 'update':
            from quorum_updates import dispatch as update_dispatch
            value = update_dispatch(args, ledger)
            print(json.dumps({'ok':True, 'code':'ok', 'data':value}))
            return 0
        from quorum_live import dispatch, LIVE_COMMANDS
        if args.command == 'knowledge':
            from quorum_knowledge import dispatch as knowledge_dispatch
            value = knowledge_dispatch(args, ledger)
            print(json.dumps({'ok': True, 'code': 'ok', 'data': value}))
            return 0
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
                    if args.command == 'leave':
                        # A confirmed retirement can release an adopting Claude worker's claim.
                        from quorum_claude_worker import record_retired
                        record_retired(ledger, data.get('agent_id'))
                        # Leaving this chat's own bound agent also retires its local binding, so the next
                        # connect registers anew instead of reusing or conflicting with the revoked agent.
                        # A local failure here never hides the confirmed leave: connect rechecks this marker.
                        from quorum_session import retire_after_leave
                        try: local = retire_after_leave(ledger.root, data.get('agent_id'))
                        except (ValueError, OSError): local = None
                        if local == 'retired' and isinstance(value, dict): value = {**value, 'local_binding': 'retired'}
                    if args.command == 'create' and args.access == 'open':
                        value.update(api.call('quorum_set_access', {'quorum_id': value['quorum_id'], 'access': 'open'},
                                              'access:' + value['quorum_id']))
        print(json.dumps({'ok': True, 'code': 'ok', 'data': value}))
        return 0
    except KnowledgeError as error:
        print(json.dumps({'ok': False, 'code': error.code, 'data': {}}))
        return 3
    except QuorumError as error:
        codes = {'release_manifest_invalid': 4, 'service_not_configured': 4, 'live_agents_disabled': 4,
                 'engine_acceptance_pending': 4, 'service_profile_ambiguous': 4, 'machine_profile_unavailable': 4,
                 'auth_profile_conflict': 3, 'service_config_conflict': 4, 'service_unavailable': 5, 'service_error': 5,
                 'response_too_large': 5, 'invalid_response': 5, 'listener_start_failed': 5, 'listener_stop_failed': 5, 'listener_not_ready': 5,
                 'listener_stop_pending': 5,
                 'anonymous_login_unavailable': 4, 'open_invite_required': 3, 'owner_required': 3, 'agent_denied': 3,
                 'owner_retire_unavailable': 4, 'coordination_unavailable': 4, 'update_target_changed': 3}
        codes.update({'update_extension_unavailable':4, 'runtime_source_unavailable':4})
        codes.update({code: exit_code for _, code, exit_code in SERVER_ERRORS.values()})
        code = str(error) if str(error) in codes else 'operation_failed'
        from quorum_listener import ListenerPending
        from quorum_release import ReleaseMismatch
        from quorum_updates import UpdateFailure
        data = {'release':error.diagnostic} if isinstance(error, ReleaseMismatch) else {}
        if isinstance(error, UpdateFailure): data = {'update':error.diagnostic}
        if isinstance(error, ListenerPending): data = error.diagnostic
        print(json.dumps({'ok': False, 'code': code, 'data': data}))
        return codes.get(code, 3)
    except GrokBindingError as error:
        print(json.dumps({'ok': False, 'code': 'grok_transport_unavailable', 'data': error.diagnostic()}))
        return 3
    except GrokRefused as error:
        # A fixed code, reason and next step; never a path, session ID or leader text.
        print(json.dumps({'ok': False, 'code': error.code, 'data': error.diagnostic()}))
        return 3
    except GrokBuildChanged as error:
        # Two fixed-format build versions and a relaunch step; never a path or process argument.
        print(json.dumps({'ok': False, 'code': error.code, 'data': error.diagnostic()}))
        return 3
    except UnsupportedClient as error:
        print(json.dumps({'ok': False, 'code': 'unsupported_client', 'data': error.diagnostic()}))
        return 3
    except AmbiguousSession as error:
        # Only the fixed names of the detected client variables; never their values.
        print(json.dumps({'ok': False, 'code': error.code, 'data': error.diagnostic()}))
        return 3
    except BindingError as error:
        # This chat's own role, quorum and agent with fixed guidance; never a path, token or server body.
        print(json.dumps({'ok': False, 'code': error.code, 'data': error.diagnostic()}))
        return 3
    except ReconcileEvidenceRequired as error:
        # Fixed guidance only; nothing was changed or acknowledged.
        print(json.dumps({'ok': False, 'code': error.code, 'data': error.diagnostic()}))
        return 3
    except MessageUnavailable as error:
        # A fixed reason and fixed guidance only; never a body or a server message.
        print(json.dumps({'ok': False, 'code': error.code, 'data': error.diagnostic()}))
        return 5
    except RoleHeld as error:
        # The caller's own role (and own other agent ID) with fixed guidance; never another identity's agent.
        print(json.dumps({'ok': False, 'code': error.code, 'data': error.diagnostic()}))
        return 3
    except RecipientUnresolved as error:
        # The caller's own role and fixed guidance only; never agent IDs or a server body.
        print(json.dumps({'ok': False, 'code': error.code, 'data': error.diagnostic()}))
        return 3
    except CloudError as error:
        # Fixed cloud codes and reasons only; never a webhook target or peer text.
        print(json.dumps({'ok': False, 'code': error.code, 'data': error.diagnostic()}))
        return 3
    except BindingExists as error:
        # This chat's own quorum, role, IDs and a fixed resume command; the invitation was not used.
        print(json.dumps({'ok': False, 'code': error.code, 'data': error.diagnostic()}))
        return 3
    except NativeInboxError as error:
        # Fixed worker codes and reasons only; never a rollout path, transcript or peer text.
        print(json.dumps({'ok': False, 'code': error.code, 'data': error.diagnostic()}))
        return 3
    except DebugError as error:
        # Fixed debug codes only; never a path, transcript, token, or peer body.
        print(json.dumps({'ok': False, 'code': error.code, 'data': error.data}))
        return 3
    except (ValueError, OSError, KeyError, TypeError, subprocess.SubprocessError) as error:
        # Allow only fixed codes; arbitrary exception text may contain secrets.
        code = safe_code(error) or 'operation_failed'
        data = error.diagnostic() if code == 'auth_profile_conflict' and hasattr(error, 'diagnostic') else {}
        if code in ('update_node_too_old', 'python_provisioning_failed'):
            from quorum_update import diagnostic as update_diagnostic
            data = update_diagnostic(error, code)
        print(json.dumps({'ok': False, 'code': code, 'data': data}))
        return 3
    finally:
        # main owns this connection; dispatchers finish before its lifetime ends.
        if ledger is not None:
            ledger.db.close()


if __name__ == '__main__':
    raise SystemExit(main())
