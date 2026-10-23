"""Quiet native-listener heartbeat, independent of task acceptance or model turns."""
import time
import uuid
from quorum_api import QuorumError
from quorum_release import ReleaseGuard

# Peers derive `heartbeat_timeout` after this gap; only then were we really offline.
HEALTH_WINDOW = 60


class Presence:
    def __init__(self, api, record, ledger, *, clock=time.monotonic, persist_epoch=False, wall=None):
        self.api, self.record, self.ledger, self.clock = api, record, ledger, clock
        self.wall = wall or (lambda: time.time())
        self.connection_id = str(uuid.uuid4())
        self.next_tick = 0
        self.online_at = None  # Last acknowledged heartbeat of the current epoch.
        self.release = ReleaseGuard(api, ledger)
        # Opt-in for bounded foreground calls under the consumer lock: one epoch spans
        # separate processes until a real gap, a clock reversal or an explicit restart.
        self.persist, self.acked_wall, self.pending = persist_epoch, None, False
        saved = ledger.setting('presence-epoch') if persist_epoch else None
        try:
            if isinstance(saved, dict) and (saved.get('acked_at') is None or type(saved['acked_at']) in (int, float)):
                self.connection_id, self.acked_wall = str(uuid.UUID(saved['connection_id'])), saved.get('acked_at')
                self.pending = saved.get('pending') is True
        except (KeyError, TypeError, ValueError): pass  # An unreadable epoch simply starts a new one.

    def _epoch(self, pending):
        self.ledger.setting('presence-epoch', {'connection_id':self.connection_id, 'acked_at':self.acked_wall,
                                               'pending':pending})

    def connected(self, stopping=None):
        # A Realtime (re)subscription only refreshes health promptly. The Hello
        # epoch changes in tick(), and only after a gap peers could observe.
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
            result = self.api.call('session_heartbeat', {'agent_id':self.record['agent_id'],
                          'session_ref':self.record['session_ref'], 'connection_id':self.connection_id, **versions})
            if isinstance(result, dict) and result.get('code') == 'skill_update_required':
                self.release.accept(result.get('required'))
                raise QuorumError('skill_update_required')
        except QuorumError as error:
            if str(error) == 'service_error':
                self.ledger.setting('presence', {'state':'unavailable', 'code':'coordination_unavailable'})
                raise QuorumError('coordination_unavailable') from None
            if str(error) != 'service_unavailable': raise
            self.ledger.setting('presence', {'state':'unreachable', 'code':'service_unavailable'})
            return
        self.online_at = (self.clock(), self.wall() if self.persist else time.time())
        if self.persist:
            self.acked_wall, self.pending = self.online_at[1], False
            self._epoch(False)
        self.ledger.setting('presence', {'state':'online', 'last_heartbeat_at':self.online_at[1]})
