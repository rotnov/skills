"""Quiet native-listener heartbeat, independent of task acceptance or model turns."""
from datetime import datetime, timezone
import time
import uuid
from quorum_api import QuorumError, is_transient_service, rpc_missing
from quorum_release import ReleaseGuard

# Peers derive `heartbeat_timeout` after this gap; only then were we really offline.
HEALTH_WINDOW = 60
# The peer status extension (#113) takes these; a server without it is asked again after this delay.
STATUS_KEY = 'peer-status-support'
STATUS_RETRY = 600
AWAITING_LIMIT = 32
HOLD_LIMIT = 1800


def _iso(moment):
    return datetime.fromtimestamp(moment, timezone.utc).isoformat(timespec='seconds')


def peer_status_fields(awaiting, hold_until, run_id, started_at, *, now):
    """Bounded heartbeat status arguments; never a message ID or body. Invalid parts are left out."""
    fields = {'awaiting_owner': min(max(awaiting, 0), AWAITING_LIMIT) if type(awaiting) is int else 0}
    if type(hold_until) in (int, float) and now < hold_until:
        fields['reception_hold_until'] = _iso(min(hold_until, now + HOLD_LIMIT))
    try: fields['client_run_id'] = str(uuid.UUID(run_id))
    except (TypeError, ValueError, AttributeError): pass
    if type(started_at) in (int, float): fields['runtime_started_at'] = _iso(min(started_at, now))
    return fields


class Presence:
    def __init__(self, api, record, ledger, *, clock=time.monotonic, persist_epoch=False, wall=None, run_id=None,
                 status=None):
        self.api, self.record, self.ledger, self.clock = api, record, ledger, clock
        self.wall = wall or (lambda: time.time())
        # Peer-visible status: this run's generation and start, plus an optional callable returning
        # (awaiting-owner count, explicit hold deadline or None) from this binding's private ledger.
        self.run_id, self.status, self.started_at = run_id, status, self.wall()
        self.connection_id = str(uuid.uuid4())
        self.next_tick = 0
        self.online_at = None  # Last acknowledged heartbeat of the current epoch.
        self.release = ReleaseGuard(api, ledger)
        # Opt-in for bounded foreground calls under the consumer lock: one epoch spans
        # separate processes until a real gap, a clock reversal or an explicit restart.
        self.persist, self.acked_wall, self.pending = persist_epoch, None, False
        saved = ledger.setting('presence-epoch') if persist_epoch else None
        # An epoch acknowledged by an earlier process: reusing it sends no Hello.
        self.inherited, self.resubscribed, self.seen = None, False, None
        try:
            if isinstance(saved, dict) and (saved.get('acked_at') is None or type(saved['acked_at']) in (int, float)):
                self.connection_id, self.acked_wall = str(uuid.UUID(saved['connection_id'])), saved.get('acked_at')
                self.pending = saved.get('pending') is True
                if self.acked_wall is not None: self.inherited = self.connection_id
        except (KeyError, TypeError, ValueError): pass  # An unreadable epoch simply starts a new one.

    def _epoch(self, pending):
        self.ledger.setting('presence-epoch', {'connection_id':self.connection_id, 'acked_at':self.acked_wall,
                                               'pending':pending})

    def connected(self, stopping=None):
        # A Realtime (re)subscription only refreshes health promptly. The Hello
        # epoch changes in tick(), and only after a gap peers could observe.
        self.resubscribed = self.online_at is not None
        self.next_tick = 0
        self.tick(stopping)

    def tick(self, stopping=None):
        """`stopping` (optional): a stop observed before or after the release check sends no heartbeat."""
        if self.clock() < self.next_tick: return
        if stopping and stopping(): return
        self.next_tick = self.clock() + 20
        if self.persist:
            now = self.wall()
            # A gap since the last acknowledgement, or a clock that moved backwards (an unknown
            # gap), starts one new epoch. A pending heartbeat (sent, response unknown) is resolved
            # with its same epoch first, even within an already acknowledged epoch and after any delay.
            if not self.pending and self.acked_wall is not None and (
                    now - self.acked_wall >= HEALTH_WINDOW or now < self.acked_wall
                    or (self.online_at is not None and self.clock() - self.online_at[0] >= HEALTH_WINDOW)):
                self.connection_id, self.acked_wall, self.online_at = str(uuid.uuid4()), None, None
            # Intent before the RPC: a lost response is retried with this same epoch.
            self.pending = True
            self._epoch(True)
        # Monotonic time pauses during macOS sleep; wall time catches that gap.
        elif self.online_at is not None and max(self.clock() - self.online_at[0],
                                                 time.time() - self.online_at[1]) >= HEALTH_WINDOW:
            # Genuine reconnect: retries of this new ID stay idempotent until acknowledged.
            self.connection_id, self.online_at = str(uuid.uuid4()), None
        try:
            versions = self.release.check()
            # An unsent heartbeat leaves any pending epoch intent to be retried with the same epoch.
            if stopping and stopping(): return
            payload = {'agent_id':self.record['agent_id'], 'session_ref':self.record['session_ref'],
                       'connection_id':self.connection_id, **versions}
            result = self._send(payload, stopping)
            if result is None: return
            if isinstance(result, dict) and result.get('code') == 'skill_update_required':
                self.release.accept(result.get('required'))
                raise QuorumError('skill_update_required')
        except QuorumError as error:
            if str(error) == 'service_error' and not is_transient_service(error):
                self.ledger.setting('presence', {'state':'unavailable', 'code':'coordination_unavailable'})
                raise QuorumError('coordination_unavailable') from None
            if not is_transient_service(error): raise
            self.ledger.setting('presence', {'state':'unreachable', 'code':str(error)})
            return
        self.online_at = (self.clock(), self.wall() if self.persist else time.time())
        if self.persist:
            self.acked_wall, self.pending = self.online_at[1], False
            self._epoch(False)
        self.ledger.setting('presence', {'state':'online', 'last_heartbeat_at':self.online_at[1]})
        self._hello(result if isinstance(result, dict) else {})

    def _status_supported(self):
        saved = self.ledger.setting(STATUS_KEY)
        if not isinstance(saved, dict) or type(saved.get('unsupported_at')) not in (int, float): return True
        return not 0 <= self.wall() - saved['unsupported_at'] < STATUS_RETRY

    def _send(self, payload, stopping):
        """The heartbeat with peer status where the server takes it; otherwise the reviewed five arguments.

        Only PostgREST's missing-function answer falls back: nothing ran, so the same connection is resent
        at once. Any other failure is the heartbeat's own. None means a stop was observed before resending.
        """
        if not self._status_supported():
            return self.api.call('session_heartbeat', payload)
        now = self.wall()
        awaiting, hold_until = self.status() if self.status else (0, None)
        extended = {**payload, **peer_status_fields(awaiting, hold_until, self.run_id, self.started_at, now=now)}
        try:
            result = self.api.call('session_heartbeat', extended)
        except QuorumError as error:
            if not rpc_missing(error): raise
            self.ledger.setting(STATUS_KEY, {'unsupported_at': now})
            if stopping and stopping(): return None
            return self.api.call('session_heartbeat', payload)
        # An expired "unsupported" marker is cleared once the server takes the status.
        if self.ledger.setting(STATUS_KEY) not in (None, {'supported': True}):
            self.ledger.setting(STATUS_KEY, {'supported': True})
        return result

    def _hello(self, result):
        """Persist what this connection's Hello reached, writing only when the outcome changes."""
        saved = self.ledger.setting('hello') or {}
        same = saved.get('connection_id') == self.connection_id
        new = result.get('new_connection') is True
        record = {'connection_id': self.connection_id, 'project_ref': self.record.get('project_ref'),
                  # A retry after a lost response is not new, yet its Hello went out; only an
                  # epoch carried over from an earlier process, or a kept connection, sent none.
                  'reused': (saved.get('reused', False) if same and self.seen == self.connection_id else
                             not new and self.connection_id == self.inherited),
                  'kept': saved.get('kept', 0) if same else 0, **hello_result(result.get('hello'))}
        if same and record['sent'] is None and saved.get('sent') is not None:
            record.update({key: saved.get(key) for key in HELLO_FIELDS})
        if self.resubscribed and same and not new: record['kept'] += 1
        self.resubscribed, self.seen = False, self.connection_id
        record['summary'] = hello_summary(record)
        if record != {key: saved.get(key) for key in record}:
            self.ledger.setting('hello', {**record, 'recorded_at': self.wall() if self.persist else time.time()})


