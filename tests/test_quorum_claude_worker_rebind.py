"""Claude worker rebind after a parent chat restart (#122), adopt grant inheritance and member opt-ins.

Fake clocks, local fixtures and a fake API only: no provider, model, transcript, service, auth or network call.
"""
import json
from pathlib import Path
from unittest.mock import patch
import uuid

from quorum_test_support import isolated_profiles
from test_quorum_claude_worker import (AGENT, HANDLE, M1, M2, OTHER_SID, OWNER_PROOF, PARENT_AGENT, QUORUM, SID,
                                       Bound, OwnerApi, actor, actor_directory, claude, message)

NEW_HANDLE = 'agent_02 quorum after restart'
THIRD_HANDLE = 'agent_03 quorum after second restart'


def revoked(_payload):
    from quorum_api import QuorumError
    return QuorumError('auth_required', denial='agent_denied')


@OWNER_PROOF
class RebindTests(Bound):
    """`session worker rebind <slot>`: the same agent and binding move to a new native handle of the same chat."""

    def members(self, api, **changes):
        scope = self.accept_scope(accept_tasks_from_members=True, **changes)
        return self.bound(api, scope, extra=('--accept-tasks-from-members',))

    def rebind(self, api, slot, *extra, sid=SID):
        return self.cli(api, 'session', 'worker', 'rebind', slot, *extra, sid=sid)

    def restart(self, api, slot=None, handle=NEW_HANDLE, *extra):
        """The restarted chat's rebind and the new worker's attestation; returns the successor and its file."""
        code, result, text = self.rebind(api, slot or self.slot, *extra)
        self.assertEqual(code, 0, text)
        successor = result['data']['slot_id']
        self.attest(successor, handle)
        return successor, self.capability(successor)

    def as_worker(self, slot, cap):
        self.slot, self.cap = slot, cap

    def refused(self, result, code, reason):
        self.assertEqual((result[0], result[1]['code'], result[1]['data'].get('reason')), (3, code, reason), result[2])

    def ops(self, api, name):
        return api.ops().count(name)

    @isolated_profiles
    def test_rebind_keeps_agent_binding_grant_and_queued_offer_with_no_new_registration(self):
        api = OwnerApi(inbox=[message(M1), message(M2)])
        old, old_cap = self.members(api)
        first = self.offer(api)
        self.assertEqual(first['message']['id'], M1)
        joins, devices = self.ops(api, 'agent_join'), self.ops(api, 'device_register')
        accepts = self.ops(api, 'invite_accept_id')
        # The restarted chat (same session ID) rebinds: one authenticated liveness read, nothing registered.
        code, result, text = self.rebind(api, old)
        self.assertEqual(code, 0, text)
        data = result['data']
        self.assertEqual((data['state'], data['rebind']), ('pending', {'previous_slot_id': old, 'session_ref': actor(),
                                                                       'agent_id': AGENT, 'count': 1}))
        self.assertEqual(data['permission_expires_at'], self.setting(old)['permission_expires_at'], 'never extended')
        self.assertIn('--accept-tasks-from-members', data['connect_flags'])
        self.assertEqual(api.calls[-1][0], 'agent_status')
        self.assertNotIn('token', text)
        # Until the new handle is attested the old one still serves: no window without a holder.
        self.assertEqual(self.setting(old)['state'], 'active')
        successor = data['slot_id']
        bound = self.attest(successor, NEW_HANDLE)
        self.assertEqual((bound['session_ref'], bound['logical_actor_id'], bound['native_worker_handle']),
                         (actor(), actor()[len('claude-worker:'):], NEW_HANDLE))
        self.assertEqual(self.setting(old)['terminal_reason'], 'rebound')
        self.assertEqual(self.setting(old)['rebound_to'], successor)
        new_cap = self.capability(successor)
        self.assertNotEqual(new_cap, old_cap)
        # The new worker receives right away, as the same agent, with the same member grant and offer.
        self.as_worker(successor, new_cap)
        offered = self.offer(api)
        self.assertEqual((offered['message']['id'], offered['session_ref']), (M1, actor()))
        self.assertIn('task-check ' + M1, offered['notice'])
        self.assertNotIn('No matching local owner delegation', offered['notice'])
        self.assertIn(str(new_cap), offered['receipt_command'])
        code, result, text = self.worker(api, 'received', M1)
        self.assertEqual(code, 0, text)
        self.assertEqual(self.offer(api)['message']['id'], M2)
        record = self.worker_ledger(successor).setting('session-agent')
        self.assertEqual((record['agent_id'], record['session_ref'], record['delegation']['scope']),
                         (AGENT, actor(), 'members'))
        self.assertEqual(record['worker_binding']['slot_id'], successor)
        self.assertEqual(record['claude_worker_rebind']['previous_slot_id'], old)
        # No invitation, device or agent was registered for the rebind.
        self.assertEqual((self.ops(api, 'agent_join'), self.ops(api, 'device_register'),
                          self.ops(api, 'invite_accept_id')), (joins, devices, accepts))
        # The old handle is refused before any service call; its status names the successor.
        before = len(api.calls)
        self.as_worker(old, old_cap)
        self.refused(self.receive(api), 'native_worker_slot_terminal', 'rebound')
        self.refused(self.worker(api, 'received', M2), 'native_worker_slot_terminal', 'rebound')
        self.refused(self.worker(api, 'stop'), 'native_worker_slot_terminal', 'rebound')
        self.assertEqual(len(api.calls), before)
        code, result, text = self.cli(api, 'session', 'worker', 'status', old)
        self.assertEqual((result['data']['state'], result['data']['rebound_to']), ('terminal', successor), text)
        # The logical selector follows the successor.
        code, result, text = self.cli(api, 'session', '--session-id', actor(), 'status')
        self.assertEqual(result['data']['slot_id'], successor, text)
        # A reconnect of the new worker keeps the same agent and session reference.
        self.as_worker(successor, new_cap)
        code, result, text = self.connect(api, new_cap, '--accept-tasks-from-members')
        self.assertEqual(code, 0, text)
        self.assertEqual((result['data']['agent_id'], result['data']['session_ref']), (AGENT, actor()))

    @isolated_profiles
    def test_old_handle_is_refused_in_flight_and_a_running_receive_blocks_the_hand_over(self):
        from quorum_native_inbox import NativeInboxError
        from quorum_os import FileLock
        api = OwnerApi(inbox=[message(M1)])
        old, old_cap = self.members(api)
        # A command of the old handle that passed its proof before the rebind is refused at its next check.
        _, _, check = self.direct('receive', {'wait_seconds': 0})
        code, result, text = self.rebind(api, old)
        self.assertEqual(code, 0, text)
        successor = result['data']['slot_id']
        actor_root = self.root / 'sessions' / actor_directory(actor())
        with FileLock(actor_root / 'listener.lock'):
            # The previous worker is mid-receive: nothing is handed over.
            with self.assertRaises(ValueError) as raised:
                self.attest(successor, NEW_HANDLE)
            self.assertEqual(str(raised.exception), 'worker_already_running')
            self.assertEqual(self.setting(old)['state'], 'active')
            self.assertEqual(self.setting(successor)['state'], 'pending')
            check()
        self.attest(successor, NEW_HANDLE)
        with self.assertRaises(NativeInboxError) as raised:
            check()
        self.assertEqual((raised.exception.code, raised.exception.reason), ('native_worker_slot_terminal', 'rebound'))
        # The binding guard's second half: the persisted actor binding no longer names the old handle.
        import quorum_claude_worker as worker
        old_context = {'binding': self.setting(old)['binding'], 'capability_file': str(old_cap),
                       'session_ref': actor(), 'scope': self.setting(old)['scope']}
        record = self.worker_ledger(successor).setting('session-agent')
        with self.assertRaises(NativeInboxError) as raised:
            worker._record_matches(record, old_context)
        self.assertEqual(raised.exception.reason, 'binding_invalid')

    @isolated_profiles
    def test_refused_for_another_session_stopped_revoked_expired_or_unconnected_slots(self):
        import quorum_claude_worker as worker
        api = OwnerApi()
        old, _ = self.members(api)
        slots = lambda: sorted(path.name for path in (self.root / 'claude-workers').iterdir())
        before = slots()
        # Another parent chat (session ID) cannot rebind this slot.
        self.refused(self.rebind(api, old, sid=OTHER_SID), 'native_worker_binding_denied', 'session_mismatch')
        # A revoked agent or membership is refused after the authenticated read; nothing is prepared.
        api.answers['agent_status'] = revoked
        self.refused(self.rebind(api, old), 'native_worker_binding_denied', 'agent_revoked')
        del api.answers['agent_status']
        self.assertEqual(slots(), before)
        # Expired permission.
        self.now = self.setting(old)['permission_expires_at'] + 1
        self.refused(self.rebind(api, old), 'native_worker_slot_terminal', 'permission_expired')
        self.assertEqual(slots(), before)
        # A stopped slot stays stopped.
        api = OwnerApi()
        old, _ = self.bound(api, self.accept_scope(lifetime='until-owner-stop'), handle='agent_04 quorum')
        with claude(): worker.stop_slot(self.root, old)
        calls = len(api.calls)
        self.refused(self.rebind(api, old), 'native_worker_slot_terminal', 'stopped')
        self.assertEqual(len(api.calls), calls, 'refused before any service call')
        # An attested slot that never connected has nothing to hand over.
        api = OwnerApi()
        fresh, _, _ = self.joined(api, self.accept_scope(lifetime='until-owner-stop'), connect=False,
                                  handle='agent_05 quorum')
        self.refused(self.rebind(api, fresh), 'native_worker_binding_denied', 'target_pending')
        # A pending slot is not a binding.
        pending = self.prepare(self.accept_scope())['slot_id']
        self.refused(self.rebind(api, pending), 'native_worker_binding_denied', 'slot_pending')

    @isolated_profiles
    def test_stopped_reception_and_a_stop_during_the_hand_over_are_refused(self):
        import quorum_claude_worker as worker
        api = OwnerApi()
        old, _ = self.members(api)
        code, result, text = self.rebind(api, old)
        self.assertEqual(code, 0, text)
        successor = result['data']['slot_id']
        # The owner stops the old slot before the new handle is attested: the hand-over is refused.
        with claude(): worker.stop_slot(self.root, old)
        error = self.denied('native_worker_slot_terminal', self.attest, successor, NEW_HANDLE)
        self.assertEqual(error.reason, 'stopped')
        self.assertEqual(self.setting(successor)['state'], 'pending')
        # A worker binding whose reception was disabled (without a slot stop) is not rebound either.
        api = OwnerApi()
        old, _ = self.bound(api, self.accept_scope(lifetime='until-owner-stop'), handle='agent_06 quorum')
        from quorum_native_inbox import CONTROL
        self.worker_ledger(old).setting(CONTROL, {'enabled': False})
        self.refused(self.rebind(api, old), 'native_worker_binding_denied', 'reception_stopped')

    @isolated_profiles
    def test_interrupted_hand_over_is_completed_by_a_retry_or_repeated_after_it_lapses(self):
        import quorum_claude_worker as worker
        api = OwnerApi(inbox=[message(M1)])
        old, old_cap = self.members(api, lifetime='until-owner-stop')
        code, result, text = self.rebind(api, old)
        successor = result['data']['slot_id']
        real, calls = worker._actor_binding, []
        def crash(*args):
            calls.append(args)
            if len(calls) == 2: raise OSError('crash before the actor ledger names the new binding')
            return real(*args)
        with patch('quorum_claude_worker._actor_binding', side_effect=crash):
            with self.assertRaises(OSError):
                self.attest(successor, NEW_HANDLE)
        # The old handle is already excluded; the successor is still pending, so nothing can act.
        self.assertEqual(self.setting(old)['terminal_reason'], 'rebound')
        self.assertEqual(self.setting(successor)['state'], 'pending')
        self.as_worker(old, old_cap)
        self.refused(self.receive(api), 'native_worker_slot_terminal', 'rebound')
        self.refused(self.rebind(api, old), 'native_worker_binding_denied', 'rebind_pending')
        # An identical retry completes the hand-over.
        self.attest(successor, NEW_HANDLE)
        self.as_worker(successor, self.capability(successor))
        self.assertEqual(self.offer(api)['message']['id'], M1)
        # A crash after the predecessor was superseded whose successor then lapses (pending expiry): a new
        # rebind from the superseded slot repeats the hand-over.
        second = self.rebind(api, successor)[1]['data']['slot_id']
        calls.clear()
        with patch('quorum_claude_worker._actor_binding', side_effect=crash):
            with self.assertRaises(OSError): self.attest(second, THIRD_HANDLE)
        self.assertEqual(self.setting(successor)['rebound_to'], second)
        self.now += 121
        self.denied('native_worker_slot_terminal', self.attest, second, THIRD_HANDLE)
        code, result, text = self.rebind(api, successor)
        self.assertEqual((code, result['data']['rebind']['previous_slot_id']), (0, successor), text)
        third = result['data']['slot_id']
        self.attest(third, THIRD_HANDLE)
        self.assertEqual(self.setting(successor)['rebound_to'], third)
        self.as_worker(third, self.capability(third))
        self.assertEqual(self.offer(api)['message']['id'], M1)
        self.as_worker(second, self.capability(second))
        self.refused(self.receive(api), 'native_worker_slot_terminal', 'pending_expired')

    @isolated_profiles
    def test_a_rebind_during_the_old_handles_reconnect_keeps_the_successor_binding(self):
        import quorum_claude_worker as worker
        api = OwnerApi(inbox=[message(M1)])
        old, old_cap = self.members(api, lifetime='until-owner-stop')
        import quorum_delegation
        landed, real = {}, quorum_delegation.member_grant
        def rebind_now(*args, **kwargs):
            # The window after the old connect's last service check and before its record write.
            with claude():
                landed['slot'] = worker.rebind(self.root, old)['slot_id']
                worker.attest(self.root, landed['slot'], NEW_HANDLE)
            return real(*args, **kwargs)
        with patch('quorum_delegation.member_grant', side_effect=rebind_now):
            code, result, text = self.connect(api, old_cap, '--accept-tasks-from-members')
        self.assertEqual((code, result['code']), (3, 'native_worker_slot_terminal'), text)
        record = self.worker_ledger(landed['slot']).setting('session-agent')
        self.assertEqual(record['worker_binding']['slot_id'], landed['slot'], 'never written back by the old handle')
        self.as_worker(landed['slot'], self.capability(landed['slot']))
        self.assertEqual(self.offer(api)['message']['id'], M1)

    @isolated_profiles
    def test_an_owner_stop_of_an_unfinished_hand_over_stays_a_stop(self):
        import quorum_claude_worker as worker
        api = OwnerApi(inbox=[message(M1)])
        old, _ = self.members(api, lifetime='until-owner-stop')
        successor = self.rebind(api, old)[1]['data']['slot_id']
        real, calls = worker._actor_binding, []
        def crash(*args):
            calls.append(args)
            if len(calls) == 2: raise OSError('crash')
            return real(*args)
        with patch('quorum_claude_worker._actor_binding', side_effect=crash):
            with self.assertRaises(OSError): self.attest(successor, NEW_HANDLE)
        # Stopping the unfinished successor (it speaks for the actor) also stops the agent's reception, and the
        # hand-over can then be neither completed nor repeated.
        code, result, text = self.cli(api, 'session', 'worker', 'stop', successor)
        self.assertEqual(code, 0, text)
        from quorum_native_inbox import CONTROL
        self.assertFalse(self.worker_ledger(old).setting(CONTROL)['enabled'])
        self.refused(self.rebind(api, old), 'native_worker_slot_terminal', 'stopped')
        self.denied('native_worker_slot_terminal', self.attest, successor, NEW_HANDLE)
        # The same through the logical selector, which still names the superseded slot.
        api = OwnerApi()
        old, _ = self.bound(api, self.accept_scope(lifetime='until-owner-stop'), handle='agent_07 quorum')
        successor = self.rebind(api, old)[1]['data']['slot_id']
        calls.clear()
        with patch('quorum_claude_worker._actor_binding', side_effect=crash):
            with self.assertRaises(OSError): self.attest(successor, NEW_HANDLE + ' 2')
        code, result, text = self.cli(api, 'session', '--session-id', actor('agent_07 quorum'), 'stop')
        self.assertEqual(code, 0, text)
        self.assertFalse(self.worker_ledger(old).setting(CONTROL)['enabled'])
        error = self.denied('native_worker_binding_denied', self.attest, successor, NEW_HANDLE + ' 2')
        self.assertEqual(error.reason, 'reception_stopped')

    @isolated_profiles
    def test_repeated_restarts_chain_and_stopping_a_superseded_slot_leaves_the_successor_running(self):
        import quorum_claude_worker as worker
        api = OwnerApi(inbox=[message(M1)])
        first, _ = self.members(api, lifetime='until-owner-stop')
        second, _ = self.restart(api, first)
        # Naming the oldest slot follows the chain to the current binding.
        code, result, text = self.rebind(api, first)
        self.assertEqual(code, 0, text)
        self.assertEqual(result['data']['rebind']['previous_slot_id'], second)
        self.assertEqual(result['data']['rebind']['count'], 2)
        third = result['data']['slot_id']
        # The original handle may serve again after a restart (same derived actor).
        self.attest(third, HANDLE)
        self.as_worker(third, self.capability(third))
        self.assertEqual(self.offer(api)['message']['id'], M1)
        # Stopping a superseded slot never stops the successor's reception, and cannot retire its agent.
        with claude(): stopped = worker.stop_slot(self.root, second)
        self.assertEqual(stopped['rebound_to'], third)
        self.assertEqual(self.offer(api)['message']['id'], M1)
        code, result, text = self.cli(api, 'session', 'worker', 'stop', second, '--leave')
        self.assertEqual(code, 3, text)
        self.assertNotIn('agent_leave', api.ops())
        # Stopping the current slot stops the agent's reception; it can then no longer be rebound.
        with claude(): worker.stop_slot(self.root, third)
        self.refused(self.rebind(api, first), 'native_worker_slot_terminal', 'stopped')

    @isolated_profiles
    def test_rebind_adds_member_knowledge_and_update_only_on_owner_opt_in(self):
        api = OwnerApi()
        old, _ = self.bound(api, self.accept_scope(lifetime='until-owner-stop'))  # Legacy: no knowledge or update.
        search = json.dumps({'space_id': str(uuid.uuid4()), 'query': 'deploy'}).encode()
        code, result, text = self.cli(api, 'knowledge', 'search', '--json-stdin', '--worker-capability',
                                      str(self.cap), stdin=search)
        self.assertEqual((code, result['data'].get('reason')), (3, 'operation_denied'), text)
        successor, cap = self.restart(api, old, NEW_HANDLE, '--knowledge', 'search', '--knowledge', 'read',
                                      '--update-maintenance')
        scope = self.setting(successor)['scope']
        self.assertEqual((scope['knowledge'], scope['update']), (['search', 'read'], True))
        self.assertEqual(scope['operations'][-2:], ['knowledge', 'update'])
        old_scope = self.setting(old)['scope']
        self.assertEqual({key: value for key, value in scope.items() if key not in ('knowledge', 'update', 'operations')},
                         {key: value for key, value in old_scope.items() if key != 'operations'})
        code, result, text = self.cli(api, 'knowledge', 'search', '--json-stdin', '--worker-capability', str(cap),
                                      stdin=search)
        self.assertEqual(code, 0, text)
        self.assertEqual((api.calls[-1][0], api.calls[-1][1]['agent_id']), ('knowledge_search', AGENT))
        status = str(uuid.uuid4())
        code, result, text = self.cli(api, 'update', 'team', '--status', status, '--worker-capability', str(cap))
        self.assertEqual(code, 0, text)
        self.assertEqual(api.calls[-1][1]['agent_id'], AGENT)
        # A plain rebind keeps the scope exactly.
        plain = self.rebind(api, successor)[1]['data']['slot_id']
        self.assertEqual(self.setting(plain)['scope'], scope)
        # Only the member subset can be added; anything else is refused before a slot is written.
        import quorum_claude_worker as worker
        with claude():
            error = self.denied('native_worker_binding_denied', worker.rebind, self.root, successor,
                                knowledge=['verify'])
        self.assertEqual(error.reason, 'scope_invalid')


