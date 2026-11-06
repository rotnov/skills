"""Peer status on the heartbeat and in the peer views (#113, #104): new and older servers.

Synthetic APIs, temporary ledgers and fake clocks only; no model, provider or Quorum service.
"""
from datetime import datetime, timezone
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
import unittest.mock
import uuid

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'skills/quorum/scripts'))
from quorum_test_support import isolated_profiles
import test_quorum_awaiting_owner as awaiting
import test_quorum_native_inbox as base

RUN = '12345678-1234-4123-8123-123456789abc'
AGENT = '66666666-6666-4666-8666-666666666666'
NOW = 1_800_000_000.0
STATUS_KEYS = {'awaiting_owner', 'reception_hold_until', 'client_run_id', 'runtime_started_at'}


def iso(moment):
    return datetime.fromtimestamp(moment, timezone.utc).isoformat(timespec='seconds')


class Server:
    """A heartbeat endpoint; `legacy` answers like PostgREST without the peer status extension."""
    def __init__(self, legacy=False, error=None):
        self.legacy, self.error, self.calls = legacy, error, []

    def call(self, operation, payload, request_id=None):
        from quorum_api import QuorumError
        if operation == 'session_requirements': return {'protocol_version': 2, 'skill_version': '0.3.0'}
        self.calls.append(dict(payload))
        if self.error and 'awaiting_owner' in payload: raise QuorumError(*self.error[0], **self.error[1])
        if self.legacy and STATUS_KEYS & set(payload): raise QuorumError('service_error', missing=True)
        return {'online': True, 'new_connection': len(self.calls) == 1}