HELLO_FIELDS = ('sent', 'roles', 'available', 'available_roles', 'roster')


def hello_result(value):
    """Bounded, typed view of the server's optional Hello report; None fields mean unreported."""
    if not isinstance(value, dict): return dict.fromkeys(HELLO_FIELDS)
    def count(key):
        item = value.get(key)
        return item if type(item) is int and 0 <= item <= 128 else None
    def roles(key):
        item = value.get(key)
        if not isinstance(item, list): return []
        return [str(role)[:48] for role in item[:16] if isinstance(role, str)]
    return {'sent': count('peers'), 'roles': roles('roles'), 'available': count('available'),
            'available_roles': roles('available_roles'), 'roster': isinstance(value.get('roster_message_id'), str)}


def hello_summary(record):
    """One owner-facing line for the setup outcome and `session status`."""
    project = record.get('project_ref') or 'this project'
    sent = record.get('sent')
    if sent is None:
        result = 'Hello result not reported by the server (Hello reporting extension not applied)'
    elif sent == 0:
        result = '0 peers share project reference ' + project + '; the Hello reached no one'
    else:
        result = 'Hello sent to ' + str(sent) + ' peer' + ('' if sent == 1 else 's') + ' in ' + project
        if record.get('roles'):
            result += ' (' + ', '.join(record['roles']) + (', ...' if sent > len(record['roles']) else '') + ')'
    if record.get('available') is not None:
        result += '; roster: ' + str(record['available']) + ' available'
    if record.get('reused') or record.get('kept'):
        reason = 'Reused connection ' if record.get('reused') else 'Reconnect kept connection '
        result = (reason + str(record.get('connection_id', ''))[:8] + ': no new Hello was sent'
                  + ('' if sent is None else '; earlier: ' + result))
    return result


def hello_status(ledger):
    """The persisted Hello outcome of the current or last connection, if any."""
    saved = ledger.setting('hello')
    return dict(saved) if isinstance(saved, dict) else None


def hello_once(ledger):
    """{'hello': summary} the first time a connection's outcome is surfaced to a bounded caller, else {}."""
    saved = ledger.setting('hello')
    if not isinstance(saved, dict) or saved.get('reported') or not saved.get('summary'): return {}
    ledger.setting('hello', {**saved, 'reported': True})
    return {'hello': saved['summary']}