@OWNER_PROOF
class AcceptParityTests(Bound):
    """Create/accept workers may opt in to the member knowledge subset and `update` (#115, #122)."""

    @isolated_profiles
    def test_accept_worker_with_knowledge_and_update(self):
        import quorum_claude_worker as worker
        api = OwnerApi()
        self.bound(api, self.accept_scope(knowledge=['search', 'read', 'propose'], update=True))
        scope = self.setting(self.slot)['scope']
        self.assertEqual(scope['operations'], [*worker.OPERATIONS['accept'], 'knowledge', 'update'])
        space = str(uuid.uuid4())
        code, result, text = self.cli(api, 'knowledge', 'read', '--json-stdin', '--worker-capability', str(self.cap),
                                      stdin=json.dumps({'space_id': space, 'entry_id': str(uuid.uuid4())}).encode())
        self.assertEqual(code, 0, text)
        self.assertEqual((api.calls[-1][0], api.calls[-1][1]['session_ref']), ('knowledge_read', actor()))
        status = str(uuid.uuid4())
        code, result, text = self.cli(api, 'update', 'team', '--status', status, '--worker-capability', str(self.cap))
        self.assertEqual(code, 0, text)
        self.assertEqual(api.calls[-1][0], 'session_update_status')
        # Owner-only knowledge stays refused: the member subset never becomes owner maintenance.
        before = len(api.calls)
        code, result, text = self.cli(api, 'knowledge', 'admin', '--json-stdin', '--owner-action', '--request-id',
                                      'admin-1', '--worker-capability', str(self.cap),
                                      stdin=json.dumps({'operation': 'create',
                                                        'payload': {'space_id': space, 'name': 'Team'}}).encode())
        self.assertEqual((code, result['data'].get('reason')), (3, 'operation_denied'), text)
        self.assertEqual(len(api.calls), before)

    def test_update_opt_in_is_strict_and_omission_keeps_the_legacy_hash(self):
        import quorum_claude_worker as worker
        legacy = worker.normalize_scope(self.accept_scope())
        self.assertEqual(worker.normalize_scope(self.accept_scope(update=False)), legacy)
        self.assertNotIn('update', legacy['operations'])
        for value in ('true', 1, None, []):
            with self.subTest(value=value):
                error = self.denied('native_worker_binding_denied', worker.normalize_scope,
                                    {**self.accept_scope(), 'update': value})
                self.assertEqual(error.reason, 'scope_invalid')
        adopt = self.scope(mode='adopt', update=True)
        adopt.pop('access')
        self.assertEqual(self.denied('native_worker_binding_denied', worker.normalize_scope, adopt).reason,
                         'scope_invalid')
        self.assertIn('update', worker.normalize_scope(self.scope(update=True))['operations'])

    @isolated_profiles
    def test_legacy_accept_worker_has_no_update(self):
        api = OwnerApi()
        self.bound(api)
        code, result, text = self.cli(api, 'update', 'team', '--status', str(uuid.uuid4()),
                                      '--worker-capability', str(self.cap))
        self.assertEqual((code, result['data'].get('reason')), (3, 'operation_denied'), text)


