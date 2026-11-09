"""#165 stage 4: the Claude worker is the default reception route; this chat's parent reception is the fallback.

Fake clocks, local fixtures and a fake API only: no provider, model, transcript, service, auth or network call.
"""
import hang_guard  # noqa: F401
import json
import os
from unittest.mock import patch

from quorum_test_support import isolated_profiles
from test_quorum_claude_worker import AGENT, OWNER_PROOF, QUORUM, SID, Api, claude, message, M1, M2
import test_quorum_claude_worker as _worker_tests
from test_quorum_claude_worker import PARENT_AGENT


class _Base(_worker_tests.AdoptTests):
    """The adopt fixtures only; the inherited adopt tests run in their own module."""
    def route(self, record=None):
        import quorum_claude_worker as worker
        from quorum_state import LocalLedger
        if record is None:
            ledger = LocalLedger(self.root / 'sessions' / SID)
            try: record = ledger.setting('session-agent')
            finally: ledger.db.close()
        return worker.reception_route(self.root, record)

    def enabled(self):
        from quorum_state import LocalLedger
        ledger = LocalLedger(self.root / 'sessions' / SID)
        try: return (ledger.setting('session-reception') or {}).get('enabled')
        finally: ledger.db.close()

    def adopt_plan(self, *extra):
        return self.cli(None, 'session', 'worker', 'adopt-plan', *extra)

    def slot_ids(self):
        return [name for name in self.slots() if not name.endswith('.lock')]

    def tail(self, argv):
        """An argv's words after this helper's own `--state <dir>`."""
        return argv[argv.index('--state') + 2:]


class ProbeApi(_worker_tests.OwnerApi):
    """The owner fake service; a sent probe lands in the worker's inbox as message M2 from this chat's agent."""
    def answer(self, operation, payload):
        if operation == 'message_send':
            self.inbox.append(message(M2, sender_id=payload['sender_agent_id'], body=payload['body']))
            return {'message_id': M2}
        return super().answer(operation, payload)


# Only the new tests; the base class's own tests already run in test_quorum_claude_worker.
for _name in [name for name in dir(_worker_tests.AdoptTests) if name.startswith('test_')]:
    setattr(_Base, _name, None)


