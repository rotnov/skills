"""The one local delivery ledger (#165): rows, compare-and-set, the server ack, unknown and unsent.

Route behaviour stays covered through each caller by test_quorum_delivery_intents.py,
test_quorum_native_inbox.py, test_quorum_awaiting_owner.py and test_quorum_grok_cloud.py. These tests pin
the module's own contract, and that the callers keep no second copy of it.
"""
import ast
from contextlib import nullcontext
import json
from pathlib import Path
import sys
import tempfile
import unittest
import uuid

SKILL = Path(__file__).resolve().parents[1] / 'skills/quorum'
SCRIPTS = SKILL / 'scripts'
sys.path.insert(0, str(SCRIPTS))
from quorum_claude_pipe import DeliveryUnsent as PipeUnsent
from quorum_claude_worker import ClaudeWorkerError
from quorum_delivery import (ABSENT_PREFIX, CLOUD_STATES, Deliveries, DeliveryUnsent, PREFIX, PULL_STATES, PUSH_STATES,
                             server_ack)
from quorum_native_inbox import Guarded, NativeInboxError
from quorum_state import LocalLedger

MID = '22222222-2222-4222-8222-222222222222'
OTHER = '44444444-4444-4444-8444-444444444444'
ATTEMPT = '33333333-3333-4333-8333-333333333333'
SCOPE = {'agent_id': 'agent', 'session_ref': 'session'}
CALLERS = ('quorum_session.py', 'quorum_native_inbox.py', 'quorum_grok_cloud.py', 'quorum_grok_webhook.py',
           'quorum_claude_worker.py')


class Api:
    def __init__(self, during_ack=None, fail=None):
        self.acks, self.during_ack, self.fail = [], during_ack, fail

    def call(self, operation, payload):
        if operation != 'session_ack': raise AssertionError(operation)
        self.acks.append(payload)
        if self.during_ack: self.during_ack()
        if self.fail: raise self.fail


