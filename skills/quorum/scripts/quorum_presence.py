"""Quiet native-listener heartbeat, independent of task acceptance or model turns."""
import time
import uuid
from quorum_api import QuorumError
from quorum_release import ReleaseGuard

# Peers derive `heartbeat_timeout` after this gap; only then were we really offline.
HEALTH_WINDOW = 60


class Presence:
    def __init__(self, api, record, ledger, *, clock=time.monotonic):
        self.api, self.record, self.ledger, self.clock = api, record, ledger, clock
        self.connection_id = str(uuid.uuid4())
        self.next_tick = 0
        self.online_at = None  # Last acknowledged heartbeat of the current epoch.
        self.release = ReleaseGuard(api, ledger)

    def connected(self):
        # A Realtime (re)subscription only refreshes health promptly. The Hello
        # epoch changes in tick(), and only after a gap peers could observe.
        self.next_tick = 0
        self.tick()

    def tick(self):
        if self.clock() < self.next_tick: return
        self.next_tick = self.clock() + 20
        # Monotonic time pauses during macOS sleep; wall time catches that gap.
        if self.online_at is not None and max(self.clock() - self.online_at[0],
                                               time.time() - self.online_at[1]) >= HEALTH_WINDOW:
            # Genuine reconnect: retries of this new ID stay idempotent until acknowledged.
            self.connection_id, self.online_at = str(uuid.uuid4()), None
        try:
            versions = self.release.check()
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
        self.online_at = (self.clock(), time.time())
        self.ledger.setting('presence', {'state':'online', 'last_heartbeat_at':self.online_at[1]})
