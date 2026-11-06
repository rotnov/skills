"""#127 worker receive-loop contract and #130 re-accept versus resume; fake API and local fixtures only."""
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import patch
from quorum_test_support import isolated_profiles

SCRIPTS = Path(__file__).resolve().parents[1] / 'skills/quorum/scripts'
SKILL = SCRIPTS.parent
sys.path.insert(0, str(SCRIPTS))

import test_quorum_native_inbox as codex  # noqa: E402
import test_quorum_claude_worker as claude_tests  # noqa: E402

STOP_CODES = ('brief_deadline', 'brief_count', 'owner_stop', 'fatal:<code>')
NEW_WORKER = '01a167b0-8caa-7533-ad39-321b0d35f98b'


def forbidden(name):
    return AssertionError('must not run: ' + name)


class LoopContractTextTests(unittest.TestCase):
    """The brief, the envelope notice and the references state one receive-loop contract."""

    def test_contract_names_empty_waits_limits_and_the_four_stop_codes(self):
        from quorum_native_inbox import RECEIVE_LOOP, STOP_REASONS
        self.assertEqual(STOP_REASONS, STOP_CODES)
        self.assertIn('through empty waits', RECEIVE_LOOP)
        for code in STOP_CODES: self.assertIn(code, RECEIVE_LOOP)
        for text in ('deadline', 'message count', 'owner stop', 'fatal error'): self.assertIn(text, RECEIVE_LOOP)

    def test_references_carry_the_contract_for_both_workers(self):
        for name in ('native-inbox.md', 'claude-worker.md', 'service-agent.md'):
            text = (SKILL / 'references' / name).read_text()
            with self.subTest(reference=name):
                self.assertIn('empty wait', text)
                for code in STOP_CODES: self.assertIn('`' + code + '`', text)

    def test_capability_alternative_keeps_receiving_through_empty_waits(self):
        from quorum_capabilities import ALTERNATIVES
        summary = ALTERNATIVES[0]['summary']
        self.assertIn('empty waits', summary)
        self.assertIn('stop reason', summary)


@codex.OWNER_PROOF
class CodexReceiveFieldTests(codex.Harness):
    """Every receive result tells a model-driven worker whether and how to continue."""

    def setUp(self):
        super().setUp()
        codex.rollout(self.home)

    @isolated_profiles
    def test_empty_wait_offer_and_stop_carry_continue_and_next_step(self):
        api = codex.Api()
        with codex.caller(self.home):
            self.connect(api)
            for _ in range(5):  # Many empty waits in a row are never a reason to stop.
                code, result, text = self.receive(api)
                self.assertEqual(code, 0, text)
                data = result['data']
                self.assertEqual((data['state'], data['continue'], data['next_step']), ('timeout', True, 'receive'))
                self.assertEqual(data['receive_command'][-6:],
                                 ['session', '--session-id', codex.CHILD, 'receive', '--wait-seconds', '0'])
                self.assertNotIn('stop_reason', data)
            api.inbox.append(codex.message(codex.M1))
            code, result, text = self.receive(api)
            data = result['data']
            self.assertEqual((data['state'], data['continue'], data['next_step']),
                             ('offered', True, 'handle_then_receive'), text)
            self.assertIn('through empty waits', data['notice'])
            for code_name in STOP_CODES: self.assertIn(code_name, data['notice'])
            code, result, text = self.cli(api, 'session', 'stop')
            self.assertEqual(code, 0, text)
            code, result, text = self.receive(api)
        data = result['data']
        self.assertEqual((data['state'], data['continue'], data['next_step'], data['stop_reason']),
                         ('stopped', False, 'report_stop', 'owner_stop'), text)
        self.assertNotIn('receive_command', data)


@claude_tests.OWNER_PROOF
class ClaudeReceiveFieldTests(claude_tests.Bound):

    @isolated_profiles
    def test_prepare_brief_and_capability_receive_carry_the_loop_contract(self):
        prepared = self.prepare(self.accept_scope())
        for code in STOP_CODES: self.assertIn(code, prepared['worker_brief'])
        self.assertIn('bootstrap_command', prepared['worker_brief'])
        api = claude_tests.OwnerApi()
        self.bound(api, self.accept_scope(quorum_slug='team'), handle='agent_09 quorum')
        data = self.offer(api)
        self.assertEqual((data['state'], data['continue'], data['next_step']), ('timeout', True, 'receive'))
        self.assertEqual(data['receive_command'][-6:], ['session', '--worker-capability', str(self.cap), 'receive',
                                                        '--wait-seconds', '0'])
        self.assertNotIn('token', json.dumps(data))


