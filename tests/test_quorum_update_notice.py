"""Typed update notices retain inert metadata without any implicit update action."""
from unittest.mock import patch

from quorum_test_support import isolated_profiles
from test_quorum_native_inbox import AGENT, PEER, QUORUM, Api, Harness, M1, caller, message, rollout


class UpdateNoticeTests(Harness):
    @isolated_profiles
    def test_worker_offer_preserves_maintenance_target_without_refresh_or_fanout(self):
        rollout(self.home)
        metadata = {'kind':'quorum.update', 'notice_id':M1,
                    'target':{'protocol_version':2, 'skill_version':'0.3.0', 'content_sha256':'a'*64}}
        api = Api(inbox=[message(M1, maintenance=metadata)])
        with caller(self.home), patch('quorum_update.refresh', side_effect=AssertionError('implicit installer')):
            self.connect(api)
            code, result, text = self.receive(api)
        self.assertEqual(code, 0, text)
        self.assertEqual(result['data']['message'].get('maintenance'), metadata)
        self.assertNotIn('session_update_request', api.ops())
        self.assertNotIn('session_ack', api.ops(), 'a tool result alone is not binding consumption')

    def test_maintenance_never_matches_a_pinned_coordinator_project_grant(self):
        from quorum_delegation import grant, matches
        record = {'agent_id':AGENT, 'quorum_id':QUORUM, 'project_ref':'project', 'cwd':'/synthetic'}
        record['delegation'] = grant(record, PEER, 'coordinator', dispatch_mode='native-subagent')
        ordinary = message(M1)
        self.assertTrue(matches(record, ordinary))
        self.assertFalse(matches(record, {**ordinary, 'maintenance':{'kind':'quorum.update'}}),
                         'a project task grant must not dispatch a maintenance notice')