class LedgerCase(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.ledger = LocalLedger(Path(directory.name) / 'ledger')
        self.addCleanup(self.ledger.db.close)
        self.rows = Deliveries(self.ledger)

    def put(self, value, message_id=MID):
        self.ledger.setting(PREFIX + message_id, value)


class RowTests(LedgerCase):
    def test_rows_keep_insertion_order_and_exclude_audit_markers(self):
        self.put({'state': 'offered'}, OTHER)
        self.put({'state': 'acked'}, MID)
        self.ledger.setting(ABSENT_PREFIX + MID + ':' + ATTEMPT, {'state': 'confirmed_absent'})
        self.assertEqual(self.rows.rows(), [(OTHER, {'state': 'offered'}), (MID, {'state': 'acked'})])
        self.assertEqual(self.rows.states(), ['offered', 'acked'])
        self.assertEqual(self.rows.archive(), {PREFIX + OTHER: {'state': 'offered'}, PREFIX + MID: {'state': 'acked'}})

    def test_clear_waits_for_the_callers_transaction(self):
        self.put({'state': 'intent'})
        marker = ABSENT_PREFIX + MID + ':' + ATTEMPT
        self.ledger.setting(marker, {'state': 'confirmed_absent'})
        try:
            with self.ledger.db:
                self.rows.clear()
                raise KeyError
        except KeyError: pass
        self.assertEqual(self.rows.get(MID), {'state': 'intent'})
        with self.ledger.db: self.rows.clear()
        self.assertEqual((self.rows.rows(), self.ledger.setting(marker)), ([], {'state': 'confirmed_absent'}))

    def test_a_row_of_another_route_is_refused_not_converted(self):
        self.put({'state': 'offered'})
        self.rows.require_states(PULL_STATES)
        self.put({'state': 'intent'}, OTHER)
        with self.assertRaisesRegex(ValueError, '^session_delivery_unknown$'): self.rows.require_states(PULL_STATES)
        self.assertEqual(self.rows.get(OTHER), {'state': 'intent'})

    def test_server_ack_scopes_the_message(self):
        api = Api()
        server_ack(api, SCOPE, MID)
        self.assertEqual(api.acks, [{**SCOPE, 'message_id': MID}])

    def test_the_contract_lists_every_state_of_every_route(self):
        text = (SKILL / 'references/protocol.md').read_text(encoding='utf-8')
        table = text[text.index('### When each route acknowledges'):text.index('### Liveness and maintenance')]
        for state in (*PUSH_STATES, *PULL_STATES, *CLOUD_STATES): self.assertIn('`' + state + '`', table)
        self.assertIn('quorum_delivery.py', table)


class CompareAndSetTests(LedgerCase):
    def test_swap_and_remove_touch_only_the_row_they_read(self):
        self.put({'state': 'submitted'})
        raw = self.rows.raw(MID)
        self.put({'state': 'queued'})
        self.assertFalse(self.rows.swap(MID, raw, {'state': 'acked'}))
        self.rows.remove(MID, raw)
        self.assertEqual(self.rows.get(MID), {'state': 'queued'})

    def test_update_retries_a_lost_race_on_a_fresh_read(self):
        self.put({'n': 0})
        seen = []
        def change(current):
            seen.append(current['n'])
            if current['n'] == 0: self.put({'n': 1})
            return {'n': current['n'] + 10}
        self.assertEqual(self.rows.update(MID, change), {'n': 11})
        self.assertEqual(seen, [0, 1])

    def test_update_gives_up_after_its_attempts(self):
        self.put({'n': 0})
        calls = []
        def change(current):
            calls.append(1)
            self.put({'n': len(calls)})
            return {'n': -1}
        self.assertIsNone(self.rows.update(MID, change, attempts=3))
        self.assertEqual(len(calls), 3)
        self.assertEqual(self.rows.get(MID), {'n': 3})

    def test_update_writes_nothing_for_a_refusal_or_the_same_row(self):
        self.put({'state': 'acked'})
        raw = self.rows.raw(MID)
        self.assertIsNone(self.rows.update(MID, lambda current: None))
        self.assertEqual(self.rows.update(MID, lambda current: current), {'state': 'acked'})
        self.assertEqual(self.rows.raw(MID), raw)

    def test_only_an_optional_transition_tolerates_a_missing_row(self):
        self.assertIsNone(self.rows.update(MID, lambda current: {}, optional=True))
        with self.assertRaises(TypeError): self.rows.update(MID, lambda current: {})


class PushTests(LedgerCase):
    def attempt(self, push, recheck=lambda: None):
        return self.rows.attempt(MID, push, recheck=recheck)

    def test_the_intent_is_durable_before_the_push(self):
        states = []
        self.attempt(lambda: states.append(self.rows.get(MID)['state']))
        self.assertEqual(states, ['intent'])
        self.assertEqual(self.rows.get(MID), {'state': 'submitted'})

    def test_a_pointer_keeps_the_attempt_for_the_owner(self):
        seen = []
        def push():
            seen.append(self.rows.get(MID)['attempt_id'])
            return 'pointer'
        self.attempt(push)
        self.assertEqual(self.rows.get(MID), {'state': 'submitted', 'attempt_id': seen[0]})

    def test_unsent_removes_only_its_own_intent_and_reraises(self):
        def push(): raise DeliveryUnsent()
        with self.assertRaises(DeliveryUnsent): self.attempt(push)
        self.assertIsNone(self.rows.get(MID))
        def replaced():
            self.put({'state': 'submitted'})
            raise DeliveryUnsent()
        with self.assertRaises(DeliveryUnsent): self.attempt(replaced)
        self.assertEqual(self.rows.get(MID), {'state': 'submitted'})

    def test_any_other_failure_keeps_the_intent_as_unknown(self):
        def push(): raise OSError('private detail')
        with self.assertRaisesRegex(ValueError, '^session_delivery_unknown$'): self.attempt(push)
        self.assertEqual(self.rows.get(MID)['state'], 'intent')
        self.assertTrue(uuid.UUID(self.rows.get(MID)['attempt_id']))

    def test_a_refused_recheck_never_pushes_and_leaves_no_intent(self):
        pushed = []
        def refuse(): raise ValueError('session_binding_denied')
        with self.assertRaisesRegex(ValueError, '^session_binding_denied$'):
            self.attempt(lambda: pushed.append(1), refuse)
        self.assertEqual((pushed, self.rows.get(MID)), ([], None))

    def test_a_row_that_replaced_the_intent_survives_the_outcome(self):
        self.attempt(lambda: self.put({'state': 'acked'}))
        self.assertEqual(self.rows.get(MID), {'state': 'acked'})

    def test_only_an_intent_at_the_head_is_unknown(self):
        self.assertFalse(self.rows.head_unknown([]))
        self.assertFalse(self.rows.head_unknown(None))
        self.put({'state': 'intent'})
        self.put({'state': 'submitted'}, OTHER)
        self.assertTrue(self.rows.head_unknown([{'id': MID}]))
        self.assertFalse(self.rows.head_unknown([{'id': OTHER}, {'id': MID}]))

    def test_proof_moves_only_a_submitted_row(self):
        self.put({'state': 'submitted', 'attempt_id': ATTEMPT})
        self.rows.prove(MID)
        self.assertEqual(self.rows.get(MID), {'state': 'queued', 'attempt_id': ATTEMPT})
        self.put({'state': 'intent', 'attempt_id': ATTEMPT})
        self.rows.prove(MID)
        self.assertEqual(self.rows.get(MID)['state'], 'intent')
        self.rows.prove(OTHER)
        self.assertIsNone(self.rows.get(OTHER))

    def test_confirm_records_the_proof_before_the_ack_and_acked_after(self):
        self.put({'state': 'submitted'})
        seen = []
        self.rows.confirm(Api(lambda: seen.append(self.rows.get(MID)['state'])), SCOPE, MID)
        self.assertEqual((seen, self.rows.get(MID)), (['queued'], {'state': 'acked'}))

    def test_a_failed_ack_leaves_the_proof_for_the_next_pass(self):
        self.put({'state': 'submitted'})
        with self.assertRaises(ConnectionError): self.rows.confirm(Api(fail=ConnectionError()), SCOPE, MID)
        self.assertEqual(self.rows.get(MID), {'state': 'queued'})

    def test_the_owners_attestation_alone_acknowledges_an_intent(self):
        self.put({'state': 'intent', 'attempt_id': ATTEMPT})
        seen = []
        self.rows.attest_queued(Api(lambda: seen.append(self.rows.get(MID))), SCOPE, MID)
        self.assertEqual((seen, self.rows.get(MID)), ([{'state': 'queued', 'attempt_id': ATTEMPT}],
                                                      {'state': 'acked', 'attempt_id': ATTEMPT}))

    def test_settled_returns_a_stuck_intent_once_no_listener_is_live(self):
        self.put({'state': 'intent'})
        self.assertEqual(self.rows.settled(MID, wait=30, live=lambda: False), {'state': 'intent'})
        self.assertEqual(self.rows.settled(MID, wait=0, live=lambda: True), {'state': 'intent'})
        polls = []
        def live():
            polls.append(1)
            self.put({'state': 'submitted'})
            return True
        self.assertEqual(self.rows.settled(MID, wait=30, live=live), {'state': 'submitted'})
        self.assertEqual(len(polls), 1)


class AttestAbsentTests(LedgerCase):
    BINDING = {'agent_id': 'agent', 'engine': 'codex'}

    def attest(self, attempt_id=ATTEMPT, unreceipted=lambda: True, recheck=lambda: None):
        return self.rows.attest_absent(MID, attempt_id, self.BINDING, unreceipted=unreceipted, recheck=recheck,
                                       exclusive=nullcontext)

    def marker(self): return self.ledger.setting(ABSENT_PREFIX + MID + ':' + ATTEMPT)

    def test_it_clears_an_exact_attempt_and_keeps_an_audit_marker(self):
        for state in ('intent', 'submitted'):
            with self.subTest(state=state):
                self.put({'state': state, 'attempt_id': ATTEMPT})
                with self.ledger.db: self.ledger.db.execute('delete from settings where key=?',
                                                            (ABSENT_PREFIX + MID + ':' + ATTEMPT,))
                self.attest()
                self.assertIsNone(self.rows.get(MID))
                self.assertEqual(self.marker(), {'state': 'confirmed_absent', 'message_id': MID, 'attempt_id': ATTEMPT,
                                                 'binding': self.BINDING})

    def test_a_repeated_command_never_touches_a_newer_attempt(self):
        self.put({'state': 'intent', 'attempt_id': ATTEMPT})
        self.attest()
        newer = {'state': 'intent', 'attempt_id': str(uuid.uuid4())}
        self.put(newer)
        self.attest(unreceipted=lambda: self.fail('no service check'))
        self.assertEqual(self.rows.get(MID), newer)

    def test_anything_else_is_refused_without_a_marker(self):
        def denied(row, **options):
            if row: self.put(row)
            with self.assertRaisesRegex(ValueError, '^session_reconcile_denied$'): self.attest(**options)
            self.assertIsNone(self.marker())
        denied(None)
        denied({'state': 'intent', 'attempt_id': str(uuid.uuid4())})
        denied({'state': 'queued', 'attempt_id': ATTEMPT})
        denied({'state': 'submitted'})
        denied({'state': 'intent', 'attempt_id': ATTEMPT}, unreceipted=lambda: False)
        self.assertEqual(self.rows.get(MID)['state'], 'intent')

    def test_a_receipt_committed_during_the_service_check_wins(self):
        self.put({'state': 'submitted', 'attempt_id': ATTEMPT})
        def receipted():
            self.put({'state': 'queued'})
            return True
        with self.assertRaisesRegex(ValueError, '^session_reconcile_denied$'): self.attest(unreceipted=receipted)
        self.assertEqual((self.rows.get(MID), self.marker()), ({'state': 'queued'}, None))


class PullTests(LedgerCase):
    RECORD = {'agent_id': 'agent', 'session_ref': 'session', 'quorum_id': 'quorum'}
    HEAD = {'id': MID, 'body': 'peer', 'secret': 'dropped', 'reply_to': MID, 'notification': False}

    def offer(self, **extra):
        self.rows.offer(MID, self.RECORD, self.HEAD, ('id', 'body'), **extra)

    def test_an_offer_caches_only_named_fields_and_is_insert_only(self):
        self.offer(offered_at=5)
        self.assertEqual(self.rows.get(MID), {
            'state': 'offered', 'offered_at': 5, 'agent_id': 'agent', 'session_ref': 'session', 'quorum_id': 'quorum',
            'reply_to': True, 'notification': False, 'message': {'id': MID, 'body': 'peer'}})
        self.put({'state': 'acked'})
        self.offer()
        self.assertEqual(self.rows.get(MID), {'state': 'acked'})

    def test_a_worker_receipt_acks_first_and_keeps_every_cached_field(self):
        self.offer()
        seen = []
        self.rows.receipt_offer(Api(lambda: seen.append(self.rows.get(MID)['state'])), SCOPE, MID, lambda row: True)
        self.assertEqual(seen, ['offered'])
        self.assertEqual({**self.rows.get(MID), 'state': 'offered'}['message']['body'], 'peer')
        self.assertEqual(self.rows.get(MID)['state'], 'acked')

    def test_a_lost_ack_leaves_the_offer_to_repeat(self):
        self.offer()
        with self.assertRaises(ConnectionError):
            self.rows.receipt_offer(Api(fail=ConnectionError()), SCOPE, MID, lambda row: True)
        self.assertEqual(self.rows.get(MID)['state'], 'offered')

    def test_an_acked_row_sends_no_second_ack(self):
        self.put({'state': 'acked'})
        api = Api()
        self.rows.receipt_offer(api, SCOPE, MID, lambda row: True)
        self.assertEqual(api.acks, [])

    def test_a_worker_receipt_that_cannot_persist_is_unknown(self):
        self.offer()
        with self.assertRaisesRegex(ValueError, '^session_delivery_unknown$'):
            self.rows.receipt_offer(Api(), SCOPE, MID, lambda row: False)
        self.assertEqual(self.rows.get(MID)['state'], 'offered')
        with self.assertRaisesRegex(ValueError, '^session_delivery_unknown$'):
            self.rows.receipt_offer(Api(lambda: self.put({'state': 'intent'})), SCOPE, MID, lambda row: True)

    def test_a_cloud_receipt_makes_one_attempt_and_never_raises_on_it(self):
        self.offer()
        self.rows.receipt_cloud_offer(Api(), SCOPE, MID)
        self.assertEqual(self.rows.get(MID)['state'], 'acked')
        self.put({'state': 'offered', 'seen': 0})
        raced = []
        original = self.rows.swap
        def swap(message_id, raw, value):
            raced.append(1)
            self.put({'state': 'offered', 'seen': 1})
            return original(message_id, raw, value)
        self.rows.swap = swap
        self.rows.receipt_cloud_offer(Api(), SCOPE, MID)
        self.assertEqual((len(raced), self.rows.get(MID)), (1, {'state': 'offered', 'seen': 1}))
        with self.ledger.db: self.ledger.db.execute('delete from settings where key=?', (PREFIX + MID,))
        self.rows.receipt_cloud_offer(Api(), SCOPE, MID)
        self.assertIsNone(self.rows.get(MID))

    def test_write_then_ack_writes_first_and_a_refusal_sends_nothing(self):
        self.put({'state': 'offered'})
        seen = []
        api = Api(lambda: seen.append(self.rows.get(MID)['state']))
        self.assertEqual(self.rows.write_then_ack(api, SCOPE, MID, lambda row: {'state': 'parked'}),
                         {'state': 'parked'})
        self.assertEqual(seen, ['parked'])
        self.assertIsNone(self.rows.write_then_ack(api, SCOPE, MID, lambda row: None))
        self.assertEqual(len(api.acks), 1)
        self.assertEqual(self.rows.write_then_ack(api, SCOPE, MID, lambda row: row), {'state': 'parked'})
        self.assertEqual(len(api.acks), 2)


class OwnershipTests(unittest.TestCase):
    def literals(self, name):
        for node in ast.walk(ast.parse((SCRIPTS / name).read_text(encoding='utf-8'))):
            if isinstance(node, ast.Constant) and isinstance(node.value, str): yield node.value

    def test_every_caller_reaches_the_row_and_the_ack_through_the_ledger(self):
        for name in CALLERS:
            with self.subTest(name=name):
                for literal in self.literals(name):
                    # Prompts may name the message ID, never the row key.
                    self.assertNotIn(PREFIX, literal)
                    self.assertNotEqual(literal, 'session_ack')

    def test_one_unsent_outcome(self):
        self.assertIs(PipeUnsent, DeliveryUnsent)
        self.assertEqual(str(DeliveryUnsent()), 'session_delivery_unsent')

    def test_the_ledger_imports_no_client(self):
        tree = ast.parse((SCRIPTS / 'quorum_delivery.py').read_text(encoding='utf-8'))
        imported = {alias.name for node in ast.walk(tree) if isinstance(node, ast.Import) for alias in node.names}
        imported |= {node.module for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)}
        self.assertEqual({name for name in imported if name.startswith('quorum')}, set())