@codex.OWNER_PROOF
class CodexResumeTests(codex.Harness):
    """#130: a chat whose worker binding is live in this quorum resumes it; the invitation stays unused."""

    def setUp(self):
        super().setUp()
        codex.rollout(self.home)
        codex.rollout(self.home, sid=NEW_WORKER)

    def accept(self, api, quorum, sid=NEW_WORKER):
        never = [patch(target, side_effect=forbidden(target)) for target in (
            'quorum_auth.login_pkce', 'quorum_profiles.ensure_login', 'quorum_api.request_json')]
        for guard in never: guard.start()
        try:
            with codex.caller(self.home, sid=sid), \
                    patch('quorum_profiles.resolve_profile', side_effect=ValueError('machine_profile_unavailable')):
                return self.cli(api, 'accept-id', codex.INVITE, '--quorum', quorum, '--transport', 'tool-inbox',
                                '--parent-thread-id', codex.PARENT, '--require-worker-inbox')
        finally:
            for guard in never: guard.stop()

    @isolated_profiles
    def test_restarted_worker_is_told_to_rebind_without_using_the_invitation(self):
        api = codex.Api()
        with codex.caller(self.home): self.connect(api)
        mark = len(api.calls)
        code, result, text = self.accept(api, 'team')
        self.assertEqual((code, result['code']), (3, 'binding_exists_resume'), text)
        data = result['data']
        self.assertEqual((data['quorum'], data['route'], data['session_ref'], data['invitation']),
                         ('team', 'codex-worker', codex.CHILD, 'unused'))
        self.assertEqual(data['next_step'][-6:], ['session', '--session-id', codex.CHILD, 'recover',
                                                  '--parent-thread-id', codex.PARENT])
        self.assertIn('not used', data['guidance'])
        self.assertEqual(api.calls[mark:], [], 'no service call, so the invitation is not consumed')
        # Another quorum is an ordinary acceptance: the check passes and acceptance proceeds.
        code, result, text = self.accept(api, 'other-team')
        self.assertEqual(result['code'], 'machine_profile_unavailable', text)

    @isolated_profiles
    def test_a_stopped_binding_does_not_block_acceptance(self):
        api = codex.Api()
        with codex.caller(self.home):
            self.connect(api)
            self.assertEqual(self.cli(api, 'session', 'stop')[0], 0)
        code, result, text = self.accept(api, 'team')
        self.assertEqual(result['code'], 'machine_profile_unavailable', text)

    @isolated_profiles
    def test_an_invalid_quorum_slug_is_refused_before_anything(self):
        code, result, text = self.accept(codex.Api(), "team';rm")
        self.assertEqual((code, result['code']), (3, 'invalid_quorum'), text)


@codex.OWNER_PROOF
class ParentResumeTests(codex.Harness):
    """A parent-route chat with enabled reception in the quorum is told to run `session recover`."""

    def bind_parent(self, enabled=True):
        from quorum_resume import binding
        from quorum_state import LocalLedger
        record = {'engine': 'codex', 'session_ref': codex.PARENT, 'agent_id': codex.AGENT, 'device_id': 'device-1',
                  'quorum_id': codex.QUORUM, 'project_ref': 'team/project', 'cwd': '/synthetic', 'role': 'lead',
                  'quorum': 'team', 'transport': 'native'}
        ledger = LocalLedger(Path(self.root) / 'sessions' / codex.PARENT)
        try:
            ledger.setting('session-agent', record)
            ledger.setting('session-reception', {'enabled': enabled, 'binding': binding(record), 'expires_at': None})
        finally: ledger.db.close()

    def accept(self, quorum):
        with codex.caller(self.home, sid=codex.PARENT), \
                patch('quorum_profiles.resolve_profile', side_effect=ValueError('machine_profile_unavailable')), \
                patch('quorum_session.client_preflight', side_effect=forbidden('client_preflight')):
            return self.cli(None, 'accept-id', codex.INVITE, '--quorum', quorum)

    @isolated_profiles
    def test_parent_binding_resumes_with_session_recover(self):
        self.bind_parent()
        code, result, text = self.accept('team')
        self.assertEqual((code, result['code']), (3, 'binding_exists_resume'), text)
        data = result['data']
        self.assertEqual((data['route'], data['role'], data['agent_id'], data['invitation']),
                         ('parent', 'lead', codex.AGENT, 'unused'))
        self.assertEqual(data['next_step'][-2:], ['session', 'recover'])
        self.assertEqual(self.accept('other-team')[1]['code'], 'machine_profile_unavailable')

    @isolated_profiles
    def test_disabled_parent_reception_is_not_live(self):
        self.bind_parent(enabled=False)
        self.assertEqual(self.accept('team')[1]['code'], 'machine_profile_unavailable')