class PresenceStatusTests(unittest.TestCase):
    def setUp(self):
        from quorum_state import LocalLedger
        quiet = patch('quorum_presence.Presence._report_release')
        quiet.start()
        self.addCleanup(quiet.stop)
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.ledger = LocalLedger(Path(temporary.name))
        self.addCleanup(self.ledger.db.close)
        self.clock, self.wall = [0], [NOW]

    def presence(self, server, **options):
        from quorum_presence import Presence
        return Presence(server, {'agent_id': AGENT, 'session_ref': 's'}, self.ledger, clock=lambda: self.clock[0],
                        wall=lambda: self.wall[0], persist_epoch=True, **options)

    @isolated_profiles
    def test_new_server_receives_bounded_status_with_the_same_connection(self):
        server = Server()
        health = self.presence(server, run_id=RUN, status=lambda: (3, NOW + 120))
        health.tick()
        sent = server.calls[-1]
        self.assertEqual({key: sent[key] for key in STATUS_KEYS},
                         {'awaiting_owner': 3, 'reception_hold_until': iso(NOW + 120), 'client_run_id': RUN,
                          'runtime_started_at': iso(NOW)})
        self.assertEqual(len(server.calls), 1)
        self.assertEqual(self.ledger.setting('presence')['state'], 'online')
        self.assertIsNone(self.ledger.setting('peer-status-support'))

    @isolated_profiles
    def test_default_status_is_zero_without_hold_and_bad_run_is_left_out(self):
        server = Server()
        self.presence(server, run_id='not-a-uuid').tick()
        sent = server.calls[-1]
        self.assertEqual(sent['awaiting_owner'], 0)
        self.assertNotIn('reception_hold_until', sent)
        self.assertNotIn('client_run_id', sent)

    @isolated_profiles
    def test_older_server_gets_the_reviewed_arguments_at_once_and_is_asked_again_later(self):
        server = Server(legacy=True)
        health = self.presence(server, run_id=RUN, status=lambda: (1, None))
        health.tick()
        extended, plain = server.calls
        self.assertTrue(STATUS_KEYS & set(extended))
        self.assertFalse(STATUS_KEYS & set(plain))
        # Nothing ran on the first call, so the same Hello epoch is resent, and health is online.
        self.assertEqual(extended['connection_id'], plain['connection_id'])
        self.assertEqual(self.ledger.setting('presence')['state'], 'online')
        self.assertEqual(self.ledger.setting('peer-status-support'), {'unsupported_at': NOW})
        # Within the retry window only the reviewed arguments go out: no repeated 404.
        self.clock[0], self.wall[0] = 20, NOW + 20
        health.tick()
        self.assertEqual(len(server.calls), 3)
        self.assertFalse(STATUS_KEYS & set(server.calls[-1]))
        # A new run of the client inherits the marker through the ledger.
        self.clock[0], self.wall[0] = 40, NOW + 40
        self.presence(server, run_id=RUN).tick()
        self.assertFalse(STATUS_KEYS & set(server.calls[-1]))
        # After the window the server is asked again; once it takes the status the marker clears.
        server.legacy = False
        self.clock[0], self.wall[0] = 1000, NOW + 700
        health.tick()
        self.assertEqual(server.calls[-1]['awaiting_owner'], 1)
        self.assertEqual(self.ledger.setting('peer-status-support'), {'supported': True})

    @isolated_profiles
    def test_only_a_missing_function_falls_back(self):
        from quorum_api import QuorumError
        denied = Server(error=(('auth_required',), {}))
        with self.assertRaisesRegex(QuorumError, 'auth_required'):
            self.presence(denied, run_id=RUN).tick()
        self.assertEqual(len(denied.calls), 1)
        # A transient outage is the heartbeat's own: no five-argument resend, health unreachable.
        down = Server(error=(('service_unavailable',), {'transient': True}))
        self.presence(down, run_id=RUN).tick()
        self.assertEqual(len(down.calls), 1)
        self.assertEqual(self.ledger.setting('presence')['state'], 'unreachable')
        self.assertIsNone(self.ledger.setting('peer-status-support'))

    @isolated_profiles
    def test_a_stop_after_the_missing_function_sends_nothing_more(self):
        server = Server(legacy=True)
        stops = iter([False, False, True])
        self.presence(server, run_id=RUN).tick(lambda: next(stops))
        self.assertEqual(len(server.calls), 1)
        self.assertNotEqual((self.ledger.setting('presence') or {}).get('state'), 'online')
        # The epoch intent stays pending, so the next heartbeat reuses this connection.
        self.assertTrue(self.ledger.setting('presence-epoch')['pending'])

    def test_fields_are_bounded(self):
        from quorum_presence import peer_status_fields
        fields = peer_status_fields(40, NOW + 7200, RUN.upper(), NOW + 60, now=NOW)
        self.assertEqual(fields, {'awaiting_owner': 32, 'reception_hold_until': iso(NOW + 1800),
                                  'client_run_id': RUN, 'runtime_started_at': iso(NOW)})
        self.assertEqual(peer_status_fields(-1, NOW - 1, None, None, now=NOW), {'awaiting_owner': 0})
        self.assertEqual(peer_status_fields(True, 'soon', 7, '1', now=NOW), {'awaiting_owner': 0})


