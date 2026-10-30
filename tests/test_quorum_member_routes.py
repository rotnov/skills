"""Member intake through the existing native/tool/cloud fixtures; no provider launch."""
import os
import sys
import unittest
from unittest.mock import patch

from quorum_test_support import isolated_profiles
import test_quorum_native_inbox as codex_cases
import test_quorum_claude_worker as claude_cases
import test_quorum_grok_cloud as cloud_cases


def response(case, payload):
    return {'authorized': True, 'reason': 'current_member', 'sender_id': case.PEER,
            'message_id': payload['message_id'], 'agent_id': case.AGENT, 'quorum_id': case.QUORUM,
            'session_ref': payload['session_ref'], 'project_ref': 'team/project'}


@unittest.skipIf(os.name == 'nt', 'native worker proof uses POSIX descriptors')
class CodexMemberRouteTests(codex_cases.Harness):
    @isolated_profiles
    def test_proven_codex_worker_keeps_its_own_member_scope_and_fresh_check(self):
        class Api(codex_cases.Api):
            def answer(self, operation, payload):
                if operation == 'session_task_authority': return response(codex_cases, payload)
                return super().answer(operation, payload)
        api = Api()
        codex_cases.rollout(self.home)
        with codex_cases.caller(self.home):
            connected = self.connect(api, '--accept-tasks-from-members')
            self.assertEqual(connected['delegation']['session_ref'], codex_cases.CHILD)
            code, result, text = self.cli(api, 'session', 'task-check', codex_cases.M1)
            self.assertEqual(code, 0, text)
            self.assertTrue(result['data']['authorized'])
            self.cli(api, 'session', 'delegate', '--revoke')
            code, result, text = self.cli(api, 'session', 'task-check', codex_cases.M1)
            self.assertFalse(result['data']['authorized'], text)


class CloudMemberRouteTests(cloud_cases.Harness):
    def setUp(self):
        super().setUp()
        modules = patch.dict(sys.modules, {'quorum_grok_webhook': cloud_cases.fake_webhook()})
        modules.start()
        self.addCleanup(modules.stop)

    @isolated_profiles
    def test_original_cloud_parent_can_grant_check_and_revoke_member_intake(self):
        class Api(cloud_cases.Api):
            def answer(self, operation, payload):
                if operation == 'session_task_authority': return response(cloud_cases, payload)
                return super().answer(operation, payload)
        api = Api()
        with cloud_cases.context():
            connected = self.connect(api, '--accept-tasks-from-members')
            self.assertEqual(connected['delegation']['scope'], 'members')
            code, result, text = self.cli(api, 'cloud', 'task-check', cloud_cases.M1)
            self.assertEqual(code, 0, text)
            self.assertTrue(result['data']['authorized'])
            self.cli(api, 'cloud', 'delegate', '--revoke')
            code, result, text = self.cli(api, 'cloud', 'task-check', cloud_cases.M1)
            self.assertFalse(result['data']['authorized'], text)
            code, result, text = self.cli(api, 'cloud', 'connect', 'team', '--role', 'grok-bot',
                                          '--project-ref', 'team/project', '--accept-tasks-from-members')
            self.assertNotEqual(code, 0, text)
            self.assertEqual(result['code'], 'delegation_scope_changed')
            self.cli(api, 'cloud', 'delegate', '--members')
            api.hooks['agent_status'] = lambda: self.cli(api, 'cloud', 'delegate', '--revoke')
            code, result, text = self.cli(api, 'cloud', 'connect', 'team', '--role', 'grok-bot',
                                          '--project-ref', 'team/project')
            self.assertNotEqual(code, 0, text)
            self.assertEqual(result['code'], 'delegation_scope_changed')
            self.assertNotIn('delegation', self.cloud_ledger().setting('cloud-agent'))


@unittest.skipIf(os.name == 'nt', 'Claude worker slots use POSIX owner proof')
class ClaudeMemberRouteTests(claude_cases.Bound):
    @isolated_profiles
    def test_member_scope_is_immutable_in_the_existing_claude_capability(self):
        class Api(claude_cases.Api):
            def answer(self, operation, payload):
                if operation == 'session_task_authority': return response(claude_cases, payload)
                return super().answer(operation, payload)
        api = Api()
        with claude_cases.claude():
            self.bound(api, scope=self.accept_scope(accept_tasks_from_members=True),
                       extra=('--accept-tasks-from-members',))
            code, result, text = self.worker(api, 'task-check', claude_cases.M1)
            self.assertEqual(code, 0, text)
            self.assertTrue(result['data']['authorized'])
            # A caller cannot replace this capability's intake scope on reconnect.
            code, result, text = self.connect(api, self.cap, '--accept-tasks-from', 'coordinator')
            self.assertEqual(code, 3, text)
            api.hooks['session_task_authority'] = self.stopper()
            code, result, text = self.worker(api, 'task-check', claude_cases.M1)
            self.assertEqual(code, 3, text)
            self.assertNotIn('authorized', result.get('data', {}))

    @isolated_profiles
    def test_legacy_claude_capability_cannot_add_member_scope_or_run_member_checks(self):
        api = claude_cases.Api()
        with claude_cases.claude():
            self.bound(api)
            code, result, text = self.connect(api, self.cap, '--accept-tasks-from-members')
            self.assertEqual(code, 3, text)
            code, result, text = self.worker(api, 'task-check', claude_cases.M1)
            self.assertEqual(code, 3, text)
            self.assertNotIn('session_task_authority', api.ops())