@claude_tests.OWNER_PROOF
class ClaudeResumeTests(claude_tests.Bound):
    """A Claude chat with a live worker slot in the quorum rebinds instead of preparing a new accept."""

    @isolated_profiles
    def test_accept_prepare_refuses_with_the_rebind_step_until_the_slot_is_stopped(self):
        import quorum_claude_worker as worker
        from quorum_resume import BindingExists
        api = claude_tests.OwnerApi()
        self.bound(api)
        with self.assertRaises(BindingExists) as raised:
            self.prepare(self.accept_scope())
        data = raised.exception.diagnostic()
        self.assertEqual((data['route'], data['slot_id'], data['agent_id'], data['invitation']),
                         ('claude-worker', self.slot, claude_tests.AGENT, 'unused'))
        self.assertEqual(data['next_step'][-4:], ['session', 'worker', 'rebind', self.slot])
        # The CLI answer is the same fixed code and never a token.
        code, result, text = self.cli(None, 'session', 'worker', 'prepare', '--json-stdin',
                                      stdin=json.dumps(self.accept_scope()).encode())
        self.assertEqual((code, result['code'], result['data']['slot_id']), (3, 'binding_exists_resume', self.slot))
        self.assertNotIn(self.token, text)
        # Another quorum, a create scope or a stopped slot prepare as before.
        self.assertEqual(self.prepare(self.accept_scope(quorum_slug='other-team'))['state'], 'pending')
        with claude_tests.claude(): worker.stop_slot(self.root, self.slot)
        self.assertEqual(self.prepare(self.accept_scope())['state'], 'pending')

    @isolated_profiles
    def test_worker_accept_quorum_flag_must_equal_the_scope(self):
        slot, cap = self.active(self.accept_scope())
        self.configure()
        code, result, text = self.cli(claude_tests.OwnerApi(), 'accept-id', claude_tests.INVITE, '--quorum', 'other',
                                      '--transport', 'tool-inbox', '--worker-capability', str(cap))
        self.assertEqual((code, result['code'], result['data'].get('reason')),
                         (3, 'native_worker_binding_denied', 'scope_mismatch'), text)
        code, result, text = self.cli(claude_tests.OwnerApi(), 'accept-id', claude_tests.INVITE, '--quorum', 'team',
                                      '--transport', 'tool-inbox', '--worker-capability', str(cap))
        self.assertEqual(code, 0, text)


class InvitationRuleTests(unittest.TestCase):

    def test_short_prompt_passes_the_quorum_and_states_the_resume_rule(self):
        from quorum_invitations import RESUME_RULE
        for text in ('live binding in this quorum', 'resume it', 'unused', 'otherwise accept'):
            self.assertIn(text, RESUME_RULE)
        source = (SCRIPTS / 'quorum_invitations.py').read_text()
        self.assertEqual(source.count('--quorum {slug}'), 3)
        self.assertEqual(source.count('+ RESUME_RULE'), 3)

    def test_join_guide_states_one_decision_rule(self):
        text = (SKILL / 'references' / 'join.md').read_text()
        self.assertIn('## Resume or accept', text)
        self.assertIn('binding_exists_resume', text)
        self.assertIn('session worker rebind', text)
        self.assertIn('session recover', text)


if __name__ == '__main__':
    unittest.main()