@base.OWNER_PROOF
class WorkerHeartbeatTests(base.Harness):
    """The tool-inbox worker's receive reports its count, hold and run generation; no message ID."""
    def setUp(self):
        super().setUp()
        base.rollout(self.home)
        guard = unittest.mock.patch('quorum_native_inbox.wall', awaiting.Clock())
        guard.start()
        self.addCleanup(guard.stop)

    run_cli = awaiting.CodexAwaitingOwnerTests.run_cli
    offered = awaiting.CodexAwaitingOwnerTests.offered
    start = awaiting.CodexAwaitingOwnerTests.start

    def heartbeats(self, api):
        return [payload for operation, payload, _ in api.calls if operation == 'session_heartbeat']

    @isolated_profiles
    def test_parked_count_and_hold_reach_the_heartbeat_without_message_ids(self):
        api = base.Api(inbox=[base.message(awaiting.M1), base.message(awaiting.M2)])
        self.start(api)
        first = self.offered(api, awaiting.M1)
        sent = self.heartbeats(api)[-1]
        self.assertEqual(sent['awaiting_owner'], 0)
        self.assertEqual(sent['client_run_id'], first['run_id'])
        self.assertNotIn('reception_hold_until', sent)
        self.run_cli(api, 'park', awaiting.M1)
        later = self.offered(api, awaiting.M2)
        sent = self.heartbeats(api)[-1]
        self.assertEqual((sent['awaiting_owner'], sent['client_run_id']), (1, later['run_id']))
        self.assertNotEqual(first['run_id'], later['run_id'])
        code, held, text = self.run_cli(api, 'hold', awaiting.M2, '--seconds', '600')
        self.assertEqual(code, 0, text)
        self.offered(api, awaiting.M2)
        sent = self.heartbeats(api)[-1]
        self.assertIsInstance(sent['reception_hold_until'], str)
        for payload in self.heartbeats(api):
            self.assertNotIn(awaiting.M1, str(payload))
            self.assertNotIn(awaiting.M2, str(payload))

    @isolated_profiles
    def test_older_server_keeps_the_worker_receiving(self):
        class Legacy(base.Api):
            def call(self, operation, payload, request_id=None):
                from quorum_api import QuorumError
                if operation == 'session_heartbeat' and 'awaiting_owner' in payload:
                    self.calls.append((operation, dict(payload), request_id))
                    raise QuorumError('service_error', missing=True)
                return super().call(operation, payload, request_id)
        api = Legacy(inbox=[base.message(awaiting.M1)])
        self.start(api)
        self.offered(api, awaiting.M1)
        plain = [payload for payload in self.heartbeats(api) if 'awaiting_owner' not in payload]
        self.assertTrue(plain)
        self.assertFalse(STATUS_KEYS & set(plain[-1]))


class InboxStatusTests(unittest.TestCase):
    def test_peer_status_counts_only_this_binding(self):
        from quorum_native_inbox import peer_status
        from quorum_state import LocalLedger
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        ledger = LocalLedger(Path(temporary.name))
        self.addCleanup(ledger.db.close)
        record = {'agent_id': 'a', 'session_ref': 's', 'quorum_id': 'q'}
        owned = {'agent_id': 'a', 'session_ref': 's', 'quorum_id': 'q', 'message': {}}
        rows = {'delivery:1': {**owned, 'state': 'parked'}, 'delivery:2': {**owned, 'state': 'parked'},
                'delivery:3': {**owned, 'state': 'offered', 'hold_until': NOW + 60},
                'delivery:4': {**owned, 'state': 'offered', 'hold_until': NOW - 60},
                'delivery:5': {**owned, 'agent_id': 'other', 'state': 'parked'},
                'delivery:6': {**owned, 'agent_id': 'other', 'state': 'offered', 'hold_until': NOW + 900}}
        for key, value in rows.items(): ledger.setting(key, value)
        self.assertEqual(peer_status(ledger, record, now=NOW), (2, NOW + 60))
        self.assertEqual(peer_status(ledger, {**record, 'agent_id': 'none'}, now=NOW), (0, None))


def peer(**extra):
    value = {'agent_id': AGENT, 'role': 'velvil-author', 'engine': 'codex', 'availability': 'unavailable',
             'reason': 'heartbeat_timeout', 'last_seen': '2026-11-05T11:50:00+00:00'}
    return {**value, **extra}