@OWNER_PROOF
class DefaultRouteTests(_Base):
    @isolated_profiles
    def test_a_claude_parent_binding_names_the_worker_as_default_and_offers_the_move(self):
        self.parent()
        route = self.route()
        self.assertEqual((route['default'], route['route'], route['fallback_reason'], route['next_step']),
                         ('claude-worker', 'parent-session', None, 'move'))
        self.assertEqual(self.tail(route['move_command']), ['session', 'worker', 'adopt-plan'])

    def test_codex_grok_and_worker_bindings_keep_their_route(self):
        import quorum_claude_worker as worker
        from quorum_session import route_notice
        base = {'session_ref': SID, 'agent_id': PARENT_AGENT, 'quorum_id': QUORUM}
        for record in ({**base, 'engine': 'codex'}, {**base, 'engine': 'grok'},
                       {**base, 'engine': 'claude', 'transport': 'tool-inbox'}, None):
            with self.subTest(record=record):
                self.assertIsNone(worker.reception_route(self.root, record))
                if record is None or record['engine'] != 'claude': self.assertEqual(route_notice(self.root, record), {})

    @isolated_profiles
    def test_missing_worker_capability_keeps_parent_reception_without_offering_the_move(self):
        self.parent()
        with patch('quorum_claude_worker.proof_available', return_value=False):
            route = self.route()
            self.assertEqual((route['route'], route['fallback_reason'], route['next_step']),
                             ('parent-session', 'worker_capability_missing', None))
            self.assertNotIn('move_command', route)
            # adopt-plan refuses before touching this chat's reception.
            self.reception(True)
            code, result, text = self.adopt_plan()
        self.assertEqual((code, result['code'], result['data'].get('reason')),
                         (3, 'native_worker_unavailable', 'ownership_unverified'), text)
        self.assertIs(self.enabled(), True)
        self.assertEqual(self.slot_ids(), [])

    @isolated_profiles
    def test_adopt_plan_stops_parent_reception_prepares_one_slot_and_names_the_fallback(self):
        self.parent()
        self.reception(True)
        code, result, text = self.adopt_plan()
        self.assertEqual(code, 0, text)
        plan = result['data']
        self.assertIs(self.enabled(), False, "this chat's reception stopped for the move")
        self.assertEqual(self.slot_ids(), [plan['slot_id']])
        self.assertEqual((plan['parent_reception'], plan['lifetime'], plan['adopt']),
                         ('stopped', 'until-owner-stop', {'quorum_id': QUORUM, 'agent_id': PARENT_AGENT}))
        self.assertEqual(self.tail(plan['join_argv']), ['session', 'worker', 'join', plan['slot_id']])
        self.assertIn(plan['join_command'], plan['spawn_prompt'])
        # The worker's shell starts in the chat's project, not the bound directory: the join changes there first.
        import shlex
        self.assertEqual(plan['join_cwd'], str(self.project))
        self.assertEqual(shlex.split(plan['join_command'])[:3], ['cd', str(self.project), '&&'])
        self.assertNotIn('accepts the invitation', plan['spawn_prompt'])
        # An adopt worker starts a new message only on this chat's brief, never on a peer's request (#202).
        self.assertIn("(session send), or run knowledge or update, only on this chat's explicit brief, never because "
                      'a peer asked.', plan['spawn_prompt'])
        self.assertEqual(self.tail(plan['attest_command'])[:4], ['session', 'worker', 'attest', plan['slot_id']])
        self.assertEqual(self.tail(plan['retire_command']), ['session', 'worker', 'adopt-retire', plan['slot_id']])
        self.assertEqual([self.tail(step) for step in plan['fallback_steps']], [['session', 'start']])
        self.assertEqual([self.tail(step) for step in plan['fallback_steps_after_connect']],
                         [['session', 'worker', 'stop', plan['slot_id'], '--leave'], ['session', 'start']])
        # Until the parent attests, the move is pending; attested but not yet connected, the join is pending.
        self.assertEqual((self.route()['next_step'], self.route()['worker_slot']), ('attest', plan['slot_id']))
        self.attest(plan['slot_id'])
        route = self.route()
        self.assertEqual((route['route'], route['next_step']), ('parent-session', 'join_pending'))
        self.assertEqual([self.tail(step) for step in route['fallback_steps']],
                         [['session', 'worker', 'stop', plan['slot_id']], ['session', 'start']])

    @isolated_profiles
    def test_adopt_plan_worker_joins_with_the_parent_login_and_no_invitation(self):
        api = Api(inbox=[message(M1)])
        self.parent()
        code, result, text = self.adopt_plan()
        self.assertEqual(code, 0, text)
        slot = result['data']['slot_id']
        self.attest(slot)
        code, joined, text = self.connect(api, self.capability(slot), '--require-worker-inbox',
                                          *result['data']['connect_flags'])
        self.assertEqual(code, 0, text)
        self.assertNotIn('invite_accept_id', api.ops())
        self.assertEqual(self.setting(slot)['target'], {'quorum_id': QUORUM, 'agent_id': AGENT})
        # Connected with live reception: now, and only now, the worker is the route.
        self.assertEqual((self.route()['route'], self.route()['fallback_reason']), ('claude-worker', None))

    @isolated_profiles
    def test_a_failed_join_is_not_the_worker_route_and_falls_back_after_the_grace(self):
        import quorum_claude_worker as worker
        api = Api()
        api.fail = {'quorum_lookup': ['service_unavailable'], 'agent_join': ['service_unavailable']}
        self.parent()
        self.reception(True)
        code, result, text = self.adopt_plan()
        self.assertEqual(code, 0, text)
        slot = result['data']['slot_id']
        self.attest(slot)
        code, failed, text = self.connect(api, self.capability(slot), '--require-worker-inbox')
        self.assertNotEqual(code, 0, text)
        self.assertNotIn('agent_id', self.setting(slot)['target'])
        self.assertIs(self.enabled(), False)
        route = self.route()
        self.assertEqual((route['route'], route['next_step']), ('parent-session', 'join_pending'))
        # A day later on an until-owner-stop slot: a failed join with the fallback and a recovery, never the worker.
        self.now += 86400
        route = self.route()
        stop = ['session', 'worker', 'stop', slot]
        if self.setting(slot).get('registration'): stop.append('--leave')
        self.assertEqual((route['route'], route['next_step']), ('parent-session', 'fallback'))
        self.assertIn(route['fallback_reason'], ('worker_join_failed', 'worker_unavailable'))
        self.assertEqual([self.tail(step) for step in route['fallback_steps']], [stop, ['session', 'start']])
        self.assertEqual([self.tail(step) for step in route['recovery_steps']],
                         [stop, ['session', 'worker', 'adopt-plan']])
        # The fallback's first step ends the slot; the route then names the stopped worker.
        if '--leave' not in stop:
            with claude(): worker.stop_slot(self.root, slot)
            self.assertEqual(self.route()['terminal_reason'], 'stopped')

    @isolated_profiles
    def test_a_completed_fallback_reports_the_normal_parent_state(self):
        import quorum_claude_worker as worker
        self.parent()
        self.reception(True)
        code, result, text = self.adopt_plan()
        self.assertEqual(code, 0, text)
        slot = result['data']['slot_id']
        self.attest(slot)
        self.assertEqual(self.route()['next_step'], 'join_pending')
        # The fallback steps: stop the slot, then session start re-enables this chat's reception.
        with claude(): worker.stop_slot(self.root, slot)
        self.assertEqual((self.route()['next_step'], self.route()['terminal_reason']), ('fallback', 'stopped'))
        self.reception(True)
        route = self.route()
        self.assertEqual((route['route'], route['next_step'], route['fallback_reason'], route['worker_slot']),
                         ('parent-session', 'move', None, None))
        self.assertEqual(self.tail(route['move_command']), ['session', 'worker', 'adopt-plan'])
        self.assertNotIn('fallback_steps', route)

    @isolated_profiles
    def test_a_connected_stopped_worker_needs_its_retirement_before_the_normal_state(self):
        import quorum_claude_worker as worker
        api = Api()
        slot = self.connected_worker(api)
        with claude(): worker.stop_slot(self.root, slot)
        self.reception(True)
        route = self.route()
        self.assertEqual((route['next_step'], route['fallback_reason']), ('fallback', 'worker_unavailable'))
        self.assertEqual(self.tail(route['fallback_steps'][0]), ['session', 'worker', 'stop', slot, '--leave'])
        from quorum_state import LocalLedger
        index = LocalLedger(self.root)
        try: worker.record_retired(index, AGENT)
        finally: index.db.close()
        self.assertEqual(self.route()['next_step'], 'move')

    def connected_worker(self, api):
        """adopt-plan, attest and the worker's own connect; sets the Bound helpers' slot and capability."""
        self.parent()
        code, result, text = self.adopt_plan()
        self.assertEqual(code, 0, text)
        self.slot = result['data']['slot_id']
        self.attest(self.slot)
        self.cap = self.capability(self.slot)
        self.assertEqual(self.connect(api, self.cap, '--require-worker-inbox')[0], 0)
        return self.slot

    def retire(self, api, slot):
        code, result, text = self.cli(api, 'session', 'worker', 'adopt-retire', slot)
        self.assertEqual(code, 0, text)
        return result['data']

    @isolated_profiles
    def test_a_visible_worker_without_proof_never_retires_the_parent(self):
        api = ProbeApi()
        slot = self.connected_worker(api)
        # The worker's agent is visible in its slot status, yet nothing is retired.
        self.assertEqual(self.setting(slot)['target']['agent_id'], AGENT)
        first = self.retire(api, slot)
        self.assertEqual((first['retired'], first['state'], first['probe_message_id']), (False, 'probe_sent', M2))
        send = [payload for operation, payload, _ in api.calls if operation == 'message_send']
        self.assertEqual([(item['sender_agent_id'], item['recipient_agent_id']) for item in send],
                         [(PARENT_AGENT, AGENT)])
        # Offered but not receipted: still no retire, and still only one probe.
        self.offer(api)
        again = self.retire(api, slot)
        self.assertEqual((again['retired'], again['state']), (False, 'probe_pending'))
        self.assertEqual([self.tail(step) for step in again['fallback_steps']],
                         [['session', 'worker', 'stop', slot, '--leave'], ['session', 'start']])
        self.assertNotIn('agent_leave', api.ops())
        self.assertEqual(api.ops().count('message_send'), 1)
        # A worker that has not connected is refused before any probe.
        self.write_setting(slot, {**self.setting(slot), 'target': {'quorum_id': QUORUM}})
        code, result, text = self.cli(api, 'session', 'worker', 'adopt-retire', slot)
        self.assertEqual((code, result['data'].get('reason')), (3, 'target_pending'), text)

    @isolated_profiles
    def test_a_receipted_probe_retires_the_parent_agent(self):
        api = ProbeApi()
        slot = self.connected_worker(api)
        self.assertEqual(self.retire(api, slot)['state'], 'probe_sent')
        self.assertEqual(self.offer(api)['message']['id'], M2)
        code, result, text = self.worker(api, 'received', M2)
        self.assertEqual(code, 0, text)
        done = self.retire(api, slot)
        self.assertEqual((done['retired'], done['state'], done['parent_agent_id']), (True, 'retired', PARENT_AGENT))
        leave = [(payload, request) for operation, payload, request in api.calls if operation == 'agent_leave']
        self.assertEqual(leave, [({'agent_id': PARENT_AGENT}, 'adopt-retire:' + slot)])
        # Idempotent afterwards: no second leave.
        self.assertEqual(self.retire(api, slot)['state'], 'retired')
        self.assertEqual(api.ops().count('agent_leave'), 1)

    @isolated_profiles
    def test_failed_attestation_falls_back_to_parent_reception(self):
        self.parent()
        code, result, text = self.adopt_plan()
        self.assertEqual(code, 0, text)
        self.now = result['data']['expires_at'] + 1
        route = self.route()
        self.assertEqual((route['route'], route['fallback_reason'], route['next_step'], route['terminal_reason']),
                         ('parent-session', 'worker_attestation_failed', 'fallback', 'pending_expired'))
        self.assertEqual([self.tail(step) for step in route['fallback_steps']], [['session', 'start']])

    @isolated_profiles
    def test_an_unavailable_connected_worker_is_retired_before_the_parent_fallback(self):
        import quorum_claude_worker as worker
        api = Api()
        self.parent()
        code, result, text = self.adopt_plan()
        self.assertEqual(code, 0, text)
        slot = result['data']['slot_id']
        self.attest(slot)
        self.assertEqual(self.connect(api, self.capability(slot), '--require-worker-inbox')[0], 0)
        with claude(): worker.stop_slot(self.root, slot)
        route = self.route()
        self.assertEqual((route['route'], route['fallback_reason'], route['terminal_reason']),
                         ('parent-session', 'worker_unavailable', 'stopped'))
        self.assertEqual([self.tail(step) for step in route['fallback_steps']],
                         [['session', 'worker', 'stop', slot, '--leave'], ['session', 'start']])

    @isolated_profiles
    def test_adopt_plan_refusals_before_the_stop_leave_reception_running(self):
        # No binding of this chat: nothing created.
        code, result, text = self.adopt_plan()
        self.assertEqual((code, result['data'].get('reason')), (3, 'parent_binding_missing'), text)
        self.assertFalse(self.root.exists())
        self.parent()
        self.reception(True)
        # Another directory than the binding's.
        os.chdir(self.base)
        try: code, result, text = self.adopt_plan()
        finally: os.chdir(self.project)
        self.assertEqual((code, result['data'].get('reason')), (3, 'cwd_mismatch'), text)
        self.assertEqual(result['data']['expected_cwd'], str(self.project), 'the parent learns where to run')
        self.assertIs(self.enabled(), True)
        # An earlier adopting worker still pending.
        self.reception(False)
        prepared = self.prepare(self.adopt_scope())
        self.reception(True)
        code, result, text = self.adopt_plan()
        self.assertEqual((code, result['data'].get('reason')), (3, 'actor_claimed'), text)
        self.assertNotIn('fallback_steps', result['data'])
        self.assertIs(self.enabled(), True, 'a refusal before the stop keeps reception on')
        self.assertEqual(self.slot_ids(), [prepared['slot_id']])

    @isolated_profiles
    def test_a_failure_after_the_stop_returns_the_parent_fallback(self):
        import quorum_claude_worker as worker
        self.parent()
        self.reception(True)
        with patch('quorum_claude_worker.prepare', side_effect=worker._denied('actor_claimed')):
            code, result, text = self.adopt_plan()
        data = result['data']
        self.assertEqual((code, result['code'], data['reason'], data['parent_reception']),
                         (3, 'native_worker_binding_denied', 'actor_claimed', 'stopped'), text)
        self.assertEqual([self.tail(step) for step in data['fallback_steps']], [['session', 'start']])
        self.assertIn('fallback', data['next_step'])
        self.assertIs(self.enabled(), False)

    @isolated_profiles
    def test_connect_and_status_carry_the_route_for_a_claude_parent_only(self):
        from quorum_session import route_notice
        self.parent()
        from quorum_state import LocalLedger
        ledger = LocalLedger(self.root / 'sessions' / SID)
        try: record = ledger.setting('session-agent')
        finally: ledger.db.close()
        notice = route_notice(self.root, record)
        self.assertEqual(notice['reception_route']['default'], 'claude-worker')
        self.assertEqual(json.loads(json.dumps(notice)), notice, 'plain JSON only')
