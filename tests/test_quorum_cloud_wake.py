"""Peer wake guidance uses synthetic owned bindings; no provider or network."""
import copy
import sys
from unittest.mock import patch

from quorum_test_support import isolated_profiles
import test_quorum_native_inbox as native
import test_quorum_grok_cloud as cloud


PEERS = [
    {'agent_id': 'online', 'engine': 'codex', 'availability': 'online', 'reason': None},
    {'agent_id': 'unknown', 'engine': 'claude', 'availability': 'unknown', 'reason': 'heartbeat_not_supported'},
    *[{'agent_id': engine, 'engine': engine, 'role': 'cloud-looking-label', 'availability': 'unavailable',
       'reason': reason, 'last_seen': '2026-10-23T09:59:00Z'}
      for engine, reason in (('codex', 'receipt_timeout'), ('claude', 'heartbeat_timeout'), ('grok', 'receipt_timeout'))],
]


def assert_guidance(test, peers):
    test.assertEqual(peers[:2], PEERS[:2])
    for original, peer in zip(PEERS[2:], peers[2:]):
        test.assertEqual({key: value for key, value in peer.items() if key != 'wake'}, original)
        test.assertEqual(peer['wake']['status'], 'unsupported')
        test.assertEqual(peer['wake']['provider'], 'unknown')
        test.assertEqual(peer['wake']['suspension'], 'unknown')
        test.assertEqual(peer['wake']['action'], 'owner_open_original_chat')
        test.assertIn('open', peer['wake']['next_step'])


class NativeApi(native.Api):
    def answer(self, operation, payload):
        if operation == 'session_peers': return copy.deepcopy(PEERS)
        return super().answer(operation, payload)


@native.POSIX_PROOF
class NativePeersTests(native.Harness):
    @isolated_profiles
    def test_unavailable_peers_preserve_health_and_get_honest_wake_guidance(self):
        native.rollout(self.home)
        api = NativeApi()
        with native.caller(self.home):
            self.connect(api)
            before = len(api.calls)
            code, result, text = self.cli(api, 'session', 'peers')
        self.assertEqual(code, 0, text)
        assert_guidance(self, result['data'])
        self.assertTrue(all(op in ('session_requirements', 'session_inbox', 'session_peers')
                            for op, _, _ in api.calls[before:]))
        self.assertEqual(api.acked, [])
        self.assertEqual(self.spawns, [])

    @isolated_profiles
    def test_revoked_membership_does_not_disclose_peers_or_attempt_wake(self):
        native.rollout(self.home)
        api = NativeApi()
        with native.caller(self.home):
            self.connect(api)
            api.revoked = True
            before = len(api.calls)
            code, result, text = self.cli(api, 'session', 'peers')
        self.assertNotEqual(code, 0, text)
        self.assertFalse(result['ok'])
        self.assertEqual([op for op, _, _ in api.calls[before:]], ['session_peers'])
        self.assertNotIn('cloud-looking-label', text)


class CloudApi(cloud.Api):
    def answer(self, operation, payload):
        if operation == 'session_peers': return copy.deepcopy(PEERS)
        return super().answer(operation, payload)


class CloudPeersTests(cloud.Harness):
    @isolated_profiles
    def test_cloud_parent_peers_has_the_same_diagnostic_without_a_wake(self):
        api = CloudApi()
        with patch.dict(sys.modules, {'quorum_grok_webhook': cloud.fake_webhook()}), cloud.context():
            self.connect(api)
            before = len(api.calls)
            code, result, text = self.cli(api, 'cloud', 'peers')
        self.assertEqual(code, 0, text)
        assert_guidance(self, result['data'])
        self.assertTrue(all(op in ('session_requirements', 'session_inbox', 'session_peers')
                            for op, _, _ in api.calls[before:]))
        self.assertEqual(api.acked, [])
        self.assertEqual(self.spawns, [])