class PeerViewTests(unittest.TestCase):
    NOW = datetime(2026, 11, 5, 12, 0, tzinfo=timezone.utc)
    REPORT = {AGENT: {'refreshed_at': '2026-11-05T11:49:30+00:00'}}

    def view(self, peers, reports=None):
        from quorum_peer_wake import with_wake_diagnostics
        return with_wake_diagnostics(peers, reports, now=self.NOW)

    def test_older_server_keeps_the_client_side_heuristic(self):
        result = self.view([peer()], self.REPORT)[0]
        self.assertEqual((result['availability'], result['server_availability']),
                         ('client_restarted_unrecovered', 'unavailable'))
        self.assertNotIn('awaiting_owner', result)

    def test_server_run_evidence_replaces_the_heuristic(self):
        # The server saw the peer's run generation and found no restart: its answer stands.
        result = self.view([peer(awaiting_owner=None, reception_hold_until=None, client_run_reported=True)],
                           self.REPORT)[0]
        self.assertEqual(result['availability'], 'unavailable')
        self.assertNotIn('server_availability', result)
        self.assertNotIn('Another run', result['wake']['next_step'])
        # The server's own restart state names its evidence and the recovery step.
        result = self.view([peer(availability='client_restarted_unrecovered', reason='run_changed_without_heartbeat',
                                 awaiting_owner=None, reception_hold_until=None, client_run_reported=True)])[0]
        self.assertEqual(result['availability'], 'client_restarted_unrecovered')
        self.assertIn('another run of its client reported it was running', result['wake']['next_step'])
        self.assertIn('worker rebind', result['wake']['next_step'])

    def test_a_peer_without_run_generation_still_uses_the_heuristic(self):
        result = self.view([peer(awaiting_owner=None, reception_hold_until=None, client_run_reported=False)],
                           self.REPORT)[0]
        self.assertEqual(result['availability'], 'client_restarted_unrecovered')
        self.assertIn('client refresh', result['wake']['next_step'])

    def test_awaiting_owner_is_alive_and_gets_no_wake(self):
        result = self.view([peer(availability='awaiting_owner', reason=None, last_seen='2026-11-05T11:59:50+00:00',
                                 awaiting_owner=2, reception_hold_until='2026-11-05T12:05:00+00:00',
                                 client_run_reported=True)])[0]
        self.assertEqual((result['availability'], result['awaiting_owner'], result['reception_hold_until']),
                         ('awaiting_owner', 2, '2026-11-05T12:05:00+00:00'))
        self.assertNotIn('wake', result)

    def test_status_fields_are_shape_checked(self):
        result = self.view([peer(availability='online', awaiting_owner=99, reception_hold_until='later',
                                 client_run_reported='yes')])[0]
        self.assertEqual((result['awaiting_owner'], result['reception_hold_until'], result['client_run_reported']),
                         (None, None, False))

    PAGE = {'operation_id': 'op-1', 'target': {'protocol_version': 2, 'skill_version': '0.3.0',
                                                'content_sha256': 'a' * 64},
            'next_after_agent': AGENT, 'unaddressable_members': 1, 'all_participants_upgraded': False}

    def joined(self, rows):
        from quorum_state import LocalLedger
        from quorum_updates import with_restart_visibility
        class Api:
            def call(self, operation, payload, request_id=None): return rows
        status = {**self.PAGE, 'participants': [{'agent_id': AGENT, 'state': 'refreshed',
                                                 'refreshed_at': '2026-11-05T11:49:30+00:00'}]}
        with tempfile.TemporaryDirectory() as root:
            ledger = LocalLedger(Path(root))
            try:
                return with_restart_visibility(Api(), ledger, {'agent_id': str(uuid.uuid4()), 'session_ref': 's'},
                                               status, lambda: None)
            finally: ledger.db.close()

    def test_update_status_page_shows_the_server_status_and_keeps_pagination(self):
        result = self.joined([peer(availability='awaiting_owner', reason=None, awaiting_owner=1,
                                   reception_hold_until=None, client_run_reported=True)])
        item = result['participants'][0]
        self.assertEqual((item['availability'], item['awaiting_owner'], item['reception_hold_until']),
                         ('awaiting_owner', 1, None))
        self.assertEqual({key: result[key] for key in self.PAGE}, self.PAGE)

    def test_older_server_page_keeps_pagination(self):
        result = self.joined([peer(availability='online', reason=None)])
        item = result['participants'][0]
        self.assertEqual(item['availability'], 'online')
        self.assertNotIn('awaiting_owner', item)
        self.assertEqual({key: result[key] for key in self.PAGE}, self.PAGE)

if __name__ == '__main__':
    unittest.main()
