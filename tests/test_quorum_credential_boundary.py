"""Real RPC encoding with synthetic credentials and intercepted transport; no external requests."""
import json
import unittest
from unittest.mock import patch
from quorum_test_support import isolated_profiles
import test_quorum_claude_worker as fixture
from quorum_api import QuorumApi, QuorumError, ServiceConfig
import quorum_claude_worker as worker
import quorum_native_inbox as inbox


@fixture.POSIX_PROOF
class CredentialBoundaryTests(fixture.Bound):
    def terminalize(self, slot, reason):
        if reason == 'stopped':
            with fixture.claude(): worker.stop_slot(self.root, slot)
        else: self.now = fixture.T0 + 900

    def authority(self, operation='reply'):
        self.now = fixture.T0
        slot, cap = self.active(self.accept_scope(), handle=str(fixture.uuid.uuid4()))
        context = self.authorize(cap, 'status')
        worker.pin_target(context, fixture.QUORUM, fixture.AGENT)
        context = self.authorize(cap, operation, {'message_id': fixture.M1})
        def check():
            with fixture.claude(): return worker.guard(context)
        return slot, check

    def real(self, token, capture):
        return QuorumApi(ServiceConfig(fixture.ORIGIN, fixture.KEY), token, request=capture)

    @isolated_profiles
    def test_real_token_terminal_refusal_prevents_reply_and_ack(self):
        for operation in ('session_reply', 'session_ack'):
            for reason in ('stopped', 'permission_expired'):
                with self.subTest(operation=operation, reason=reason):
                    slot, check = self.authority('reply' if operation == 'session_reply' else 'received')
                    sent = []
                    def token():
                        self.terminalize(slot, reason)
                        return 'synthetic-token'
                    def capture(url, headers, body):
                        sent.append((url, json.loads(body)))
                        return {'acked': True}
                    api = inbox.Guarded(self.real(token, capture), check)
                    with self.assertRaises(inbox.NativeInboxError) as raised:
                        api.call(operation, {'message_id': fixture.M1})
                    self.assertEqual((raised.exception.code, raised.exception.reason),
                                     ('native_worker_slot_terminal', reason))
                    self.assertEqual(sent, [], 'credentials returned after authority became terminal')
                    self.assertIsNone(raised.exception.remote_effect)
                    self.assertIsNone(api.effect())

    @isolated_profiles
    def test_reply_retains_completed_reply_when_ack_token_is_refused(self):
        for reason in ('stopped', 'permission_expired'):
            with self.subTest(reason=reason):
                self.now = fixture.T0
                fake = fixture.Api(inbox=[fixture.message(fixture.M1)])
                self.bound(fake, handle=reason)
                self.offer(fake)
                ledger, record, check = self.direct('reply', {'message_id': fixture.M1})
                sent = []
                def token():
                    if sent and sent[-1][0] == 'session_reply': self.terminalize(self.slot, reason)
                    return 'synthetic-token'
                def capture(url, headers, body):
                    operation = url.rsplit('/', 1)[1]
                    data = json.loads(body)
                    sent.append((operation, data))
                    return fake.answer(operation, {key[2:]: value for key, value in data.items()})
                api = inbox.Guarded(self.real(token, capture), check)
                with self.assertRaises(inbox.NativeInboxError) as raised:
                    inbox.reply(api, ledger, record, fixture.M1, 'local reply', 'normal', authorize=check)
                self.assertEqual([op for op, _ in sent if op not in inbox.NativeInboxError.READS], ['session_reply'])
                self.assertEqual(api.effect(), {'state': 'completed', 'operation': 'session_reply'})
                self.assertEqual(raised.exception.remote_effect, api.effect())
                self.assertEqual(ledger.setting('delivery:' + fixture.M1)['state'], 'offered')

    @isolated_profiles
    def test_real_token_failure_does_not_invent_dispatch_or_replace_prior_effect(self):
        for prior in (False, True):
            with self.subTest(prior=prior):
                slot, check = self.authority()
                sent, fail = [], [False]
                def token():
                    if fail[0]: raise QuorumError('auth_required')
                    return 'synthetic-token'
                def capture(url, headers, body):
                    sent.append(url.rsplit('/', 1)[1])
                    return {'acked': True}
                api = inbox.Guarded(self.real(token, capture), check)
                if prior: api.call('session_reply', {'message_id': fixture.M1})
                fail[0] = True
                with self.assertRaisesRegex(QuorumError, 'auth_required'):
                    api.call('session_ack', {'message_id': fixture.M1})
                self.assertEqual(sent, ['session_reply'] if prior else [])
                self.assertEqual(api.effect(), {'state': 'completed', 'operation': 'session_reply'} if prior else None)

    @isolated_profiles
    def test_nested_guarded_layers_keep_inner_authority_and_prior_effects(self):
        for prior in (False, True):
            with self.subTest(prior=prior):
                slot, check = self.authority()
                sent, stop = [], [False]
                def token():
                    if stop[0]: self.terminalize(slot, 'stopped')
                    return 'synthetic-token'
                def capture(url, headers, body):
                    sent.append(url.rsplit('/', 1)[1])
                    return {'acked': True}
                inner = inbox.Guarded(self.real(token, capture), check)
                outer = inbox.Guarded(inner, lambda: None)
                if prior: outer.call('session_reply', {'message_id': fixture.M1})
                stop[0] = True
                with self.assertRaises(inbox.NativeInboxError) as raised:
                    outer.call('session_ack', {'message_id': fixture.M1})
                effect = {'state': 'completed', 'operation': 'session_reply'} if prior else None
                self.assertEqual(sent, ['session_reply'] if prior else [])
                self.assertEqual((inner.effect(), outer.effect(), raised.exception.remote_effect), (effect,) * 3)

    @isolated_profiles
    def test_real_postdispatch_completion_and_loss_keep_truthful_effects(self):
        for lost in (False, True):
            for reason in ('stopped', 'permission_expired'):
                with self.subTest(lost=lost, reason=reason):
                    slot, check = self.authority()
                    sent = []
                    def capture(url, headers, body):
                        sent.append((url, json.loads(body)))
                        self.terminalize(slot, reason)
                        if lost: raise QuorumError('service_unavailable')
                        return {'reply_message_id': fixture.M2}
                    api = inbox.Guarded(self.real(lambda: 'synthetic-token', capture), check)
                    if lost:
                        with self.assertRaisesRegex(QuorumError, 'service_unavailable'):
                            api.call('session_reply', {'message_id': fixture.M1}, 'stable-reply')
                        with self.assertRaises(inbox.NativeInboxError) as raised: api.check()
                    else:
                        with self.assertRaises(inbox.NativeInboxError) as raised:
                            api.call('session_reply', {'message_id': fixture.M1}, 'stable-reply')
                    effect = {'state': 'uncertain' if lost else 'completed', 'operation': 'session_reply'}
                    self.assertEqual((api.effect(), raised.exception.remote_effect), (effect, effect))
                    self.assertEqual(sent, [(fixture.ORIGIN + '/rest/v1/rpc/session_reply',
                                             {'p_message_id': fixture.M1, 'p_request_id': 'stable-reply'})])

    @isolated_profiles
    def test_create_token_refusal_restores_only_unsent_intent(self):
        for prior in (False, True):
            for reason in ('stopped', 'permission_expired'):
                with self.subTest(prior=prior, reason=reason):
                    self.now = fixture.T0
                    slot, cap = self.active(handle=str(fixture.uuid.uuid4()))
                    self.configure()
                    sent, stop = [], [not prior]
                    def token():
                        if stop[0]: self.terminalize(slot, reason)
                        return 'synthetic-token'
                    def capture(url, headers, body):
                        sent.append((url, json.loads(body)))
                        if prior: raise QuorumError('service_unavailable')
                        return {'quorum_id': fixture.QUORUM, 'slug': 'team'}
                    api = self.real(token, capture)
                    if prior:
                        code, result, _ = fixture.CreateTests.create(self, api, cap)
                        self.assertEqual((code, result['code']), (5, 'service_unavailable'))
                        stop[0] = True
                    code, result, text = fixture.CreateTests.create(self, api, cap)
                    self.assertEqual((code, result['code'], result['data']['reason']),
                                     (3, 'native_worker_slot_terminal', reason), text)
                    self.assertEqual(len(sent), int(prior))
                    self.assertNotIn('remote_effect', result['data'])
                    with fixture.claude(): status = worker.slot_status(self.root, slot)
                    self.assertEqual((status['create'], status['target']), ('ambiguous' if prior else None, {}))

    @isolated_profiles
    def test_accept_token_refusal_does_not_consume_invitation(self):
        for reason in ('stopped', 'permission_expired'):
            with self.subTest(reason=reason):
                self.now = fixture.T0
                slot, cap = self.active(self.accept_scope(), handle=reason)
                self.configure()
                sent = []
                def token():
                    self.terminalize(slot, reason)
                    return 'synthetic-token'
                def capture(url, headers, body):
                    sent.append((url, json.loads(body)))
                    return {'quorum_id': fixture.QUORUM, 'membership_id': 'membership-1'}
                code, result, text = self.cli(self.real(token, capture), 'accept-id', fixture.INVITE,
                                              '--transport', 'tool-inbox', '--worker-capability', str(cap))
                self.assertEqual((code, result['code'], result['data']['reason']),
                                 (3, 'native_worker_slot_terminal', reason), text)
                self.assertEqual(sent, [])
                self.assertNotIn('remote_effect', result['data'])
                self.assertEqual(self.setting(slot)['target'], {})

    @isolated_profiles
    def test_direct_create_and_accept_preserve_real_postdispatch_effects(self):
        for mode in ('create', 'accept'):
            for lost in (False, True):
                with self.subTest(mode=mode, lost=lost):
                    self.now = fixture.T0
                    slot, cap = self.active(self.scope() if mode == 'create' else self.accept_scope(),
                                            handle=str(fixture.uuid.uuid4()))
                    self.configure()
                    sent = []
                    def capture(url, headers, body):
                        sent.append((url, json.loads(body)))
                        self.terminalize(slot, 'stopped')
                        if lost: raise QuorumError('service_unavailable')
                        return {'quorum_id': fixture.QUORUM, 'membership_id': 'membership-1', 'slug': 'team'}
                    api = self.real(lambda: 'synthetic-token', capture)
                    if mode == 'create': code, result, text = fixture.CreateTests.create(self, api, cap)
                    else:
                        code, result, text = self.cli(api, 'accept-id', fixture.INVITE, '--transport', 'tool-inbox',
                                                      '--worker-capability', str(cap))
                    operation = 'quorum_create' if mode == 'create' else 'invite_accept_id'
                    self.assertEqual(code, 3, text)
                    self.assertEqual(len(sent), 1)
                    effect = {'state': 'uncertain' if lost else 'completed' if mode == 'create' else 'accepted',
                              'operation': operation}
                    if not lost:
                        effect['quorum_id'] = fixture.QUORUM
                        if mode == 'accept': effect['membership_id'] = 'membership-1'
                    self.assertEqual(result['data']['remote_effect'], effect)
                    self.assertEqual(self.setting(slot)['target'], {} if lost else
                                     {'quorum_id': fixture.QUORUM} if mode == 'create' else
                                     {'quorum_id': fixture.QUORUM, 'membership_id': 'membership-1'})

    @isolated_profiles
    def test_connect_refuses_new_registration_when_token_returns_after_terminal(self):
        for reason in ('stopped', 'permission_expired'):
            with self.subTest(reason=reason):
                self.now = fixture.T0
                fake = fixture.Api()
                self.bound(fake, connect=False, handle=reason)
                sent = []
                def token():
                    if sent and sent[-1] == 'quorum_lookup': self.terminalize(self.slot, reason)
                    return 'synthetic-token'
                def capture(url, headers, body):
                    operation = url.rsplit('/', 1)[1]
                    sent.append(operation)
                    data = {key[2:]: value for key, value in json.loads(body).items()}
                    return fake.answer(operation, data)
                code, result, text = self.connect(self.real(token, capture), self.cap)
                self.assertEqual((code, result['code'], result['data']['reason']),
                                 (3, 'native_worker_slot_terminal', reason), text)
                self.assertNotIn('device_register', sent)
                self.assertNotIn('agent_join', sent)
                self.assertEqual(result['data']['remote_effect'], {'state': 'completed', 'operation': 'quorum_lookup'})


