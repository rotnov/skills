"""Explicit owner maintenance. Peer notices never execute an installer or fan out."""
import hashlib
import json
from pathlib import Path
import re
import uuid

from quorum_api import QuorumError, guarded_call
from quorum_os import FileLock
from quorum_release import read_release, runtime_evidence, source_identity
from quorum_resume import binding as resume_binding
from quorum_update import refresh as canonical_refresh, validate_source

BLOCKERS = {'update_tool_missing', 'update_fetch_failed', 'update_invalid_copy', 'update_validation_failed',
            'update_cache_conflict', 'update_target_changed', 'update_in_progress', 'update_unsafe_cache'}


class UpdateFailure(QuorumError):
    """Keep known local success distinct from an unavailable/uncertain remote effect."""
    def __init__(self, error, diagnostic):
        super().__init__(str(error), transient=error.transient)
        self.diagnostic = diagnostic


def _request(value):
    if not isinstance(value, str) or not re.fullmatch('[A-Za-z0-9_.:-]{1,128}', value):
        raise ValueError('update_request_invalid')
    return value


def _binding(record):
    """Update-specific immutable snapshot; never widen global recovery grants."""
    endpoints = {key: record.get(key) for key in ('messaging_socket', 'grok_transport')}
    marker = hashlib.sha256(json.dumps(endpoints, sort_keys=True).encode()).hexdigest()
    snapshot = {**resume_binding(record), 'registration': record.get('registration'),
                'transport': record.get('transport'), 'worker_binding': record.get('worker_binding'),
                'endpoint_sha256': marker}
    return json.loads(json.dumps(snapshot, sort_keys=True))


def _scope(record):
    return {key:record[key] for key in ('agent_id', 'session_ref', 'quorum_id')}


def _call(api, operation, payload, request_id, authorize):
    try:
        result = guarded_call(api, operation, payload, request_id,
                              before_request=authorize, on_dispatch=lambda:None)
    except QuorumError as error:
        # Missing RPCs and an incompatible implementation are never a reason to
        # silently approximate the all-quorum set with session_peers.
        if str(error) == 'service_error' and not error.transient:
            raise QuorumError('update_extension_unavailable') from None
        raise
    authorize()
    if not isinstance(result, dict): raise QuorumError('invalid_response')
    return result


def _fresh(current, cache, record, refresh, authorize):
    authorize()
    local = refresh(current, cache, record['engine'])
    authorize()
    selected = Path(local['skill_dir'])
    target = {**read_release(selected/'assets/release.json'), 'content_sha256':validate_source(selected)}
    if source_identity(target) is None or target['content_sha256'] != local.get('content_sha256'):
        raise ValueError('update_invalid_copy')
    return target, {key:local[key] for key in ('status', 'skill_dir', 'content_sha256')}


def _cached(journal):
    try:
        selected = Path(journal['local']['skill_dir'])
        target = {**read_release(selected/'assets/release.json'), 'content_sha256':validate_source(selected)}
        if target != journal['target']: raise ValueError
    except (OSError, ValueError, TypeError, KeyError):
        raise ValueError('update_cache_conflict') from None