class ClaudeVocabularyTests(unittest.TestCase):
    CLAUDE = {'native_worker_slot_terminal', 'native_worker_create_ambiguous'}

    def test_the_codex_error_type_holds_no_claude_vocabulary(self):
        self.assertEqual(NativeInboxError.CODES & self.CLAUDE, set())
        self.assertEqual(NativeInboxError.REASONS & {'rebound', 'slot_invalid', 'scope_invalid', 'recovery_needed',
                                                     'target_mismatch', 'parent_binding_running'}, set())
        self.assertFalse(hasattr(NativeInboxError, 'RECOVERY'))
        error = NativeInboxError('native_worker_slot_terminal', 'rebound')
        self.assertEqual((error.code, error.reason), ('native_worker_unavailable', None))

    def test_a_claude_error_is_still_a_worker_refusal_with_its_own_words(self):
        error = ClaudeWorkerError('native_worker_slot_terminal', 'rebound')
        self.assertIsInstance(error, NativeInboxError)
        self.assertEqual((error.code, error.reason), ('native_worker_slot_terminal', 'rebound'))
        self.assertIn('session worker rebind', error.diagnostic()['next_step'])

    def test_claude_guidance_runs_before_the_shared_fallback_only_for_its_reasons(self):
        rebuilt = ClaudeWorkerError('native_worker_unavailable', 'path_unsafe').diagnostic()
        self.assertEqual(rebuilt, {**NativeInboxError('native_worker_unavailable').diagnostic(), 'reason': 'path_unsafe'})

    def test_a_dispatched_refusal_stays_its_own_class_and_keeps_the_recovery_route(self):
        effect = {'state': 'uncertain', 'operation': 'session_reply'}
        claude = ClaudeWorkerError('native_worker_unavailable', 'recovery_needed', recovery='actor').dispatched(effect)
        self.assertIs(type(claude), ClaudeWorkerError)
        self.assertEqual((claude.reason, claude.recovery, claude.remote_effect), ('recovery_needed', 'actor', effect))
        codex = NativeInboxError('native_worker_binding_denied', 'binding_changed').dispatched(effect)
        self.assertIs(type(codex), NativeInboxError)
        self.assertEqual(json.loads(json.dumps(codex.diagnostic()))['remote_effect'], effect)

    def test_guarded_reraises_a_claude_refusal_with_the_dispatched_effect(self):
        state = {'refuse': False}
        def authorize():
            if state['refuse']: raise ClaudeWorkerError('native_worker_binding_denied', 'scope_mismatch')
        guarded = Guarded(None, authorize)
        guarded.read = 'session_inbox'
        state['refuse'] = True
        with self.assertRaises(ClaudeWorkerError) as raised: guarded.check()
        self.assertEqual(raised.exception.remote_effect, {'state': 'completed', 'operation': 'session_inbox'})


if __name__ == '__main__':
    unittest.main()
