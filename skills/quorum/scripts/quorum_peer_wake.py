"""Read-only fallback guidance; health timeouts prove neither hosting nor sleep."""
from datetime import datetime, timedelta, timezone
import time
import uuid

from quorum_api import QuorumError

RESTARTED = 'client_restarted_unrecovered'
# This chat's last owner-update status per participant, as the service reported it; never authority.
REPORTS_KEY = 'peer-runtime-reports'
REPORTS_LIMIT = 512
STALE = timedelta(seconds=60)
REPORT_FIELDS = ('refreshed_at', 'active_at', 'state')


def _time(value):
    if not isinstance(value, str) or len(value) > 64: return None
    try: moment = datetime.fromisoformat(value.replace('Z', '+00:00'))
    except ValueError: return None
    return moment if moment.tzinfo else moment.replace(tzinfo=timezone.utc)


def restart_state(peer, report, *, now=None):
    """`client_restarted_unrecovered` only from existing evidence, else None.

    The participant reported a canonical refresh (which needs a client restart) and no runtime report
    since, and its heartbeat, still alive around that refresh, has now been stale for 60 seconds.
    A heartbeat that stopped before the refresh, or any newer runtime report, stays plain `unavailable`.
    """
    if not isinstance(peer, dict) or not isinstance(report, dict) or peer.get('availability') != 'unavailable':
        return None
    refreshed, last_seen = _time(report.get('refreshed_at')), _time(peer.get('last_seen'))
    if refreshed is None or last_seen is None: return None
    runtime = report.get('runtime_report')
    reported = [moment for moment in (_time(report.get('active_at')),
                                      _time(runtime.get('reported_at')) if isinstance(runtime, dict) else None)
                if moment is not None]
    if any(moment >= refreshed for moment in reported): return None
    now = now or datetime.now(timezone.utc)
    if now - last_seen < STALE or last_seen < refreshed - STALE: return None
    return RESTARTED


def remember_reports(ledger, status):
    """Cache only the timestamps restart_state needs from an `update --status` page; bounded, read-only use."""
    participants = status.get('participants') if isinstance(status, dict) else None
    if not isinstance(participants, list): return
    cache = ledger.setting(REPORTS_KEY) or {}
    if not isinstance(cache, dict): cache = {}
    for item in participants:
        if not isinstance(item, dict): continue
        try: agent = str(uuid.UUID(item.get('agent_id')))
        except (ValueError, TypeError, AttributeError): continue
        runtime = item.get('runtime_report')
        entry = {key: item.get(key) for key in REPORT_FIELDS if isinstance(item.get(key), str)}
        if isinstance(runtime, dict) and isinstance(runtime.get('reported_at'), str):
            entry['runtime_report'] = {'reported_at': runtime['reported_at']}
        entry['cached_at'] = int(time.time())
        cache.pop(agent, None)
        cache[agent] = entry
    while len(cache) > REPORTS_LIMIT: cache.pop(next(iter(cache)))
    ledger.setting(REPORTS_KEY, cache)


def server_status(peer):
    """The peer status extension's fields (#113), shape-checked; {} from an older server."""
    if not isinstance(peer, dict) or 'client_run_reported' not in peer: return {}
    awaiting, hold = peer.get('awaiting_owner'), peer.get('reception_hold_until')
    return {'awaiting_owner': awaiting if type(awaiting) is int and 0 <= awaiting <= 32 else None,
            'reception_hold_until': hold if _time(hold) else None,
            'client_run_reported': peer.get('client_run_reported') is True}


def annotate(peers, reports, *, now=None):
    """Mark peers whose existing evidence shows a client restart without recovery; others are unchanged.

    When the server judged restarts from the peer's own run generation (`client_run_reported`), its
    availability stands and this chat's cached owner-update reports are not consulted for that peer.
    """
    reports = reports if isinstance(reports, dict) else {}
    marked = []
    for peer in peers:
        status = server_status(peer)
        if status: peer = {**peer, **status}
        state = None if status.get('client_run_reported') else restart_state(peer, reports.get(peer.get('agent_id')),
                                                                              now=now)
        marked.append(dict(peer, availability=state, server_availability=peer.get('availability')) if state else peer)
    return marked


def with_wake_diagnostics(peers, reports=None, *, now=None):
    if not isinstance(peers, list) or any(not isinstance(peer, dict) for peer in peers):
        raise QuorumError('invalid_response')
    reported = ('Its heartbeat stopped after another run of its client reported it was running, and no heartbeat '
                'followed. ')
    cached = ('Its last report was a client refresh, which needs a client restart, and its heartbeat '
              'stopped without a newer runtime report. ')
    recover = ('Ask its owner to resume the original chat and recover its existing binding (a Codex tool-inbox '
               'worker uses the worker rebind); no new invitation is needed. ')
    grok = ('A Grok TUI can lose its listener while the chat stays open, for example after a session restore '
            'or a Grok self-update; peers then see it unavailable until that same chat runs session recover. ')
    def restarted(peer):
        if peer.get('availability') != RESTARTED: return grok if peer.get('engine') == 'grok' else ''
        return (reported if peer.get('reason') == 'run_changed_without_heartbeat' else cached) + recover
    return [dict(peer, wake={
        'status': 'unsupported',
        'provider': 'unknown',
        'suspension': 'unknown',
        'action': 'owner_open_original_chat',
        'next_step': (restarted(peer) +
                      'Quorum has no verified automatic wake for this binding. Ask its owner to open the '
                      'original chat and recover its existing binding if reception needs recovery. The '
                      'peer view does not prove a message was sent or queued. Leave any existing message '
                      'queued and inspect its delivery status; a timeout does not establish that the computer is asleep.'),
    }) if peer.get('availability') in ('unavailable', RESTARTED) else dict(peer)
        for peer in annotate(peers, reports, now=now)]