def owner_update(api, ledger, record, request_id, *, current, cache,
                 refresh=canonical_refresh, authorize=lambda:None):
    """One owner invocation: validate locally, persist the target, then request fan-out."""
    _request(request_id)
    authorize()
    key = 'owner-update:' + hashlib.sha256((record['agent_id']+':'+request_id).encode()).hexdigest()
    with FileLock(ledger.root/'owner-update.lock'):
        journal = ledger.setting(key)
        if journal is None:
            target, local = _fresh(current, cache, record, refresh, authorize)
            journal = {'binding':_binding(record), 'target':target, 'local':local, 'state':'target_ready'}
            # A lost send/HTTP response can never select a newer main on retry.
            ledger.setting(key, journal)
        else:
            if journal.get('binding') != _binding(record): raise ValueError('session_binding_denied')
            _cached(journal)
        authorize()
        payload = {**_scope(record), **journal['target']}
        journal['state'] = 'fanout_unknown'
        ledger.setting(key, journal)
        try:
            fanout = _call(api, 'session_update_request', payload, request_id, authorize)
        except QuorumError as error:
            raise UpdateFailure(error, {'target':journal['target'], 'local':journal['local'],
                                'request_id':request_id, 'runtime':runtime_evidence(ledger),
                                'fanout_state':'unavailable' if str(error)=='update_extension_unavailable' else 'unknown'}) from None
        try:
            operation_id = str(uuid.UUID(fanout['operation_id']))
            counts = {key:fanout[key] for key in ('queued', 'existing')}
            failure_count = fanout.get('failed', 0)
            if any(type(value) is not int or not 0 <= value <= 2147483647
                   for value in (*counts.values(), failure_count)):
                raise ValueError
        except (KeyError, ValueError, TypeError, AttributeError):
            raise QuorumError('invalid_response') from None
        fanout = {'operation_id':operation_id, **counts}
        if failure_count: fanout['failed'] = failure_count
        ledger.setting(key, {**journal, 'state':'queued', 'fanout':fanout})
        return {'target':journal['target'], 'local':journal['local'], 'fanout':fanout,
                'runtime':runtime_evidence(ledger), 'all_participants_upgraded':False}


def _notice(api, record, notice_id, authorize):
    notice_id = str(uuid.UUID(notice_id))
    value = _call(api, 'session_update_status', {**_scope(record), 'notice_id':notice_id}, None, authorize)
    target = source_identity(value.get('target'))
    if (value.get('notice_id') != notice_id or value.get('recipient_agent_id') != record['agent_id']
            or value.get('quorum_id') != record['quorum_id'] or target is None):
        raise ValueError('update_notice_denied')
    return target


def apply_notice(api, ledger, record, notice_id, request_id, *, owner_refresh=False,
                 current, cache, refresh=canonical_refresh, authorize=lambda:None):
    """Explicit receiving-owner consent refreshes one notice; never calls fan-out or lifecycle."""
    _request(request_id)
    notice_id = str(uuid.UUID(notice_id))
    target = _notice(api, record, notice_id, authorize)
    if not owner_refresh:
        return {'state':'owner_action_required', 'target':target, 'runtime':runtime_evidence(ledger)}
    # A notice's recipient/target is immutable. Invocation IDs may change on
    # retry, but cannot create another installer/report operation for that notice.
    key = 'notice-update:' + hashlib.sha256(notice_id.encode()).hexdigest()
    with FileLock(ledger.root/'owner-update.lock'):
        authorize()
        journal = ledger.setting(key)
        if journal is None:
            # This one-invocation local grant comes from the receiving owner's
            # explicit command, never from a peer body, reception or delegation.
            journal = {'binding':_binding(record), 'target':target, 'authority':'explicit_owner_refresh',
                       'state':'authorized'}
            ledger.setting(key, journal)
        if journal.get('binding') != _binding(record) or journal.get('target') != target:
            raise ValueError('update_notice_denied')
        if journal.get('state') not in ('authorized', 'refreshed', 'reported'):
            raise ValueError('update_cache_conflict')
        if journal.get('state') == 'authorized':
            try:
                refreshed, local = _fresh(current, cache, record, refresh, authorize)
                if refreshed != target: raise ValueError('update_target_changed')
            except ValueError as error:
                if str(error) in BLOCKERS:
                    _call(api, 'session_update_report', {**_scope(record), 'notice_id':notice_id,
                          'event':'blocked', 'evidence':{'code':str(error)}},
                          'blocked:'+hashlib.sha256((notice_id+':'+str(error)).encode()).hexdigest(), authorize)
                raise
            journal = {**journal, 'local':local, 'state':'refreshed'}
            ledger.setting(key, journal)
        else: _cached(journal)
        authorize()
        if journal['state'] != 'reported':
            # Persist completion only after a known reply. An uncertain report
            # retries this same inert body/ID without repeating local refresh.
            _call(api, 'session_update_report', {**_scope(record), 'notice_id':notice_id,
                  'event':'refreshed', 'evidence':target}, 'refresh:'+hashlib.sha256(notice_id.encode()).hexdigest(), authorize)
            journal = {**journal, 'state':'reported'}
            ledger.setting(key, journal)
        return {'state':'refreshed', 'target':target, 'local':journal['local'],
                'runtime':runtime_evidence(ledger), 'lifecycle_required':True}


