"""Codex tool-inbox worker rebind after a client restart: synthetic rollouts and a fake API only (#104)."""
import json
from pathlib import Path
import unittest

from quorum_test_support import isolated_profiles
from test_quorum_native_inbox import (AGENT, CHILD, M1, M2, OTHER, OWNER_PROOF, PARENT, Api, Harness, OtherConnection, caller,
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

    def rebind_during(self, api, ledger, record, operation='session_requirements'):
        """Complete a real rebind by the new worker while the previous worker's command awaits `operation`."""
        from quorum_native_inbox import rebind
        def concurrent():
            with OtherConnection(ledger.root) as other, caller(self.home, sid=NEW):
                rebind(Api(), other, record, PARENT)
        api.hooks[operation] = concurrent

    @isolated_profiles
    def test_rebind_after_the_previous_workers_proof_refuses_its_receive_without_disclosure(self):
        api = Api(inbox=[message(M1, body='private-peer-body')])
        ledger, record = self.bound(api)
        self.rebind_during(api, ledger, record)
        with caller(self.home):
            mark = len(api.calls)
            code, result, text = self.receive(api)
        self.assertNotIn('session_requirements', api.hooks, 'the rebind ran inside the old receive')
        self.assertEqual((code, result['code']), (3, 'native_worker_binding_denied'), text)
        self.assertNotIn('private-peer-body', text)
        self.assertNotIn(M1, text)
        self.assertNotIn('session_inbox', [call[0] for call in api.calls[mark:]])
        self.assertIsNone(ledger.setting('delivery:' + M1))
        # The replacement worker receives the message.
        with caller(self.home, sid=NEW):
            code, offered, text = self.cli(api, 'session', '--session-id', CHILD, 'receive', '--wait-seconds', '0')
        self.assertEqual((code, offered['data']['message_id']), (0, M1), text)

    @isolated_profiles
    def test_rebind_after_the_previous_workers_proof_refuses_its_receipt_reply_and_send(self):
        api = Api(inbox=[message(M1)])
        ledger, record = self.bound(api)
        with caller(self.home):
            self.assertEqual(self.receive(api)[1]['data']['message_id'], M1)
        commands = ((('received', M1), b''), (('reply', M1, '--text-stdin'), b'answer'),
                    (('send', '--to-role', 'peer', '--request-id', 'r-1', '--text-stdin'), b'hello'))
        for argv, stdin in commands:
            with self.subTest(command=argv[0]):
                record = ledger.setting('session-agent')
                if record['worker_binding']['session_ref'] != CHILD:
                    # Return the binding to the previous worker for the next race.
                    with caller(self.home, sid=CHILD):
                        self.assertEqual(self.rebind(api, sid=CHILD)[0], 0)
                    record = ledger.setting('session-agent')
                self.rebind_during(api, ledger, record)
                mark = len(api.calls)
                with caller(self.home):
                    code, result, text = self.cli(api, 'session', *argv, stdin=stdin)
                self.assertNotIn('session_requirements', api.hooks)
                self.assertEqual((code, result['code']), (3, 'native_worker_binding_denied'), text)
                sent = [call[0] for call in api.calls[mark:]]
                for operation in ('session_ack', 'session_reply', 'message_send', 'session_inbox'):
                    self.assertNotIn(operation, sent)
        self.assertEqual(api.acked, [])
        self.assertEqual(ledger.setting('delivery:' + M1)['state'], 'offered')

    @isolated_profiles
    def test_superseded_start_cannot_rearm_after_rebind_and_owner_stop(self):
        from quorum_native_inbox import rebind, stop
        api = Api(inbox=[message(M1)])
        ledger, record = self.bound(api)
        def rebind_then_stop():
            with OtherConnection(ledger.root) as other:
                with caller(self.home, sid=NEW): rebind(Api(), other, record, PARENT)
                stop(other, other.setting('session-agent'))
        api.hooks['session_requirements'] = rebind_then_stop
        with caller(self.home):
            code, result, text = self.cli(api, 'session', 'start')
        self.assertNotIn('session_requirements', api.hooks, 'the rebind and stop ran inside the old start')
        self.assertEqual((code, result['code']), (3, 'native_worker_binding_denied'), text)
        self.assertEqual(ledger.setting('tool-inbox-control'), {'enabled': False})
        self.assertEqual(ledger.setting('session-agent')['worker_binding']['session_ref'], NEW)
        with caller(self.home, sid=NEW):
            code, stopped, _ = self.cli(api, 'session', '--session-id', CHILD, 'receive', '--wait-seconds', '0')
        self.assertEqual((code, stopped['data']['state']), (0, 'stopped'))

    @isolated_profiles
    def test_superseded_delegate_task_check_and_peers_are_refused(self):
        class PeerApi(Api):
            def answer(self, operation, payload):
                if operation == 'session_peers': return [{'agent_id': AGENT, 'role': 'private-peer-role'}]
                return super().answer(operation, payload)
        cases = (('delegate', ('delegate', '--coordinator-role', 'coordinator'), 'resolve_role'),
                 ('task-check', ('task-check', M1), 'session_requirements'),
                 ('peers', ('peers',), 'session_peers'))
        for name, argv, operation in cases:
            with self.subTest(command=name):
                self.setUp()
                api = PeerApi()
                ledger, record = self.bound(api)
                self.rebind_during(api, ledger, record, operation)
                with caller(self.home):
                    code, result, text = self.cli(api, 'session', *argv)
                self.assertNotIn(operation, api.hooks, 'the rebind ran inside the old command')
                self.assertEqual((code, result['code']), (3, 'native_worker_binding_denied'), text)
                self.assertNotIn('private-peer-role', text)
                self.assertNotIn('session_task_authority', api.ops())
                latest = ledger.setting('session-agent')
                self.assertEqual(latest['worker_binding']['session_ref'], NEW)
                self.assertEqual(latest.get('delegation'), record.get('delegation'))

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
