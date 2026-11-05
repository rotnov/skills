"""Codex tool-inbox worker rebind after a client restart: synthetic rollouts and a fake API only (#104)."""
import json
from pathlib import Path
import unittest

from quorum_test_support import isolated_profiles
from test_quorum_native_inbox import (AGENT, CHILD, M1, M2, OTHER, OWNER_PROOF, PARENT, Api, Harness, caller,
                                      message, rollout)

NEW = '01a16f00-0000-7000-8000-00000000a104'
SECOND = '01a16f00-0000-7000-8000-00000000b104'
FOREIGN_PARENT = '01a16f00-0000-7000-8000-00000000c104'


@OWNER_PROOF
class WorkerRebindTests(Harness):
    def setUp(self):
        super().setUp()
        rollout(self.home)
        rollout(self.home, sid=NEW)

    def bound(self, api):
        with caller(self.home): self.connect(api)
        ledger = self.ledger()
        return ledger, ledger.setting('session-agent')

    def rebind(self, api, sid=NEW, parent=PARENT, selector=CHILD):
        with caller(self.home, sid=sid):
            return self.cli(api, 'session', '--session-id', selector, 'recover', '--parent-thread-id', parent)

    @isolated_profiles
    def test_new_worker_keeps_agent_role_delegation_and_offered_delivery(self):
        api = Api(inbox=[message(M1), message(M2)])
        with caller(self.home):
            self.connect(api, '--accept-tasks-from', 'coordinator')
            self.assertEqual(self.receive(api)[1]['data']['message_id'], M1)
        ledger = self.ledger()
        before = ledger.setting('session-agent')
        self.assertIsNotNone(before['delegation'])
        joins = [call for call in api.calls if call[0] in ('agent_join', 'device_register', 'invite_accept_id')]
        code, result, text = self.rebind(api)
        self.assertEqual(code, 0, text)
        data = result['data']
        self.assertEqual((data['rebind'], data['reception'], data['session_id']), ('rebound', 'enabled', CHILD))
        self.assertEqual(data['worker_binding'], {'provider': 'codex', 'session_ref': NEW, 'parent_thread_id': PARENT})
        self.assertEqual((data['worker_rebind']['previous_session_ref'], data['worker_rebind']['count']), (CHILD, 1))
        after = ledger.setting('session-agent')
        for key in ('agent_id', 'session_ref', 'registration', 'role', 'quorum', 'quorum_id', 'delegation', 'device_id'):
            self.assertEqual(after.get(key), before.get(key), key)
        self.assertEqual(after['agent_id'], AGENT)
        # No new registration, device or invitation; nothing under the new worker's own UUID.
        self.assertEqual([call for call in api.calls if call[0] in ('agent_join', 'device_register', 'invite_accept_id')], joins)
        self.assertFalse((Path(self.root) / 'sessions' / NEW).exists())
        self.assertNotIn('synthetic-jwt', text)
        with caller(self.home, sid=NEW):
            code, offered, text = self.cli(api, 'session', '--session-id', CHILD, 'receive', '--wait-seconds', '0')
            self.assertEqual(code, 0, text)
            # The unreceipted offer is repeated to the new worker under the same agent scope.
            self.assertEqual(offered['data']['message_id'], M1)
            self.assertEqual(offered['data']['receipt_command'][-5:], ['session', '--session-id', CHILD, 'received', M1])
            self.assertEqual(self.cli(api, 'session', '--session-id', CHILD, 'received', M1)[0], 0)
        self.assertEqual(api.acked, [M1])
        self.assertTrue(all(call[1].get('session_ref', CHILD) == CHILD for call in api.calls))
        # The previous worker and the parent are refused afterwards; neither can receive as the agent.
        for sid in (CHILD, PARENT):
            with self.subTest(sid=sid), caller(self.home, sid=sid):
                code, refused, _ = self.cli(api, 'session', '--session-id', CHILD, 'receive', '--wait-seconds', '0')
                self.assertEqual(code, 3)
                self.assertIn(refused['code'], ('session_binding_denied', 'native_worker_binding_denied'))
        self.assertEqual(self.spawns, [])

    @isolated_profiles
    def test_same_worker_recover_is_unchanged_and_repeat_rebind_is_idempotent(self):
        api = Api()
        ledger, record = self.bound(api)
        code, result, text = self.rebind(api, sid=CHILD)
        self.assertEqual((code, result['data']['rebind']), (0, 'unchanged'), text)
        self.assertEqual(ledger.setting('session-agent'), record)
        self.assertEqual(self.rebind(api)[1]['data']['rebind'], 'rebound')
        self.assertEqual(self.rebind(api)[1]['data']['rebind'], 'unchanged')
        rollout(self.home, sid=SECOND)
        second = self.rebind(api, sid=SECOND)[1]['data']
        self.assertEqual((second['worker_rebind']['previous_session_ref'], second['worker_rebind']['count']), (NEW, 2))

    @isolated_profiles
    def test_stopped_binding_is_refused_and_not_rearmed(self):
        api = Api(inbox=[message(M1)])
        ledger, record = self.bound(api)
        with caller(self.home, sid=PARENT):
            self.assertEqual(self.cli(None, 'session', '--session-id', CHILD, 'stop')[0], 0)
        code, result, _ = self.rebind(api)
        self.assertEqual((code, result['code']), (3, 'session_recovery_denied'))
        self.assertEqual(ledger.setting('session-agent'), record)
        self.assertEqual(ledger.setting('tool-inbox-control'), {'enabled': False})

    @isolated_profiles
    def test_revoked_agent_or_membership_is_refused_without_change(self):
        from quorum_api import QuorumError
        api = Api()
        ledger, record = self.bound(api)
        denial = QuorumError('auth_required')
        denial.denial = 'agent_denied'
        api.fail['agent_status'] = [None]
        original = api.answer
        def answer(operation, payload):
            if operation == 'agent_status': raise denial
            return original(operation, payload)
        api.answer = answer
        code, result, _ = self.rebind(api)
        self.assertEqual((code, result['code']), (3, 'session_recovery_denied'))
        api.answer, api.revoked = original, True
        code, result, _ = self.rebind(api)
        self.assertEqual(code, 3)
        self.assertEqual(ledger.setting('session-agent'), record)

    @isolated_profiles
    def test_wrong_parent_parent_caller_or_unbound_worker_is_denied(self):
        api = Api()
        ledger, record = self.bound(api)
        rollout(self.home, sid=OTHER, parent=FOREIGN_PARENT)
        cases = ((OTHER, FOREIGN_PARENT, 'native_worker_binding_denied'),
                 (OTHER, PARENT, 'native_worker_binding_denied'),
                 (PARENT, PARENT, 'native_worker_binding_denied'))
        for sid, parent, code in cases:
            with self.subTest(sid=sid, parent=parent):
                status, result, _ = self.rebind(api, sid=sid, parent=parent)
                self.assertEqual((status, result['code']), (3, code))
        # Without --parent-thread-id a tool binding is never recovered as a parent chat.
        with caller(self.home, sid=NEW):
            status, result, _ = self.cli(api, 'session', '--session-id', CHILD, 'recover')
        self.assertEqual((status, result['code']), (3, 'native_worker_binding_denied'))
        self.assertEqual(ledger.setting('session-agent'), record)

    @isolated_profiles
    def test_active_previous_receive_blocks_the_rebind(self):
        from quorum_os import FileLock
        api = Api()
        ledger, record = self.bound(api)
        with FileLock(ledger.root / 'listener.lock'):
            code, result, _ = self.rebind(api)
        self.assertEqual((code, result['code']), (3, 'worker_already_running'))
        self.assertEqual(ledger.setting('session-agent'), record)

    @isolated_profiles
    def test_tampered_rebind_record_fails_closed(self):
        api = Api()
        ledger, record = self.bound(api)
        self.assertEqual(self.rebind(api)[0], 0)
        tampered = ledger.setting('session-agent')
        tampered['worker_rebind']['session_ref'] = CHILD
        ledger.setting('session-agent', tampered)
        with caller(self.home, sid=NEW):
            code, result, _ = self.cli(api, 'session', '--session-id', CHILD, 'receive', '--wait-seconds', '0')
        self.assertEqual(code, 3)
        self.assertIn(result['code'], ('session_binding_denied', 'native_worker_binding_denied'))

    @isolated_profiles
    def test_native_parent_binding_rejects_the_worker_flag(self):
        api = Api()
        self.bound(api)
        with caller(self.home, sid=OTHER):
            from unittest.mock import patch
            with patch('shutil.which', return_value='/usr/bin/codex'):
                self.assertEqual(self.cli(api, 'session', 'connect', 'team', '--role', 'native',
                                          '--project-ref', 'team/project')[0], 0)
            code, result, _ = self.cli(api, 'session', 'recover', '--parent-thread-id', PARENT)
        self.assertEqual((code, result['code']), (3, 'native_inbox_transport_conflict'))


if __name__ == '__main__':
    unittest.main()
