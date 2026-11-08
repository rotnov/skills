"""The Claude worker's `wait` cools down on a server-side failure like the native listener (#188). Synthetic only.

The real Presence and RealtimeWait (no Realtime socket) run under a fake clock and a fake service. A generic
`service_error` or `coordination_unavailable` is a cooldown with the listener's delays: presence `unavailable` with
the code, no heartbeat, Hello or inbox call until the recheck, which resubscribes and reconciles first. The bound
still ends the wait, as a `rearm` carrying the code; an owner stop ends it within a second; a named refusal stays
fatal.
"""
import hang_guard  # noqa: F401
import json
import unittest

from quorum_test_support import isolated_profiles
from test_quorum_claude_wait import WaitCase
from test_quorum_claude_worker import M1, OWNER_PROOF, Api, claude, message


class TimedApi(Api):
    """The fake service, recording the fake-clock moment of every heartbeat and inbox read."""
    def __init__(self, test, inbox=()):
        super().__init__(inbox)
        self.test, self.times = test, []

    def call(self, operation, payload, request_id=None):
        if operation in ('session_heartbeat', 'session_inbox'): self.times.append((operation, self.test.moment))
        return super().call(operation, payload, request_id)

    def at(self, operation): return [moment for name, moment in self.times if name == operation]


