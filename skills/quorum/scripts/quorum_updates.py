"""Explicit owner maintenance. Peer notices never execute an installer or fan out."""
import hashlib
import json
from pathlib import Path
import re
import uuid

from quorum_api import QuorumError, guarded_call, server_refused
from quorum_os import FileLock
from quorum_release import (public_source, read_release, release_status, release_view, runtime_evidence, same_source,
                            short_hash, source_identity, without_carried)
from quorum_resume import binding as resume_binding
from quorum_update import refresh as canonical_refresh, validate_source

BLOCKERS = {'update_tool_missing', 'update_node_too_old', 'update_fetch_failed', 'update_invalid_copy', 'update_validation_failed',
            'update_cache_conflict', 'update_target_changed', 'update_in_progress', 'update_unsafe_cache',
            'python_provisioning_failed'}
# The deployed blocked-evidence allowlist predates these local codes. Report the
# closest accepted code; the local result keeps the precise one. A distinct
# server code needs a reviewed migration.
SERVER_BLOCKER = {'update_node_too_old': 'update_tool_missing',
                  'python_provisioning_failed': 'update_tool_missing'}


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
        # silently approximate the all-quorum set with session_peers. A reviewed
        # refusal such as skill_update_required is not `service_error` and keeps its name.
        if str(error) == 'service_error' and not error.transient:
            raise QuorumError('update_extension_unavailable') from None
        # A request ID already bound to another target or report: update.md's blocker, never an approximation.
        if str(error) == 'idempotency_conflict': raise QuorumError('update_target_changed') from None
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
        if not same_source(target, journal['target']): raise ValueError
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
            refused = str(error) in ('update_extension_unavailable', 'update_target_changed') or server_refused(error)
            raise UpdateFailure(error, {'target':public_source(journal['target']), 'local':journal['local'],
                                'request_id':request_id, 'runtime':runtime_evidence(ledger),
                                'fanout_state':'unavailable' if refused else 'unknown'}) from None
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
        return {'target':public_source(journal['target']), 'local':journal['local'], 'fanout':fanout,
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
        return {'state':'owner_action_required', 'target':public_source(target), 'runtime':runtime_evidence(ledger)}
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
        if journal.get('binding') != _binding(record) or not same_source(journal.get('target'), target):
            raise ValueError('update_notice_denied')
        if journal.get('state') not in ('authorized', 'refreshed', 'reported'):
            raise ValueError('update_cache_conflict')
        if journal.get('state') == 'authorized':
            try:
                refreshed, local = _fresh(current, cache, record, refresh, authorize)
                if not same_source(refreshed, target): raise ValueError('update_target_changed')
            except ValueError as error:
                if str(error) in BLOCKERS:
                    reported = SERVER_BLOCKER.get(str(error), str(error))
                    _call(api, 'session_update_report', {**_scope(record), 'notice_id':notice_id,
                          'event':'blocked', 'evidence':{'code':reported}},
                          'blocked:'+hashlib.sha256((notice_id+':'+reported).encode()).hexdigest(), authorize)
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
        return {'state':'refreshed', 'target':public_source(target), 'local':journal['local'],
                'runtime':runtime_evidence(ledger), 'lifecycle_required':True}


def report_runtime(api, ledger, record, notice_id, request_id, *, authorize=lambda:None):
    """Report a current resident run only after that binding's separate lifecycle recovery."""
    _request(request_id)
    target = _notice(api, record, notice_id, authorize)
    active = runtime_evidence(ledger)
    if not active['verified'] or not same_source(active['source'], target):
        return {'state':'runtime_unverified', 'target':public_source(target), 'runtime':active}
    # The run is part of the identity: a restarted listener's report is a new fact, not a conflicting replay.
    _call(api, 'session_update_report', {**_scope(record), 'notice_id':notice_id, 'event':'active',
          'evidence':{**target, 'run_id':active['run_id']}},
          'runtime:'+hashlib.sha256((request_id+':'+active['run_id']).encode()).hexdigest(), authorize)
    return {'state':'active_reported', 'target':public_source(target), 'runtime':active}


def with_fleet_release(api, record, status, page, authorize):
    """One status page by content hash: each participant's running hash against the fleet target.

    The fleet target is the quorum's newest requested target, which may be newer than this operation's. A running
    hash is the listener's own report, or on a server without reports its runtime report for this operation. Both
    are self-reports. `behind` means not on the target: hashes have no order. No update state changes here.
    """
    if not isinstance(status, dict): return status
    target = source_identity(status.get('target'))
    participants = status.get('participants')
    shown = {**status, 'target':public_source(target)}
    if not isinstance(participants, list): return shown
    authorize()
    fleet = release_status(api, {key: record[key] for key in ('agent_id', 'session_ref')}, **page) or {}
    authorize()
    running = fleet.get('agents') or {}
    fleet_target = fleet.get('target') or (target or {}).get('content_sha256')
    rows, counts, behind = [], {'on_target':0, 'unreported':0}, []
    for item in participants:
        if not isinstance(item, dict):
            rows.append(item)
            continue
        report = item.get('runtime_report')
        report = without_carried(report) if isinstance(report, dict) else report
        row = running.get(item.get('agent_id'))
        # With the server's reports its run check stands: an older report never overrules it.
        if not fleet and isinstance(report, dict): row = {**report, 'reported_via':'update_report'}
        view = release_view(row, fleet_target)
        if view['state'] == 'behind': behind.append({'agent_id':item.get('agent_id'), 'running':view['running']})
        elif view['state'] in counts: counts[view['state']] += 1
        rows.append({**item, **({'runtime_report':report} if 'runtime_report' in item else {}), 'release':view})
    return {**shown, 'participants':rows, 'fleet_release':{'target':short_hash(fleet_target), **counts, 'behind':behind}}


def with_restart_visibility(api, ledger, record, status, authorize):
    """Join an operation page with current peer health to name `client_restarted_unrecovered`.

    A server with the peer status extension decides that from each peer's run generation; otherwise the
    client-side heuristic over this page's reports applies.

    The report cache feeds later `session peers`. Peer health is best effort: if it is unavailable the
    page is returned as the service sent it. No participant gains or loses any update state here.
    """
    from quorum_peer_wake import annotate, remember_reports
    participants = status.get('participants')
    if not isinstance(participants, list): return status
    remember_reports(ledger, status)
    try:
        authorize()
        peers = api.call('session_peers', {'agent_id':record['agent_id'], 'session_ref':record['session_ref']})
        authorize()
    except QuorumError:
        return status
    if not isinstance(peers, list): return status
    reports = {item.get('agent_id'):item for item in participants if isinstance(item, dict)}
    health = {peer.get('agent_id'):peer for peer in annotate([peer for peer in peers if isinstance(peer, dict)], reports)}
    joined = []
    for item in participants:
        peer = health.get(item.get('agent_id')) if isinstance(item, dict) else None
        if peer is None or 'availability' not in peer: joined.append(item)
        else:
            # A server with the peer status extension also reports the awaiting-owner count and a running hold.
            fields = {key: peer[key] for key in ('awaiting_owner', 'reception_hold_until') if key in peer}
            joined.append({**item, 'availability':peer['availability'], 'last_seen':peer.get('last_seen'), **fields})
    return {**status, 'participants':joined}


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
    parser.add_argument('--worker-capability', type=Path,
                        help='Claude worker capability file (adopt scope, or a create/accept scope with "update": true); '
                             'identity comes from that worker binding')


def dispatch(args, auth):
    from quorum_live import api_for
    from quorum_native_inbox import require_worker
    from quorum_session import current_session, require_caller
    from quorum_state import LocalLedger
    context = getattr(args, 'worker_context', None)
    worker_api = check_worker = None
    if context is not None:
        # An adopted Claude worker acts only as its own connected binding, under its capability.
        from quorum_claude_worker import owner_binding
        with_ledger, record, check_worker, worker_api = owner_binding(context, auth)
    else:
        sid = current_session()
        with_ledger = LocalLedger(auth.root/'sessions'/sid)
    try:
        record = with_ledger.setting('session-agent')
        if not record or record.get('quorum') != args.quorum:
            raise ValueError('session_not_connected')
        saved = _binding(record)
        def authorize():
            if check_worker is not None: check_worker()
            else: require_caller(sid, record['engine'])
            latest = with_ledger.setting('session-agent')
            def same_current():
                return (isinstance(latest, dict) and _binding(latest) == saved
                        and str(Path.cwd().resolve()) == saved.get('cwd'))
            if not same_current(): raise ValueError('session_binding_denied')
            if latest.get('transport') == 'tool-inbox' and check_worker is None:
                require_worker(latest)
                # Worker discovery can also block; do not dispatch using a
                # binding or directory changed while proof was being checked.
                latest = with_ledger.setting('session-agent')
                if not same_current(): raise ValueError('session_binding_denied')
        authorize()
        if args.owner_refresh and not args.apply_notice: raise ValueError('update_request_invalid')
        if not args.status: _request(args.request_id)
        api = worker_api if worker_api is not None else api_for(auth)[0]
        options = {'current':Path(__file__).resolve().parents[1],
                   'cache':Path.home()/'.local/share/quorum/skill-updates', 'authorize':authorize}
        if args.status:
            payload = {**_scope(record), 'operation_id':str(uuid.UUID(args.status))}
            if args.after_agent: payload['after_agent'] = str(uuid.UUID(args.after_agent))
            page = {key:payload[key] for key in ('operation_id', 'after_agent') if key in payload}
            status = with_fleet_release(api, record, _call(api, 'session_update_status', payload, None, authorize),
                                        page, authorize)
            return with_restart_visibility(api, with_ledger, record, status, authorize)
        if args.apply_notice:
            return apply_notice(api, with_ledger, record, args.apply_notice, args.request_id,
                                owner_refresh=args.owner_refresh, **options)
        if args.report_runtime:
            return report_runtime(api, with_ledger, record, args.report_runtime, args.request_id,
                                  authorize=authorize)
        return owner_update(api, with_ledger, record, args.request_id, **options)
    finally: with_ledger.db.close()
