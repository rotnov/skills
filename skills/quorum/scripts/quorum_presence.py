"""Quiet native-listener heartbeat, independent of task acceptance or model turns."""
import time
import uuid
from quorum_api import QuorumError
from quorum_release import ReleaseGuard


class Presence:
    def __init__(self, api, record, ledger, *, clock=time.monotonic):
        self.api, self.record, self.ledger, self.clock = api, record, ledger, clock
        self.connection_id = str(uuid.uuid4())
        self.next_tick = 0
        self.release = ReleaseGuard(api, ledger)

    def connected(self):
        # A successful new subscription is a reconnect edge, not an inbox wake.
        self.connection_id = str(uuid.uuid4())
        self.next_tick = 0
        self.tick()

    def tick(self):
        if self.clock() < self.next_tick: return
        self.next_tick = self.clock() + 20
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
        self.ledger.setting('presence', {'state':'online', 'last_heartbeat_at':time.time()})