@OWNER_PROOF
class AdoptGrantTests(Bound):
    """An adopt worker's first envelope sees the member grant the parent's connect stored (#122)."""

    def adopt_scope(self, **changes):
        value = self.scope(mode='adopt')
        value.pop('access')
        value.update(changes)
        return value

    def parent(self, delegation=None):
        from quorum_delegation import member_grant
        from quorum_state import LocalLedger
        for directory in (self.root, self.root / 'sessions'):
            if not directory.exists(): directory.mkdir(mode=0o700)
        record = {'engine': 'claude', 'session_ref': SID, 'project_ref': 'team/project', 'cwd': str(self.project),
                  'role': 'service-worker', 'quorum': 'team', 'quorum_id': QUORUM, 'agent_id': PARENT_AGENT}
        record['registration'] = {key: record[key] for key in ('engine', 'session_ref', 'project_ref', 'cwd')}
        if delegation == 'members': record['delegation'] = member_grant(record, 'lead', True)
        elif delegation: record['delegation'] = delegation
        ledger = LocalLedger(self.root / 'sessions' / SID)
        try: ledger.setting('session-agent', record)
        finally: ledger.db.close()
        self.configure()

    @isolated_profiles
    def test_first_envelope_after_adopt_sees_the_inherited_member_grant(self):
        api = OwnerApi(inbox=[message(M1)])
        self.parent('members')
        prepared = self.prepare(self.adopt_scope())
        self.slot = prepared['slot_id']
        scope = self.setting(self.slot)['scope']
        self.assertEqual((scope['accept_tasks_from_members'], scope['standing_grants_from'], scope['trust_members']),
                         (True, 'lead', True))
        self.assertEqual(prepared['connect_flags'], ['--accept-tasks-from-members', '--standing-grants-from', 'lead',
                                                     '--trust-members', '--dispatch-mode', 'inline'])
        self.attest(self.slot)
        self.cap = self.capability(self.slot)
        # The connect flags must equal the scope: a connect without them is refused before any call.
        code, result, text = self.connect(api, self.cap)
        self.assertEqual((code, result['data'].get('reason')), (3, 'scope_mismatch'), text)
        self.assertEqual(api.calls, [])
        code, result, text = self.connect(api, self.cap, *prepared['connect_flags'])
        self.assertEqual(code, 0, text)
        self.assertEqual(result['data']['delegation']['agent_id'], AGENT, 'its own grant, never the parent agent')
        offered = self.offer(api)
        self.assertEqual(offered['message']['id'], M1)
        self.assertNotIn('No matching local owner delegation', offered['notice'])
        self.assertIn('task-check ' + M1, offered['notice'])
        # The adopt worker's own rebind keeps the grant and moves the adoption claim.
        code, result, text = self.cli(api, 'session', 'worker', 'rebind', self.slot)
        self.assertEqual(code, 0, text)
        successor = result['data']['slot_id']
        self.attest(successor, NEW_HANDLE)
        self.slot, self.cap = successor, self.capability(successor)
        offered = self.offer(api)
        self.assertIn('task-check ' + M1, offered['notice'])
        self.assertEqual(self.ops_join(api), 1)
        error = self.denied('native_worker_binding_denied', self.prepare, self.adopt_scope())
        self.assertEqual(error.reason, 'actor_claimed')
        # The owner's stop --leave on the successor retires the same worker agent.
        code, result, text = self.cli(api, 'session', 'worker', 'stop', successor, '--leave')
        self.assertEqual((code, result['data']['worker_agent']), (0, 'retired'), text)
        self.assertEqual(api.calls[-1][:2], ('agent_leave', {'agent_id': AGENT}))

    def ops_join(self, api):
        return api.ops().count('agent_join')

    @isolated_profiles
    def test_explicit_adopt_policy_is_kept_and_coordinator_policy_is_inherited(self):
        self.parent('members')
        slot = self.prepare(self.adopt_scope(accept_tasks_from_members=False))['slot_id']
        self.assertFalse(self.setting(slot)['scope']['accept_tasks_from_members'])
        # A coordinator-only parent policy carries its role and dispatch mode; the worker resolves it itself.
        import quorum_claude_worker as worker
        with claude(): worker.stop_slot(self.root, slot)
        self.parent({'agent_id': PARENT_AGENT, 'quorum_id': QUORUM, 'project_ref': 'team/project',
                     'cwd': str(self.project), 'coordinator_id': str(uuid.uuid4()), 'coordinator_role': 'lead',
                     'dispatch_mode': 'native-subagent'})
        prepared = self.prepare(self.adopt_scope())
        scope = self.setting(prepared['slot_id'])['scope']
        self.assertEqual((scope['coordinator_role'], scope['dispatch_mode']), ('lead', 'native-subagent'))
        self.assertEqual(prepared['connect_flags'], ['--accept-tasks-from', 'lead', '--dispatch-mode',
                                                     'native-subagent'])