class ApiGuardContractTests(unittest.TestCase):
    def test_opt_in_guard_keeps_legacy_encoding_token_and_request_callbacks(self):
        events, sent = [], []
        def token():
            events.append('token')
            return 'synthetic-token'
        def request(url, headers, body):
            events.append('request')
            sent.append((url, headers, json.loads(body)))
            return {'acked': True}
        api = QuorumApi(ServiceConfig(fixture.ORIGIN, fixture.KEY), token, request=request)
        self.assertTrue(callable(getattr(api, 'call_guarded', None)), 'missing opt-in transport guard')
        self.assertEqual(api.call('session_reply', {'body': 'ü'}, 'stable-id'), {'acked': True})
        self.assertEqual(api.call_guarded('session_reply', {'body': 'ü'}, 'stable-id',
                                          before_request=lambda: events.append('guard'),
                                          on_dispatch=lambda: events.append('dispatch')), {'acked': True})
        self.assertEqual(events, ['token', 'request', 'token', 'guard', 'dispatch', 'request'])
        self.assertEqual(sent, [(fixture.ORIGIN + '/rest/v1/rpc/session_reply',
                                 {'Authorization': 'Bearer synthetic-token', 'apikey': fixture.KEY,
                                  'Content-Type': 'application/json'},
                                 {'p_body': 'ü', 'p_request_id': 'stable-id'})] * 2)
        self.assertIs(api.token, token)
        self.assertIs(api.request, request)


if __name__ == '__main__': unittest.main()
