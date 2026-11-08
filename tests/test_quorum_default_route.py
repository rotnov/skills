"""#165 stage 4: the Claude worker is the default reception route; this chat's parent reception is the fallback.

Fake clocks, local fixtures and a fake API only: no provider, model, transcript, service, auth or network call.
"""
import hang_guard  # noqa: F401
import json
import os
from unittest.mock import patch

from quorum_test_support import isolated_profiles
from test_quorum_claude_worker import AGENT, OWNER_PROOF, QUORUM, SID, Api, claude, message, M1
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
        self.assertNotIn('accepts the invitation', plan['spawn_prompt'])
        self.assertEqual(self.tail(plan['attest_command'])[:4], ['session', 'worker', 'attest', plan['slot_id']])
        self.assertEqual(plan['retire_parent']['stdin'], {'agent_id': PARENT_AGENT})
        self.assertEqual([self.tail(step) for step in plan['fallback_steps']], [['session', 'start']])
        self.assertEqual([self.tail(step) for step in plan['fallback_steps_after_connect']],
                         [['session', 'worker', 'stop', plan['slot_id'], '--leave'], ['session', 'start']])
        # Until the parent attests, the move is pending; attested, the worker is the route.
        self.assertEqual((self.route()['next_step'], self.route()['worker_slot']), ('attest', plan['slot_id']))
        self.attest(plan['slot_id'])
        self.assertEqual((self.route()['route'], self.route()['fallback_reason']), ('claude-worker', None))

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