def report_runtime(api, ledger, record, notice_id, request_id, *, authorize=lambda:None):
    """Report a current resident run only after that binding's separate lifecycle recovery."""
    _request(request_id)
    target = _notice(api, record, notice_id, authorize)
    active = runtime_evidence(ledger)
    if not active['verified'] or active['source'] != target:
        return {'state':'runtime_unverified', 'target':target, 'runtime':active}
    _call(api, 'session_update_report', {**_scope(record), 'notice_id':notice_id, 'event':'active',
          'evidence':{**target, 'run_id':active['run_id']}}, 'runtime:'+hashlib.sha256(request_id.encode()).hexdigest(), authorize)
    return {'state':'active_reported', 'target':target, 'runtime':active}


def add_commands(commands):
    parser = commands.add_parser('update', help='Explicit owner refresh and bounded quorum maintenance')
    parser.add_argument('quorum')
    parser.add_argument('--request-id', help='Keep this ID when resuming an uncertain refresh/fan-out')
    action = parser.add_mutually_exclusive_group()
    action.add_argument('--status', metavar='OPERATION_UUID')
    action.add_argument('--apply-notice', metavar='NOTICE_UUID')
    action.add_argument('--report-runtime', metavar='NOTICE_UUID')
    parser.add_argument('--owner-refresh', action='store_true',
                        help='Receiving owner explicitly authorizes one canonical refresh for --apply-notice')
    parser.add_argument('--after-agent', help='Status pagination cursor returned by the server')


def dispatch(args, auth):
    from quorum_live import api_for
    from quorum_native_inbox import require_worker
    from quorum_session import current_session, require_caller
    from quorum_state import LocalLedger
    sid = current_session()
    with_ledger = LocalLedger(auth.root/'sessions'/sid)
    try:
        record = with_ledger.setting('session-agent')
        if not record or record.get('quorum') != args.quorum:
            raise ValueError('session_not_connected')
        saved = _binding(record)
        def authorize():
            require_caller(sid, record['engine'])
            latest = with_ledger.setting('session-agent')
            def same_current():
                return (isinstance(latest, dict) and _binding(latest) == saved
                        and str(Path.cwd().resolve()) == saved.get('cwd'))
            if not same_current(): raise ValueError('session_binding_denied')
            if latest.get('transport') == 'tool-inbox':
                require_worker(latest)
                # Worker discovery can also block; do not dispatch using a
                # binding or directory changed while proof was being checked.
                latest = with_ledger.setting('session-agent')
                if not same_current(): raise ValueError('session_binding_denied')
        authorize()
        if args.owner_refresh and not args.apply_notice: raise ValueError('update_request_invalid')
        if not args.status: _request(args.request_id)
        api, _, _ = api_for(auth)
        options = {'current':Path(__file__).resolve().parents[1],
                   'cache':Path.home()/'.local/share/quorum/skill-updates', 'authorize':authorize}
        if args.status:
            payload = {**_scope(record), 'operation_id':str(uuid.UUID(args.status))}
            if args.after_agent: payload['after_agent'] = str(uuid.UUID(args.after_agent))
            return _call(api, 'session_update_status', payload, None, authorize)
        if args.apply_notice:
            return apply_notice(api, with_ledger, record, args.apply_notice, args.request_id,
                                owner_refresh=args.owner_refresh, **options)
        if args.report_runtime:
            return report_runtime(api, with_ledger, record, args.report_runtime, args.request_id,
                                  authorize=authorize)
        return owner_update(api, with_ledger, record, args.request_id, **options)
    finally: with_ledger.db.close()