@OWNER_PROOF
class WaitCooldownTests(WaitCase):
    def real_wait(self, api, seconds, on_sleep=None):
        """`wait` with the real Presence and RealtimeWait; `on_sleep` sees each one-second cooldown step."""
        from quorum_presence import Presence
        from quorum_realtime import RealtimeWait
        from quorum_state import LocalLedger
        import quorum_native_inbox as inbox
        ledger, record, check = self.direct('receive', {'wait_seconds': None})
        self.seen = []
        def sleep(step):
            self.moment += step
            self.now += step
            self.seen.append((self.moment, ledger.setting('presence'), ledger.setting('listener')['state']))
            if on_sleep: on_sleep()
        def refused(*_, **__): raise OSError('synthetic: no realtime socket')
        presence = Presence(api, record, ledger, clock=lambda: self.moment, persist_epoch=True, wall=lambda: self.now)
        waiter = RealtimeWait(api, record, ledger, connect=refused, clock=lambda: self.moment, sleep=sleep,
                              keepalive=presence, stop_aware=True)
        auth = LocalLedger(self.root)
        self.addCleanup(auth.db.close)
        with claude():
            result = inbox.wait(api, auth, ledger, record, seconds, waiter=waiter, presence=presence,
                                clock=lambda: self.moment, sleep=sleep, authorize=check)
        return result, ledger

    def cooling(self, until, code='coordination_unavailable'):
        """Each step of the cooldown before `until` reported degraded with presence unavailable and the code."""
        steps = [(presence, state) for moment, presence, state in self.seen if moment < until]
        self.assertTrue(steps)
        self.assertEqual({json.dumps(step, sort_keys=True) for step in steps},
                         {json.dumps(({'state': 'unavailable', 'code': code}, 'degraded'), sort_keys=True)})

    @isolated_profiles
    def test_a_generic_heartbeat_failure_cools_down_then_offers_the_message_once(self):
        api = TimedApi(self, inbox=[message(M1, body='private peer body')])
        self.bound(api)
        api.times.clear()
        api.fail['session_heartbeat'] = ['service_error', 'service_error', None]
        result, ledger = self.real_wait(api, 1680)
        envelope = self.fixed(result, 'offer_available', 'handle_then_wait')
        self.assertEqual((envelope['message_id'], envelope['message']['body']), (M1, 'private peer body'))
        # The listener's 60 and 300 second delays: no heartbeat, so no Hello, and no inbox read in between.
        self.assertEqual(api.at('session_heartbeat'), [0, 60, 360])
        self.assertEqual(api.at('session_inbox'), [360], 'reconciled only after the cooldown')
        self.cooling(360)
        self.assertEqual(ledger.setting('presence')['state'], 'online')
        self.assertEqual(ledger.setting('delivery:' + M1)['state'], 'offered')
        self.assertEqual(api.acked, [], 'a disclosure is not a receipt')
        # Exactly once: received once, the next wait has nothing to offer.
        code, _, text = self.worker(api, 'received', M1)
        self.assertEqual(code, 0, text)
        self.assertEqual(api.acked, [M1])
        result, _ = self.real_wait(api, 120)
        self.fixed(result, 'rearm', 'wait')

    @isolated_profiles
    def test_a_generic_inbox_failure_cools_down_with_its_own_code_then_resumes(self):
        api = TimedApi(self, inbox=[message(M1)])
        self.bound(api)
        api.times.clear()
        api.fail['session_inbox'] = ['service_error', 'service_error', None]
        result, _ = self.real_wait(api, 1680)
        self.assertEqual(self.fixed(result, 'offer_available', 'handle_then_wait')['message_id'], M1)
        self.assertEqual(api.at('session_inbox'), [0, 60, 360])
        self.cooling(360, 'service_error')

    @isolated_profiles
    def test_the_bound_ending_during_a_cooldown_is_a_rearm_with_the_code(self):
        import quorum_native_inbox as inbox
        api = TimedApi(self)
        self.bound(api)
        api.times.clear()
        api.fail['session_heartbeat'] = ['service_error'] * 10
        result, ledger = self.real_wait(api, 300)
        self.fixed(result, 'rearm', 'wait', code='coordination_unavailable')
        self.assertEqual(self.moment, 300, 'the bound holds inside the 300 second step')
        self.assertEqual(api.at('session_heartbeat'), [0, 60])
        self.assertEqual(api.at('session_inbox'), [])
        self.assertEqual(ledger.setting('presence'), {'state': 'unavailable', 'code': 'coordination_unavailable'})
        line, _ = inbox.wake_line(result)
        self.assertLessEqual(len(line), inbox.WAKE_LIMIT)
        self.assertEqual(json.loads(line)['data'], {'state': 'rearm', 'next_step': 'wait', 'continue': True,
                                                    'awaiting_owner': 0, 'code': 'coordination_unavailable',
                                                    'full': False})

    @isolated_profiles
    def test_the_backoff_is_the_listeners(self):
        from quorum_listener import COOLDOWN_DELAYS
        api = TimedApi(self)
        self.bound(api, self.accept_scope(lifetime='until-owner-stop'))  # Outlives the default deadline.
        api.times.clear()
        api.fail['session_heartbeat'] = ['service_error'] * 10
        result, _ = self.real_wait(api, 1680)
        self.fixed(result, 'rearm', 'wait', code='coordination_unavailable')
        beats = api.at('session_heartbeat')
        self.assertEqual([later - earlier for earlier, later in zip(beats, beats[1:])], list(COOLDOWN_DELAYS[:3]))

    @isolated_profiles
    def test_an_owner_stop_ends_a_cooldown_within_a_second(self):
        api = TimedApi(self)
        self.bound(api)
        api.times.clear()
        api.fail['session_heartbeat'] = ['service_error'] * 10
        stop = self.stopper()
        def on_sleep():
            if self.moment == 30: stop()
        result, _ = self.real_wait(api, 1680, on_sleep)
        self.fixed(result, 'stopped', 'report_stop', False, stop_reason='owner_stop')
        self.assertEqual(self.moment, 30)
        self.assertEqual(api.at('session_heartbeat'), [0])

    def refused(self, name):
        from quorum_api import QuorumError
        api = TimedApi(self, inbox=[message(M1)])
        self.bound(api)
        api.times.clear()
        api.fail['session_heartbeat'] = [name]
        with self.assertRaises(QuorumError) as raised:
            self.real_wait(api, 1680)
        self.assertEqual(str(raised.exception), name)
        self.assertEqual(api.at('session_inbox'), [], 'no cooldown and no inbox call')
        self.assertNotIn('degraded', [state for _, _, state in self.seen])

    @isolated_profiles
    def test_a_named_heartbeat_refusal_stays_fatal(self):
        self.refused('invalid_peer_status')

    @isolated_profiles
    def test_a_hard_terminal_code_stays_fatal(self):
        self.refused('membership_revoked')

    @isolated_profiles
    def test_a_named_inbox_refusal_exits_the_cli_with_its_code(self):
        from unittest.mock import patch
        from test_quorum_claude_worker import StubWaiter
        api = Api()
        self.bound(api)
        api.fail['session_inbox'] = ['invalid_peer_status']
        with patch('quorum_native_inbox.RealtimeWait', lambda *_, **__: StubWaiter()), \
                patch('quorum_native_inbox.pause') as pause:
            code, result, _ = self.worker(api, 'wait', '--max-seconds', '60')
        self.assertNotEqual(code, 0)
        self.assertEqual(result['code'], 'invalid_peer_status')
        self.assertEqual(pause.call_count, 0)


if __name__ == '__main__':
    unittest.main()
